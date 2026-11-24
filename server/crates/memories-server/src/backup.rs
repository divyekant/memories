//! Backup, restore, snapshot, export and import routes of `app.py` v5.19.0
//! (task P5-4). A backup is a folder in `DATA_DIR/backups` with the Qdrant
//! snapshot of the collection, an NDJSON export and a copy of each SQLite
//! file. See `docs/rust-rewrite/backup-export.md`.

// Each handler returns the axum `Response` as its error; a box gives no gain here.
#![allow(clippy::result_large_err)]

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use axum::{
    Extension, Json,
    body::Bytes,
    extract::{Path as UrlPath, Query, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use memories_engine::policy::validate_project_write;
use memories_engine::rank::PyDateTime;
use memories_engine::records::truthy;
use memories_engine::writes::{NewMemory, WriteError, Writer, source_of};
use memories_types::Record;
use serde_json::{Map, Value, json};

use crate::{
    AppState,
    audit::Entry,
    auth::AuthContext,
    error::ApiError,
    shell::Client,
    validate::{Fields, finish},
    writes::trusted,
};

/// The file names in a backup folder.
pub const SNAPSHOT_FILE: &str = "memories_v2.snapshot";
pub const EXPORT_FILE: &str = "export.ndjson";
/// The prefix of the daily backup, and how many of them stay.
pub const SCHEDULED: &str = "scheduled";
const SCHEDULED_KEEP: usize = 7;
const DAY: Duration = Duration::from_secs(24 * 3600);

const STANDARD_FIELDS: [&str; 6] = [
    "id",
    "text",
    "source",
    "created_at",
    "updated_at",
    "timestamp",
];

#[derive(Debug, thiserror::Error)]
pub enum BackupError {
    #[error(transparent)]
    Write(#[from] WriteError),
    #[error("backup file: {0}")]
    Io(#[from] std::io::Error),
    #[error("sqlite copy: {0}")]
    Sqlite(#[from] rusqlite::Error),
}

impl From<memories_store::StoreError> for BackupError {
    fn from(e: memories_store::StoreError) -> Self {
        Self::Write(e.into())
    }
}

impl From<memories_engine::EngineError> for BackupError {
    fn from(e: memories_engine::EngineError) -> Self {
        Self::Write(e.into())
    }
}

fn err(status: StatusCode, detail: impl Into<Value>) -> Response {
    ApiError::new(status, detail).into_response()
}

fn server_error(e: impl std::fmt::Display) -> Response {
    tracing::error!(error = %e, "backup route failed");
    err(StatusCode::INTERNAL_SERVER_ERROR, "Internal server error")
}

fn writer(state: &AppState) -> Result<Arc<Writer>, Response> {
    state.writer.get().cloned().ok_or_else(|| {
        err(
            StatusCode::SERVICE_UNAVAILABLE,
            "The memory store is not configured: set QDRANT_URL",
        )
    })
}

fn admin(auth: &AuthContext) -> Result<(), Response> {
    auth.require_admin().map_err(IntoResponse::into_response)
}

/// The last value of each query parameter, as Starlette reads a scalar.
fn query_map(q: &[(String, String)], names: &[&str]) -> Map<String, Value> {
    let mut m = Map::new();
    for n in names {
        if let Some((_, v)) = q.iter().rev().find(|(k, _)| k == n) {
            m.insert((*n).into(), json!(v));
        }
    }
    m
}

// ---- Python JSON ------------------------------------------------------------------

/// `json.dumps` with `ensure_ascii=True` and compact separators: each
/// character above U+007F becomes `\uXXXX` (a surrogate pair above U+FFFF).
pub fn py_dumps(v: &Value) -> String {
    let s = serde_json::to_string(v).expect("JSON values serialize");
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            let mut units = [0u16; 2];
            for u in c.encode_utf16(&mut units) {
                out.push_str(&format!("\\u{u:04x}"));
            }
        }
    }
    out
}

// ---- Export -----------------------------------------------------------------------

/// The filters of `export_memories`. An empty string is no filter (Python
/// `if source_prefix:`).
#[derive(Clone, Debug, Default)]
pub struct ExportFilter {
    pub source: Option<String>,
    pub since: Option<String>,
    pub until: Option<String>,
}

fn str_field(r: &Record, key: &str) -> String {
    r.field(key)
        .and_then(|v| v.as_str().map(str::to_owned))
        .unwrap_or_default()
}

impl ExportFilter {
    fn keeps(&self, r: &Record) -> bool {
        let set = |o: &Option<String>| o.clone().filter(|s| !s.is_empty());
        if let Some(p) = set(&self.source)
            && !source_of(r).starts_with(&p)
        {
            return false;
        }
        let created = str_field(r, "created_at");
        if let Some(s) = set(&self.since)
            && created < s
        {
            return false;
        }
        if let Some(u) = set(&self.until)
            && created > u
        {
            return false;
        }
        true
    }
}

/// The header line. The keys are in the Python order.
pub fn header_line(f: &ExportFilter, count: usize, exported_at: &str) -> String {
    let opt = |o: &Option<String>| py_dumps(&o.clone().map_or(Value::Null, Value::String));
    format!(
        "{{\"_header\":true,\"exported_at\":{},\"source_filter\":{},\"since\":{},\"until\":{},\"count\":{count},\"version\":\"4.0.0\"}}",
        py_dumps(&json!(exported_at)),
        opt(&f.source),
        opt(&f.since),
        opt(&f.until),
    )
}

/// One record line: no ID, no vectors and no `timestamp`.
pub fn record_line(r: &Record) -> String {
    let mut custom = r.to_payload();
    for k in STANDARD_FIELDS {
        custom.remove(k);
    }
    format!(
        "{{\"text\":{},\"source\":{},\"created_at\":{},\"updated_at\":{},\"custom_fields\":{}}}",
        py_dumps(&json!(str_field(r, "text"))),
        py_dumps(&json!(source_of(r))),
        py_dumps(&json!(str_field(r, "created_at"))),
        py_dumps(&json!(str_field(r, "updated_at"))),
        py_dumps(&Value::Object(custom)),
    )
}

/// The export of `export_memories`: the header, then each record that the
/// filter keeps (in ID order), with its source for the key check.
// shortcut: the lines stay in memory, as in Python; stream them if exports reach many GB.
pub async fn export_lines(
    w: &Writer,
    f: &ExportFilter,
) -> Result<(String, Vec<(String, String)>), BackupError> {
    let mut lines = Vec::new();
    let mut offset = None;
    loop {
        let (records, next) = w.engine.store.scroll(None, offset, 1_000).await?;
        for mut r in records {
            w.engine.overlay(&mut r);
            if f.keeps(&r) {
                lines.push((source_of(&r), record_line(&r)));
            }
        }
        match next {
            Some(n) => offset = Some(n),
            None => break,
        }
    }
    let header = header_line(f, lines.len(), &PyDateTime::now_utc().isoformat_utc());
    Ok((header, lines))
}

pub async fn export(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Response, Response> {
    let m = query_map(&q, &["source", "since", "until"]);
    let mut errors = Vec::new();
    let mut fields = Fields::new(&m, vec![json!("query")], &mut errors);
    let f = ExportFilter {
        source: fields.string("source", false, (0, 500), None),
        since: fields.string("since", false, (0, usize::MAX), None),
        until: fields.string("until", false, (0, usize::MAX), None),
    };
    finish(errors, Some(())).map_err(IntoResponse::into_response)?;
    let w = writer(&state)?;
    let (header, lines) = export_lines(&w, &f).await.map_err(server_error)?;
    let mut body = header;
    body.push('\n');
    for (source, line) in lines {
        if auth.can_read(&source) {
            body.push_str(&line);
            body.push('\n');
        }
    }
    Ok(([(header::CONTENT_TYPE, "application/x-ndjson")], body).into_response())
}

// ---- Backup folders -------------------------------------------------------------------

pub fn backup_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("backups")
}

/// `re.sub(r"[^a-zA-Z0-9_-]", "_", prefix)`.
pub fn sanitize_prefix(prefix: &str) -> String {
    prefix
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// `<prefix>_<YYYYMMDD>_<HHMMSS>` in UTC.
pub fn backup_name(prefix: &str, now: &PyDateTime) -> String {
    let [y, mo, d, h, mi, s, _] = now.fields();
    format!(
        "{}_{y:04}{mo:02}{d:02}_{h:02}{mi:02}{s:02}",
        sanitize_prefix(prefix)
    )
}

/// The prefix class of a backup name (`re.sub(r"_\d{8}_\d{6}$", "", name)`).
pub fn prefix_of(name: &str) -> &str {
    let b = name.as_bytes();
    let n = b.len();
    let digits = |r: std::ops::Range<usize>| b[r].iter().all(u8::is_ascii_digit);
    if n >= 16 && b[n - 16] == b'_' && b[n - 7] == b'_' && digits(n - 15..n - 7) && digits(n - 6..n)
    {
        &name[..n - 16]
    } else {
        name
    }
}

/// The names that stay: the `keep` newest, the 2 newest of each prefix, and
/// the 7 newest scheduled backups. `entries` is (name, modified time).
pub fn survivors(mut entries: Vec<(String, SystemTime)>, keep: usize) -> HashSet<String> {
    entries.sort_by_key(|e| std::cmp::Reverse(e.1));
    let mut out: HashSet<String> = entries.iter().take(keep).map(|e| e.0.clone()).collect();
    let mut per_prefix: HashMap<&str, usize> = HashMap::new();
    for (name, _) in &entries {
        let p = prefix_of(name);
        let n = per_prefix.entry(p).or_default();
        let limit = if p == SCHEDULED { SCHEDULED_KEEP } else { 2 };
        if *n < limit {
            out.insert(name.clone());
            *n += 1;
        }
    }
    out
}

/// The entries of the backups folder that match the Python glob `*_*`.
fn list_entries(dir: &Path) -> std::io::Result<Vec<(String, std::fs::Metadata)>> {
    let rd = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    let mut out = Vec::new();
    for entry in rd {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with('.') || !name.contains('_') {
            continue;
        }
        out.push((name, entry.metadata()?));
    }
    Ok(out)
}

/// `_cleanup_old_backups`: delete each backup folder that is not a survivor.
pub fn cleanup(dir: &Path, keep: usize) -> std::io::Result<()> {
    let entries = list_entries(dir)?;
    let stamped = entries
        .iter()
        .map(|(n, m)| (n.clone(), m.modified().unwrap_or(SystemTime::UNIX_EPOCH)))
        .collect();
    let stay = survivors(stamped, keep);
    for (name, meta) in entries {
        if meta.is_dir() && !stay.contains(&name) {
            let _ = std::fs::remove_dir_all(dir.join(name));
        }
    }
    Ok(())
}

/// Copy each `*.db` file of `data_dir` into `dest` with `VACUUM INTO`.
fn copy_sqlite(data_dir: &Path, dest: &Path) -> Result<(), BackupError> {
    for entry in std::fs::read_dir(data_dir)? {
        let path = entry?.path();
        if !path.is_file() || path.extension().is_none_or(|e| e != "db") {
            continue;
        }
        let target = dest.join(path.file_name().expect("a file has a name"));
        let _ = std::fs::remove_file(&target);
        let conn = rusqlite::Connection::open_with_flags(
            &path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        conn.execute("VACUUM INTO ?1", [target.to_string_lossy()])?;
    }
    Ok(())
}

/// Make a backup folder, then delete the old folders (`cleanup`). The caller
/// holds the write lock if it needs a backup that agrees with a later step.
pub async fn create_backup(
    w: &Writer,
    data_dir: &Path,
    max_backups: usize,
    prefix: &str,
) -> Result<PathBuf, BackupError> {
    let path = make_backup(w, data_dir, prefix).await?;
    run_cleanup(data_dir, max_backups).await?;
    Ok(path)
}

async fn run_cleanup(data_dir: &Path, max_backups: usize) -> Result<(), BackupError> {
    let root = backup_dir(data_dir);
    tokio::task::spawn_blocking(move || cleanup(&root, max_backups))
        .await
        .map_err(std::io::Error::other)??;
    Ok(())
}

/// A new, empty folder `<prefix>_<time>`. If a backup of the same second has
/// the name, wait for the next second, so two backups never share a folder.
async fn new_folder(root: &Path, prefix: &str) -> Result<PathBuf, BackupError> {
    std::fs::create_dir_all(root)?;
    for _ in 0..5 {
        let path = root.join(backup_name(prefix, &PyDateTime::now_utc()));
        match std::fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            Err(e) => return Err(e.into()),
        }
    }
    Err(std::io::Error::other("no free backup name after 5 tries").into())
}

/// Make a backup folder with no cleanup. A failed backup removes its folder.
async fn make_backup(w: &Writer, data_dir: &Path, prefix: &str) -> Result<PathBuf, BackupError> {
    let path = new_folder(&backup_dir(data_dir), prefix).await?;
    let made = fill_backup(w, data_dir, &path).await;
    if let Err(e) = made {
        let p = path.clone();
        let _ = tokio::task::spawn_blocking(move || std::fs::remove_dir_all(p)).await;
        return Err(e);
    }
    Ok(path)
}

async fn fill_backup(w: &Writer, data_dir: &Path, path: &Path) -> Result<(), BackupError> {
    let store = &w.engine.store;
    let name = store.create_snapshot().await?;
    let part = path.join(format!("{SNAPSHOT_FILE}.part"));
    let got = store.download_snapshot(&name, &part).await;
    if let Err(e) = store.delete_snapshot(&name).await {
        tracing::warn!(error = %e, snapshot = %name, "cannot delete the Qdrant snapshot of a backup");
    }
    got?;
    tokio::fs::rename(&part, path.join(SNAPSHOT_FILE)).await?;
    let (header, lines) = export_lines(w, &ExportFilter::default()).await?;
    let (d, p) = (data_dir.to_owned(), path.to_owned());
    tokio::task::spawn_blocking(move || {
        use std::io::Write;
        let mut f = std::io::BufWriter::new(std::fs::File::create(p.join(EXPORT_FILE))?);
        writeln!(f, "{header}")?;
        for (_, line) in lines {
            writeln!(f, "{line}")?;
        }
        f.into_inner().map_err(|e| e.into_error())?.sync_all()?;
        copy_sqlite(&d, &p)
    })
    .await
    .map_err(std::io::Error::other)??;
    Ok(())
}

/// Replace the collection with a snapshot file. The caller holds the write
/// lock. Makes a `pre_restore` backup first, and deletes old folders only
/// after the upload, so the cleanup cannot remove the source. Returns the
/// record count.
async fn restore_locked(
    w: &Writer,
    data_dir: &Path,
    max_backups: usize,
    file: &Path,
) -> Result<usize, BackupError> {
    w.engine.flush_reinforcements().await?;
    make_backup(w, data_dir, "pre_restore").await?;
    let uploaded = w.engine.store.upload_snapshot(file).await;
    // A failed upload can still have changed the collection: load it again.
    let n = w.reload().await?;
    uploaded?;
    run_cleanup(data_dir, max_backups).await?;
    Ok(n)
}

// ---- Backup routes ------------------------------------------------------------------

pub async fn list_backups(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Json<Value>, Response> {
    use std::os::unix::fs::MetadataExt;
    admin(&auth)?;
    let mut entries = list_entries(&backup_dir(&state.data_dir)).map_err(server_error)?;
    entries.sort_by(|a, b| b.0.cmp(&a.0));
    let backups: Vec<Value> = entries
        .iter()
        .map(
            |(n, m)| json!({"name": n, "created": m.ctime() as f64 + m.ctime_nsec() as f64 * 1e-9}),
        )
        .collect();
    Ok(Json(json!({"backups": backups, "count": backups.len()})))
}

pub async fn create(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    Query(q): Query<Vec<(String, String)>>,
) -> Result<Json<Value>, Response> {
    let m = query_map(&q, &["prefix"]);
    let mut errors = Vec::new();
    let prefix =
        Fields::new(&m, vec![json!("query")], &mut errors).string("prefix", false, (0, 50), None);
    finish(errors, Some(())).map_err(IntoResponse::into_response)?;
    admin(&auth)?;
    let w = writer(&state)?;
    let prefix = prefix.unwrap_or_else(|| "manual".into());
    let path = create_backup(&w, &state.data_dir, state.max_backups, &prefix)
        .await
        .map_err(server_error)?;
    Ok(Json(json!({
        "success": true,
        "backup_path": path.display().to_string(),
        "message": "Backup created successfully",
    })))
}

pub async fn restore(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    crate::error::JsonBody(map): crate::error::JsonBody,
) -> Result<Json<Value>, Response> {
    let mut errors = Vec::new();
    let name = Fields::new(&map, vec![json!("body")], &mut errors).string(
        "backup_name",
        true,
        (1, 200),
        None,
    );
    let name = finish(errors, name).map_err(IntoResponse::into_response)?;
    admin(&auth)?;
    if name.contains("..") || name.contains('/') || name.contains('\\') {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!("Invalid backup name: {name}"),
        ));
    }
    let root = backup_dir(&state.data_dir);
    let path = root.join(&name);
    if !path.exists() {
        return Err(err(
            StatusCode::NOT_FOUND,
            format!("Backup '{name}' not found"),
        ));
    }
    let inside = match (path.canonicalize(), root.canonicalize()) {
        (Ok(p), Ok(r)) => p.starts_with(r),
        _ => false,
    };
    if !inside {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!("Invalid backup path: {name}"),
        ));
    }
    let file = path.join(SNAPSHOT_FILE);
    if !file.is_file() {
        return Err(err(
            StatusCode::NOT_FOUND,
            format!("Backup '{name}' is incomplete"),
        ));
    }
    let w = writer(&state)?;
    let _g = w.lock_writes().await;
    let total = restore_locked(&w, &state.data_dir, state.max_backups, &file)
        .await
        .map_err(server_error)?;
    Ok(Json(json!({
        "success": true,
        "restored_from": name,
        "total_memories": total,
        "message": "Restored successfully",
    })))
}

// ---- Snapshot routes ------------------------------------------------------------------

pub async fn list_snapshots(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
) -> Result<Json<Value>, Response> {
    admin(&auth)?;
    let path = state.data_dir.join("snapshots").join("manifest.json");
    let entries: Vec<Value> = match std::fs::read(&path) {
        Ok(b) => serde_json::from_slice(&b).map_err(server_error)?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
        Err(e) => return Err(server_error(e)),
    };
    Ok(Json(json!({"snapshots": entries, "count": entries.len()})))
}

pub async fn create_snapshot(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    client: Option<Extension<Client>>,
) -> Result<Json<Value>, Response> {
    admin(&auth)?;
    let w = writer(&state)?;
    let name = w.snapshot("manual").await.map_err(server_error)?;
    let mut e = Entry::from_request(&auth, client.as_ref().map(|c| &c.0), "snapshot.created");
    e.resource_id = name.clone();
    state.audit(e).await;
    Ok(Json(json!({"snapshot": name})))
}

pub async fn restore_snapshot(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    UrlPath(name): UrlPath<String>,
) -> Result<Json<Value>, Response> {
    admin(&auth)?;
    let w = writer(&state)?;
    let root = backup_dir(&state.data_dir);
    // Under the lock, so two snapshot restores do not share the file.
    let _g = w.lock_writes().await;
    let file = root.join(".snapshot-restore.part");
    let run = async {
        std::fs::create_dir_all(&root)?;
        w.engine.store.download_snapshot(&name, &file).await?;
        restore_locked(&w, &state.data_dir, state.max_backups, &file).await
    };
    let done = run.await;
    let _ = std::fs::remove_file(&file);
    done.map_err(server_error)?;
    Ok(Json(json!({"success": true, "restored": name})))
}

// ---- Import -----------------------------------------------------------------------

const HEADER_ERROR: &str = "Missing header: first line must contain _header: true";
const SKIP_THRESHOLD: f64 = 0.95;
const NOVEL_THRESHOLD: f64 = 0.80;

/// One record of an import, after the parse.
#[derive(Clone, Debug, PartialEq)]
pub struct ImportRecord {
    pub line: usize,
    pub text: String,
    pub source: String,
    pub created_at: String,
    pub custom: Map<String, Value>,
}

/// A line that Python cannot read without an exception (HTTP 500).
#[derive(Debug, PartialEq)]
pub struct Unreadable;

fn line_error(line: usize, msg: &str) -> Value {
    json!({"line": line, "error": msg})
}

fn remap(source: &str, remap: Option<&(String, String)>) -> String {
    match remap {
        Some((old, new)) if source.starts_with(old.as_str()) => {
            format!("{new}{}", &source[old.len()..])
        }
        _ => source.to_owned(),
    }
}

/// The route step: keep the header, and each line whose (remapped) source
/// the key can write. A line that is not JSON stays for the engine.
pub fn filter_writable<'a>(
    lines: &[&'a str],
    remap_to: Option<&(String, String)>,
    can_write: impl Fn(&str) -> bool,
) -> Result<(Vec<&'a str>, Vec<Value>), Unreadable> {
    let mut kept = vec![lines[0]];
    let mut errors = Vec::new();
    for (i, line) in lines.iter().enumerate().skip(1) {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            kept.push(line);
            continue;
        };
        let source = match v.as_object().ok_or(Unreadable)?.get("source") {
            None => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(_) => return Err(Unreadable),
        };
        if !can_write(&remap(&source, remap_to)) {
            errors.push(line_error(i + 1, "source prefix not authorized"));
            continue;
        }
        kept.push(line);
    }
    Ok((kept, errors))
}

/// The records of an import and the line errors.
pub type Parsed = (Vec<ImportRecord>, Vec<Value>);

/// The engine parse: `None` with the header error, or the records and the
/// line errors.
pub fn parse_import(
    lines: &[&str],
    remap_to: Option<&(String, String)>,
) -> Result<Option<Parsed>, Unreadable> {
    let header = match lines.first().map(|l| serde_json::from_str::<Value>(l)) {
        Some(Ok(v)) => v,
        _ => return Ok(None),
    };
    let header = header.as_object().ok_or(Unreadable)?;
    if !header.get("_header").is_some_and(truthy) {
        return Ok(None);
    }
    let mut records = Vec::new();
    let mut errors = Vec::new();
    for (i, line) in lines.iter().enumerate().skip(1) {
        let n = i + 1;
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            errors.push(line_error(n, "Invalid JSON"));
            continue;
        };
        let m = v.as_object().ok_or(Unreadable)?;
        let (Some(text), Some(source)) = (m.get("text"), m.get("source")) else {
            errors.push(line_error(n, "Missing required field (text or source)"));
            continue;
        };
        let (Some(text), Some(source)) = (text.as_str(), source.as_str()) else {
            return Err(Unreadable);
        };
        let custom = match m.get("custom_fields") {
            Some(Value::Object(c)) => c.clone(),
            _ => Map::new(),
        };
        records.push(ImportRecord {
            line: n,
            text: text.to_owned(),
            source: remap(source, remap_to),
            created_at: m
                .get("created_at")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned(),
            custom,
        });
    }
    Ok(Some((records, errors)))
}

/// `{"imported": True, "import_source": source, **custom_fields}`.
fn import_metadata(r: &ImportRecord) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("imported".into(), json!(true));
    m.insert("import_source".into(), json!(r.source));
    m.extend(r.custom.clone());
    m
}

fn new_memory(r: &ImportRecord) -> NewMemory {
    NewMemory {
        text: r.text.clone(),
        source: r.source.clone(),
        metadata: Some(import_metadata(r)),
    }
}

struct ImportCounts {
    imported: usize,
    skipped: usize,
    updated: usize,
}

/// `_import_smart`: skip a near duplicate, add a novel record, and on a
/// borderline match keep the newer record.
async fn import_smart(
    w: &Writer,
    records: Vec<ImportRecord>,
    trusted: Option<&memories_engine::policy::TrustedAuthorship>,
) -> Result<ImportCounts, WriteError> {
    let mut c = ImportCounts {
        imported: 0,
        skipped: 0,
        updated: 0,
    };
    let mut novel = Vec::new();
    for r in records {
        if w.engine.count() == 0 {
            novel.push(new_memory(&r));
            continue;
        }
        let exact = trusted.map(|_| r.source.as_str());
        let (_, top) = w.is_novel(&r.text, 1.0, exact).await?;
        let hit_source = |t: &Value| {
            t.get("source")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned()
        };
        let top = top.filter(|t| trusted.is_none() || hit_source(t) == r.source);
        let Some(best) = top else {
            novel.push(new_memory(&r));
            continue;
        };
        let cross = trusted.is_none() && hit_source(&best) != r.source;
        let sim = best["similarity"].as_f64().unwrap_or(0.0);
        if sim >= SKIP_THRESHOLD {
            c.skipped += 1;
        } else if sim < NOVEL_THRESHOLD {
            novel.push(new_memory(&r));
        } else {
            let existing = best.get("created_at").and_then(Value::as_str).unwrap_or("");
            if r.created_at.as_str() > existing {
                if let (Some(id), false) = (best["id"].as_i64(), cross) {
                    w.delete_memory(id, false).await?;
                }
                novel.push(new_memory(&r));
                if cross {
                    continue;
                }
                c.updated += 1;
            } else {
                c.skipped += 1;
            }
        }
    }
    if !novel.is_empty() {
        c.imported = novel.len();
        w.add_memories(novel, false, trusted).await?;
    }
    Ok(c)
}

const STRATEGY_ERROR: &str = "Invalid strategy. Must be one of: {'smart+extract', 'add', 'smart'}";

pub async fn import(
    State(state): State<Arc<AppState>>,
    Extension(auth): Extension<AuthContext>,
    headers: axum::http::HeaderMap,
    Query(q): Query<Vec<(String, String)>>,
    body: Bytes,
) -> Result<Json<Value>, Response> {
    let m = query_map(&q, &["strategy", "source_remap", "no_backup"]);
    let mut errors = Vec::new();
    let mut fields = Fields::new(&m, vec![json!("query")], &mut errors);
    let strategy = fields.string("strategy", false, (0, usize::MAX), None);
    let source_remap = fields.string("source_remap", false, (0, 200), None);
    let no_backup = fields.boolean("no_backup", Some(false));
    finish(errors, Some(())).map_err(IntoResponse::into_response)?;
    let strategy = strategy.unwrap_or_else(|| "add".into());
    let no_backup = no_backup.unwrap_or(false);
    let trusted = trusted(&auth, &headers);
    if !matches!(strategy.as_str(), "add" | "smart" | "smart+extract") {
        return Err(err(StatusCode::BAD_REQUEST, STRATEGY_ERROR));
    }
    let text = std::str::from_utf8(&body).map_err(server_error)?;
    let lines: Vec<&str> = text.trim().split('\n').collect();
    let remap_to = source_remap
        .as_deref()
        .and_then(|s| s.split_once('='))
        .map(|(a, b)| (a.to_owned(), b.to_owned()));
    let (lines, auth_errors) = filter_writable(&lines, remap_to.as_ref(), |s| auth.can_write(s))
        .map_err(|_| server_error("an import line is not a JSON object"))?;
    let w = writer(&state)?;
    let mut out = json!({"imported": 0, "skipped": 0, "updated": 0, "errors": [], "backup": null});
    let failed = |e: WriteError| match e {
        WriteError::Policy(p) => err(StatusCode::UNPROCESSABLE_ENTITY, p.0),
        e => {
            tracing::error!(error = %e, "Import failed");
            err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
        }
    };
    let mut line_errors = Vec::new();
    match parse_import(&lines, remap_to.as_ref())
        .map_err(|_| server_error("an import line has a value of a wrong type"))?
    {
        None => line_errors.push(line_error(1, HEADER_ERROR)),
        Some((records, parse_errors)) => {
            line_errors.extend(parse_errors);
            let mut valid = Vec::new();
            for r in records {
                match validate_project_write(&r.text, &r.source, trusted.as_ref()) {
                    Ok(()) => valid.push(r),
                    Err(e) => line_errors.push(line_error(r.line, &e.0)),
                }
            }
            if !no_backup && w.engine.count() > 0 {
                let path = create_backup(&w, &state.data_dir, state.max_backups, "pre-import")
                    .await
                    .map_err(|e| match e {
                        BackupError::Write(e) => failed(e),
                        e => failed(WriteError::Io(std::io::Error::other(e.to_string()))),
                    })?;
                out["backup"] = json!(path.file_name().map(|n| n.to_string_lossy().into_owned()));
            }
            if strategy == "add" {
                if !valid.is_empty() {
                    let n = valid.len();
                    w.add_memories(
                        valid.iter().map(new_memory).collect(),
                        false,
                        trusted.as_ref(),
                    )
                    .await
                    .map_err(failed)?;
                    out["imported"] = json!(n);
                }
            } else {
                let c = import_smart(&w, valid, trusted.as_ref())
                    .await
                    .map_err(failed)?;
                out["imported"] = json!(c.imported);
                out["skipped"] = json!(c.skipped);
                out["updated"] = json!(c.updated);
            }
        }
    }
    line_errors.extend(auth_errors);
    out["errors"] = Value::Array(line_errors);
    Ok(Json(out))
}

// ---- Scheduled backup -------------------------------------------------------------

/// The time of the next daily backup: 24 hours after the newest scheduled
/// backup, or `first` after `now` if there is none.
pub fn next_scheduled(data_dir: &Path, now: SystemTime, first: Duration) -> SystemTime {
    let newest = list_entries(&backup_dir(data_dir))
        .unwrap_or_default()
        .into_iter()
        .filter(|(n, m)| prefix_of(n) == SCHEDULED && m.is_dir())
        .filter_map(|(_, m)| m.modified().ok())
        .max();
    match newest {
        Some(t) => t + DAY,
        None => now + first,
    }
}

/// Make one backup each day while the server runs. Waits for the engine
/// (the first-start migration can still run).
pub async fn schedule(state: Arc<AppState>) {
    loop {
        let due = next_scheduled(
            &state.data_dir,
            SystemTime::now(),
            Duration::from_secs(3600),
        );
        let wait = due
            .duration_since(SystemTime::now())
            .unwrap_or(Duration::ZERO);
        tokio::time::sleep(wait).await;
        let Some(w) = state.writer.get().cloned() else {
            tokio::time::sleep(Duration::from_secs(60)).await;
            continue;
        };
        match create_backup(&w, &state.data_dir, state.max_backups, SCHEDULED).await {
            Ok(p) => tracing::info!(path = %p.display(), "scheduled backup done"),
            Err(e) => {
                tracing::error!(error = %e, "scheduled backup failed, next try in 1 hour");
                tokio::time::sleep(Duration::from_secs(3600)).await;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(v: Value) -> Record {
        Record::from_json(v).unwrap()
    }

    #[test]
    fn py_dumps_escapes_non_ascii_as_python() {
        assert_eq!(
            py_dumps(&json!({"a": "\u{e9}\u{1f600}\n"})),
            r#"{"a":"\u00e9\ud83d\ude00\n"}"#
        );
    }

    #[test]
    fn header_has_the_python_key_order() {
        let f = ExportFilter {
            source: Some("learning/".into()),
            ..Default::default()
        };
        assert_eq!(
            header_line(&f, 3, "2026-11-16T10:00:00+00:00"),
            r#"{"_header":true,"exported_at":"2026-11-16T10:00:00+00:00","source_filter":"learning/","since":null,"until":null,"count":3,"version":"4.0.0"}"#
        );
    }

    #[test]
    fn record_line_drops_id_and_timestamp() {
        let r = rec(
            json!({"id": 7, "text": "t", "source": "s/a", "created_at": "c",
                           "updated_at": "u", "timestamp": "c", "pinned": true,
                           "links": [{"to_id": 3, "type": "related"}]}),
        );
        let v: Value = serde_json::from_str(&record_line(&r)).unwrap();
        assert_eq!(
            v,
            json!({"text": "t", "source": "s/a", "created_at": "c", "updated_at": "u",
                   "custom_fields": {"pinned": true, "links": [{"to_id": 3, "type": "related"}]}})
        );
        let bare = rec(json!({"id": 8}));
        let v: Value = serde_json::from_str(&record_line(&bare)).unwrap();
        assert_eq!(v["source"], "");
        assert_eq!(v["created_at"], "");
    }

    #[test]
    fn export_filter_compares_strings() {
        let r = rec(json!({"id": 1, "source": "learning/x", "created_at": "2026-02-01"}));
        let f = |s: Option<&str>, a: Option<&str>, b: Option<&str>| ExportFilter {
            source: s.map(Into::into),
            since: a.map(Into::into),
            until: b.map(Into::into),
        };
        assert!(f(Some("learning/"), Some("2026-01"), Some("2026-03")).keeps(&r));
        assert!(f(Some(""), Some(""), None).keeps(&r));
        assert!(!f(Some("codex/"), None, None).keeps(&r));
        assert!(!f(None, Some("2026-03"), None).keeps(&r));
        assert!(!f(None, None, Some("2026-01")).keeps(&r));
    }

    #[test]
    fn backup_names_and_prefixes() {
        let t = PyDateTime::from_unix_us(1_794_826_861_000_000);
        assert_eq!(backup_name("a b/é", &t), "a_b___20261116_110101");
        assert_eq!(prefix_of("pre_restore_20261116_104101"), "pre_restore");
        assert_eq!(prefix_of("manual"), "manual");
        assert_eq!(prefix_of("x_2026111_104101"), "x_2026111_104101");
    }

    #[test]
    fn retention_keeps_newest_two_per_prefix_and_seven_scheduled() {
        let t = |s: u64| SystemTime::UNIX_EPOCH + Duration::from_secs(s);
        let mut e = Vec::new();
        for i in 0..10u64 {
            e.push((format!("scheduled_2026010{}_000000", i % 10), t(i)));
            e.push((format!("manual_2026020{}_000000", i % 10), t(100 + i)));
        }
        e.push(("old_20250101_000000".into(), t(0)));
        let s = survivors(e, 3);
        let sched = s.iter().filter(|n| n.starts_with("scheduled")).count();
        let manual = s.iter().filter(|n| n.starts_with("manual")).count();
        assert_eq!((sched, manual), (7, 3));
        assert!(s.contains("old_20250101_000000"));
        assert!(s.contains("scheduled_20260109_000000"));
        assert!(!s.contains("scheduled_20260100_000000"));
        assert_eq!(survivors(vec![("a_1".into(), t(1))], 0).len(), 1);
    }

    #[test]
    fn filter_keeps_header_and_bad_json_and_checks_remapped_sources() {
        let lines = [
            "{\"_header\":true}",
            "{\"text\":\"a\",\"source\":\"old/x\"}",
            "not json",
            "{\"text\":\"b\",\"source\":\"other/y\"}",
            "{\"text\":\"c\"}",
        ];
        let r = ("old/".to_string(), "new/".to_string());
        let (kept, errors) = filter_writable(&lines, Some(&r), |s| s.starts_with("new/")).unwrap();
        assert_eq!(kept, vec![lines[0], lines[1], lines[2]]);
        assert_eq!(
            errors,
            vec![
                json!({"line": 4, "error": "source prefix not authorized"}),
                json!({"line": 5, "error": "source prefix not authorized"}),
            ]
        );
        assert_eq!(
            filter_writable(&["h", "[1]"], None, |_| true),
            Err(Unreadable)
        );
        assert_eq!(
            filter_writable(&["h", "{\"source\":3}"], None, |_| true),
            Err(Unreadable)
        );
    }

    #[test]
    fn parse_reads_the_engine_rules() {
        assert_eq!(parse_import(&["nope"], None), Ok(None));
        assert_eq!(parse_import(&["{\"_header\":0}"], None), Ok(None));
        assert_eq!(parse_import(&["[]"], None), Err(Unreadable));
        let lines = [
            "{\"_header\":true,\"version\":1}",
            "{\"text\":\"one\",\"source\":\"a/b\",\"created_at\":\"c\",\"custom_fields\":{\"k\":\"v\"}}",
            "{\"source\":\"no-text\"}",
            "",
            "{\"text\":\"two\",\"source\":\"a/c\",\"custom_fields\":[1]}",
        ];
        let r = ("a/".to_string(), "z/".to_string());
        let (records, errors) = parse_import(&lines, Some(&r)).unwrap().unwrap();
        assert_eq!(
            errors,
            vec![
                json!({"line": 3, "error": "Missing required field (text or source)"}),
                json!({"line": 4, "error": "Invalid JSON"}),
            ]
        );
        assert_eq!(records.len(), 2);
        assert_eq!(
            (
                records[0].line,
                records[0].source.as_str(),
                records[0].created_at.as_str()
            ),
            (2, "z/b", "c")
        );
        assert_eq!(records[0].custom["k"], "v");
        assert!(records[1].custom.is_empty());
        let meta = import_metadata(&ImportRecord {
            custom: json!({"imported": "x", "k": 1})
                .as_object()
                .unwrap()
                .clone(),
            ..records[0].clone()
        });
        assert_eq!(
            Value::Object(meta),
            json!({"imported": "x", "import_source": "z/b", "k": 1})
        );
    }

    #[test]
    fn next_scheduled_follows_the_newest_scheduled_folder() {
        let dir = std::env::temp_dir().join(format!("p54_sched_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let now = SystemTime::now();
        let hour = Duration::from_secs(3600);
        assert_eq!(next_scheduled(&dir, now, hour), now + hour);
        std::fs::create_dir_all(backup_dir(&dir).join("scheduled_20260101_000000")).unwrap();
        std::fs::create_dir_all(backup_dir(&dir).join("manual_20260101_000000")).unwrap();
        let next = next_scheduled(&dir, now, hour);
        assert!(
            next > now + DAY - Duration::from_secs(60)
                && next <= now + DAY + Duration::from_secs(5)
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
