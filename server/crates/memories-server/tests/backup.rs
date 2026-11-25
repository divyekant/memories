//! Task P5-4 against a Qdrant server and the real embedder: export, import,
//! backup, restore and snapshot routes.
//!
//! Run: QDRANT_TEST_URL=http://127.0.0.1:6333 \
//!   cargo test -p memories-server --test backup -- --ignored

#[allow(dead_code)]
mod common;

use std::path::PathBuf;
use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use memories_embed::EmbedConfig;
use memories_engine::Engine;
use memories_engine::rank::GraphParams;
use memories_engine::writes::Writer;
use memories_server::{AppState, app, audit::AuditLog, extract::Extract, keys::KeyStore};
use memories_store::QdrantSettings;
use serde_json::{Value, json};
use tower::ServiceExt;

const ADMIN: &str = "test-admin-key";

struct T {
    state: Arc<AppState>,
    dir: PathBuf,
    rw: String,
    ro: String,
}

async fn setup(name: &str) -> T {
    setup_with(name, 10).await
}

async fn setup_with(name: &str, max_backups: usize) -> T {
    let url =
        std::env::var("QDRANT_TEST_URL").expect("set QDRANT_TEST_URL, e.g. http://127.0.0.1:6333");
    let base = format!("p54_{name}_{}", std::process::id());
    let lookup = |k: &str| match k {
        "QDRANT_URL" => Some(url.clone()),
        "QDRANT_COLLECTION" => Some(base.clone()),
        _ => None,
    };
    let settings = QdrantSettings::from_lookup(lookup).unwrap();
    let mut cfg = common::config(ADMIN, 1000);
    cfg.audit_log = true;
    cfg.max_backups = max_backups;
    let dir = cfg.data_dir.clone();
    let embed = EmbedConfig {
        cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
            .ok()
            .map(PathBuf::from),
        ..EmbedConfig::default()
    };
    let engine = Engine::open(&settings, &dir, embed, GraphParams::default())
        .await
        .unwrap();
    let _ = engine.store.delete_collection().await;
    engine.store.ensure_collection(engine.dim).await.unwrap();
    let engine = Arc::new(engine);
    let writer = Writer::open(engine.clone(), dir.clone(), 0.97)
        .await
        .unwrap();
    let keys = KeyStore::open(&dir.join("keys.db")).unwrap();
    let rw = keys
        .create("rw", "read-write", vec!["learning/".into()], None)
        .unwrap()
        .key;
    let ro = keys
        .create("ro", "read-only", vec!["learning/".into()], None)
        .unwrap()
        .key;
    let audit = AuditLog::open(&dir.join("audit.db")).unwrap();
    let extract = Extract::open(&dir, Default::default(), None, None).unwrap();
    let state = AppState::with_extract(&cfg, keys, Some(audit), extract, Some(engine));
    let _ = state.writer.set(Arc::new(writer));
    T { state, dir, rw, ro }
}

impl T {
    async fn call(&self, method: &str, path: &str, key: &str, body: Body) -> (StatusCode, String) {
        let req = Request::builder()
            .method(method)
            .uri(path)
            .header("X-API-Key", key)
            .header("Content-Type", "application/json")
            .body(body)
            .unwrap();
        let res = app(self.state.clone()).oneshot(req).await.unwrap();
        let status = res.status();
        let bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
        (status, String::from_utf8(bytes.to_vec()).unwrap())
    }

    async fn json(&self, method: &str, path: &str, key: &str, body: Value) -> (StatusCode, Value) {
        let b = if body.is_null() {
            Body::empty()
        } else {
            Body::from(body.to_string())
        };
        let (s, text) = self.call(method, path, key, b).await;
        (s, serde_json::from_str(&text).unwrap_or(Value::Null))
    }

    async fn export(&self, query: &str, key: &str) -> Vec<Value> {
        let (s, text) = self
            .call("GET", &format!("/export{query}"), key, Body::empty())
            .await;
        assert_eq!(s, StatusCode::OK, "{text}");
        text.lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    async fn import(&self, query: &str, key: &str, body: &str) -> (StatusCode, Value) {
        let (s, text) = self
            .call(
                "POST",
                &format!("/import{query}"),
                key,
                Body::from(body.to_owned()),
            )
            .await;
        (s, serde_json::from_str(&text).unwrap_or(Value::Null))
    }

    async fn cleanup(self) {
        let w = self.state.writer.get().unwrap();
        let _ = w.engine.store.delete_collection().await;
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn ndjson(lines: &[Value]) -> String {
    lines
        .iter()
        .map(|l| l.to_string() + "\n")
        .collect::<String>()
}

fn seed() -> String {
    ndjson(&[
        json!({"_header": true}),
        json!({"text": "Billing uses Postgres 16 with a pool of 20.", "source": "learning/app/db",
               "created_at": "2026-01-01T00:00:00+00:00",
               "custom_fields": {"pinned": true, "links": [{"to_id": 2, "type": "related"}]}}),
        json!({"text": "The fleet API resets its rate limit each minute.", "source": "learning/app/api"}),
        json!({"text": "Codex writes the release notes on Fridays.", "source": "codex/app/notes",
               "custom_fields": {"links": [{"to_id": 0, "type": "supersedes"}]}}),
    ])
}

fn links(export: &[Value]) -> usize {
    export[1..]
        .iter()
        .map(|r| r["custom_fields"]["links"].as_array().map_or(0, Vec::len))
        .sum()
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server and the embedder: set QDRANT_TEST_URL"]
async fn export_and_import_follow_the_python_routes() {
    let t = setup("io").await;
    let (s, r) = t.import("?no_backup=true", ADMIN, &seed()).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        r,
        json!({"imported": 3, "skipped": 0, "updated": 0, "errors": [], "backup": null})
    );

    let all = t.export("", ADMIN).await;
    assert_eq!(all[0]["count"], 3);
    assert_eq!(all[0]["version"], "4.0.0");
    assert_eq!(all[0]["source_filter"], Value::Null);
    assert_eq!(all.len(), 4);
    let first = &all[1];
    assert_eq!(first["text"], "Billing uses Postgres 16 with a pool of 20.");
    assert_ne!(
        first["created_at"], "2026-01-01T00:00:00+00:00",
        "a new created_at"
    );
    assert_eq!(first["custom_fields"]["imported"], true);
    assert_eq!(first["custom_fields"]["import_source"], "learning/app/db");
    assert_eq!(first["custom_fields"]["pinned"], true);
    assert_eq!(links(&all), 2);
    for r in &all[1..] {
        let c = r["custom_fields"].as_object().unwrap();
        assert!(!c.contains_key("timestamp") && !c.contains_key("id"));
    }

    let scoped = t.export("?source=learning/", ADMIN).await;
    assert_eq!((scoped[0]["count"].clone(), scoped.len()), (json!(2), 3));
    assert_eq!(scoped[0]["source_filter"], "learning/");
    let none = t.export("?since=2999", ADMIN).await;
    assert_eq!((none[0]["count"].clone(), none.len()), (json!(0), 1));
    // Python counts before the key check.
    let ro = t.export("", &t.ro).await;
    assert_eq!((ro[0]["count"].clone(), ro.len()), (json!(3), 3));

    let (s, r) = t
        .json(
            "GET",
            &format!("/export?source={}", "x".repeat(501)),
            ADMIN,
            Value::Null,
        )
        .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(r["detail"][0]["loc"], json!(["query", "source"]));

    // Remap, the key scope, the backup and the line errors.
    let body = ndjson(&[
        json!({"_header": true}),
        json!({"text": "Remapped record one.", "source": "codex/x"}),
        json!({"text": "Out of scope.", "source": "claude-code/y"}),
        json!({"source": "learning/no-text"}),
    ]) + "not json\n";
    let (s, r) = t
        .import("?source_remap=codex/=learning/", &t.rw, &body)
        .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["imported"], 1);
    // Python numbers the engine errors after the key filter drops a line.
    assert_eq!(
        r["errors"],
        json!([
            {"line": 3, "error": "Missing required field (text or source)"},
            {"line": 4, "error": "Invalid JSON"},
            {"line": 3, "error": "source prefix not authorized"},
        ])
    );
    let backup = r["backup"].as_str().unwrap();
    assert!(backup.starts_with("pre-import_"), "{backup}");
    assert!(
        t.dir
            .join("backups")
            .join(backup)
            .join("memories_v2.snapshot")
            .is_file()
    );
    let after = t.export("?source=learning/x", ADMIN).await;
    assert_eq!(after[1]["custom_fields"]["import_source"], "learning/x");

    let (s, r) = t
        .import("?strategy=nope", ADMIN, "{\"_header\": true}\n")
        .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    assert_eq!(
        r["detail"],
        "Invalid strategy. Must be one of: {'smart+extract', 'add', 'smart'}"
    );
    let (s, r) = t.import("?no_backup=maybe", ADMIN, "").await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(r["detail"][0]["loc"], json!(["query", "no_backup"]));
    let (s, r) = t.import("", ADMIN, "").await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(
        r["errors"],
        json!([{"line": 1, "error": "Missing header: first line must contain _header: true"}])
    );
    let (s, _) = t.import("", ADMIN, "{\"_header\": true}\n[1]\n").await;
    assert_eq!(s, StatusCode::INTERNAL_SERVER_ERROR);

    // Smart: an exact duplicate is skipped; a new text is added.
    let body = ndjson(&[
        json!({"_header": true}),
        json!({"text": "The fleet API resets its rate limit each minute.", "source": "learning/app/api"}),
        json!({"text": "Payments close on the first day of the month.", "source": "learning/app/pay"}),
    ]);
    let (s, r) = t.import("?strategy=smart&no_backup=1", ADMIN, &body).await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(
        (r["imported"].clone(), r["skipped"].clone()),
        (json!(1), json!(1))
    );
    t.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server and the embedder: set QDRANT_TEST_URL"]
async fn backup_and_restore_give_back_the_counts_and_the_links() {
    let t = setup("br").await;
    t.import("?no_backup=true", ADMIN, &seed()).await;
    let before = t.export("", ADMIN).await;

    let (s, r) = t
        .json("POST", "/backup?prefix=exit%20check", ADMIN, Value::Null)
        .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r["message"], "Backup created successfully");
    let path = PathBuf::from(r["backup_path"].as_str().unwrap());
    let name = path.file_name().unwrap().to_str().unwrap().to_owned();
    assert!(name.starts_with("exit_check_"), "{name}");
    for f in [
        "memories_v2.snapshot",
        "export.ndjson",
        "keys.db",
        "audit.db",
        "state.db",
    ] {
        assert!(path.join(f).is_file(), "{f}");
    }
    let saved = std::fs::read_to_string(path.join("export.ndjson")).unwrap();
    assert_eq!(saved.lines().count(), before.len());

    let (s, r) = t.json("GET", "/backups", ADMIN, Value::Null).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(r["count"], 1);
    assert_eq!(r["backups"][0]["name"], name.as_str());
    assert!(r["backups"][0]["created"].as_f64().unwrap() > 1.7e9);

    // A change after the backup; the restore takes it away.
    let (_, r) = t
        .json(
            "POST",
            "/memory/add",
            ADMIN,
            json!({"text": "A record after the backup.", "source": "learning/late"}),
        )
        .await;
    let late_id = r["id"].as_u64().unwrap();
    t.json("DELETE", "/memory/1?force=true", ADMIN, Value::Null)
        .await;

    let (s, r) = t
        .json("POST", "/restore", ADMIN, json!({"backup_name": name}))
        .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(
        r,
        json!({"success": true, "restored_from": name, "total_memories": 3,
               "message": "Restored successfully"})
    );
    let after = t.export("", ADMIN).await;
    assert_eq!(after.len(), before.len());
    assert_eq!(after[0]["count"], before[0]["count"]);
    assert_eq!(links(&after), links(&before));
    assert_eq!(after[1..], before[1..]);
    // The restore made a backup first, and the ID counter did not go down.
    let (_, r) = t.json("GET", "/backups", ADMIN, Value::Null).await;
    assert_eq!(r["count"], 2);
    let (_, r) = t
        .json(
            "POST",
            "/memory/add",
            ADMIN,
            json!({"text": "A record after the restore.", "source": "learning/later"}),
        )
        .await;
    assert!(r["id"].as_u64().unwrap() > late_id);

    // Error replies.
    for (body, status, detail) in [
        (
            json!({"backup_name": "nope"}),
            404,
            "Backup 'nope' not found",
        ),
        (
            json!({"backup_name": "../x"}),
            400,
            "Invalid backup name: ../x",
        ),
    ] {
        let (s, r) = t.json("POST", "/restore", ADMIN, body).await;
        assert_eq!(
            (s.as_u16(), r["detail"].as_str().unwrap()),
            (status, detail)
        );
    }
    std::fs::create_dir_all(t.dir.join("backups/python_20260101_000000")).unwrap();
    let (s, r) = t
        .json(
            "POST",
            "/restore",
            ADMIN,
            json!({"backup_name": "python_20260101_000000"}),
        )
        .await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    assert_eq!(r["detail"], "Backup 'python_20260101_000000' is incomplete");
    let (s, r) = t
        .json("POST", "/restore", ADMIN, json!({"backup_name": ""}))
        .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(r["detail"][0]["type"], "string_too_short");
    let (s, _) = t
        .json(
            "POST",
            "/backup?prefix=".to_owned().as_str(),
            &t.rw,
            Value::Null,
        )
        .await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    let (s, _) = t
        .json(
            "POST",
            &format!("/backup?prefix={}", "p".repeat(51)),
            ADMIN,
            Value::Null,
        )
        .await;
    assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
    t.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server and the embedder: set QDRANT_TEST_URL"]
async fn snapshot_create_list_and_restore() {
    let t = setup("snap").await;
    t.import("?no_backup=true", ADMIN, &seed()).await;
    let (s, r) = t.json("GET", "/snapshots", ADMIN, Value::Null).await;
    assert_eq!(
        (s, r),
        (StatusCode::OK, json!({"snapshots": [], "count": 0}))
    );

    let (s, r) = t.json("POST", "/snapshots", ADMIN, Value::Null).await;
    assert_eq!(s, StatusCode::OK, "{r}");
    let name = r["snapshot"].as_str().unwrap().to_owned();
    let (_, r) = t.json("GET", "/snapshots", ADMIN, Value::Null).await;
    assert_eq!(r["count"], 1);
    assert_eq!(r["snapshots"][0]["name"], name.as_str());
    assert_eq!(r["snapshots"][0]["reason"], "manual");
    assert_eq!(r["snapshots"][0]["point_count"], 3);
    let (_, r) = t
        .json("GET", "/audit?action=snapshot.created", ADMIN, Value::Null)
        .await;
    assert_eq!(r["entries"][0]["resource_id"], name.as_str());

    t.json(
        "POST",
        "/memory/add",
        ADMIN,
        json!({"text": "A record after the snapshot.", "source": "learning/late"}),
    )
    .await;
    let (s, r) = t
        .json(
            "POST",
            &format!("/snapshots/{name}/restore"),
            ADMIN,
            Value::Null,
        )
        .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    assert_eq!(r, json!({"success": true, "restored": name}));
    let after = t.export("", ADMIN).await;
    assert_eq!(after[0]["count"], 3);
    assert_eq!(links(&after), 2);

    let (s, r) = t
        .json("POST", "/snapshots/nope/restore", ADMIN, Value::Null)
        .await;
    assert_eq!(
        (s, r),
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"detail": "Internal server error"})
        )
    );
    let (s, _) = t.json("GET", "/snapshots", &t.ro, Value::Null).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    t.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server and the embedder: set QDRANT_TEST_URL"]
async fn a_restore_keeps_its_source_when_the_cleanup_runs() {
    // MAX_BACKUPS=3. The restore source is the third newest manual folder:
    // the new pre_restore folder pushes it out of the 3 newest, and it is not
    // one of the 2 newest manual folders.
    let t = setup_with("keep", 3).await;
    t.import("?no_backup=true", ADMIN, &seed()).await;
    let mut names = Vec::new();
    for _ in 0..4 {
        let (s, r) = t.json("POST", "/backup", ADMIN, Value::Null).await;
        assert_eq!(s, StatusCode::OK, "{r}");
        let p = PathBuf::from(r["backup_path"].as_str().unwrap());
        names.push(p.file_name().unwrap().to_str().unwrap().to_owned());
    }
    assert_eq!(
        names.iter().collect::<std::collections::HashSet<_>>().len(),
        4,
        "no shared folder"
    );
    let (_, r) = t.json("GET", "/backups", ADMIN, Value::Null).await;
    assert_eq!(r["count"], 3, "the oldest manual folder is gone");
    let (s, r) = t
        .json("POST", "/restore", ADMIN, json!({"backup_name": names[1]}))
        .await;
    assert_eq!(s, StatusCode::OK, "{r}");
    let (_, r) = t.json("GET", "/backups", ADMIN, Value::Null).await;
    let left: Vec<&str> = r["backups"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b["name"].as_str().unwrap())
        .collect();
    assert!(
        !left.contains(&names[1].as_str()),
        "cleanup ran after the upload: {left:?}"
    );
    t.cleanup().await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "needs a Qdrant server and the embedder: set QDRANT_TEST_URL"]
async fn smart_import_keeps_the_newer_record_of_a_borderline_match() {
    let t = setup("smart").await;
    let old = ndjson(&[
        json!({"_header": true}),
        json!({"text": "The billing service stores invoices in Postgres.", "source": "learning/s"}),
    ]);
    t.import("?no_backup=true", ADMIN, &old).await;
    let near = "The billing service keeps all of its invoices in a Postgres database.";
    let body = |created: &str| {
        ndjson(&[
            json!({"_header": true}),
            json!({"text": near, "source": "learning/s", "created_at": created}),
        ])
    };
    let (_, top) = t
        .json(
            "POST",
            "/memory/is-novel",
            ADMIN,
            json!({"text": near, "threshold": 0.5}),
        )
        .await;
    let sim = top["most_similar"]["similarity"].as_f64().unwrap();
    assert!(
        (0.80..0.95).contains(&sim),
        "borderline fixture: similarity {sim}"
    );
    // Older than the stored record: skipped.
    let (_, r) = t
        .import("?strategy=smart&no_backup=true", ADMIN, &body("2000-01-01"))
        .await;
    assert_eq!(
        (
            r["imported"].clone(),
            r["skipped"].clone(),
            r["updated"].clone()
        ),
        (json!(0), json!(1), json!(0))
    );
    // Newer: the stored record is replaced.
    let (_, r) = t
        .import("?strategy=smart&no_backup=true", ADMIN, &body("2999-01-01"))
        .await;
    assert_eq!(
        (
            r["imported"].clone(),
            r["skipped"].clone(),
            r["updated"].clone()
        ),
        (json!(1), json!(0), json!(1))
    );
    let all = t.export("", ADMIN).await;
    assert_eq!(all.len(), 2);
    assert_eq!(all[1]["text"], near);
    // A cross-source borderline match is never deleted: the record is added.
    let cross = ndjson(&[
        json!({"_header": true}),
        json!({"text": "The billing service stores invoices in Postgres.", "source": "codex/other", "created_at": "2999-01-01"}),
    ]);
    let (_, r) = t
        .import("?strategy=smart&no_backup=true", ADMIN, &cross)
        .await;
    assert_eq!(
        (r["imported"].clone(), r["updated"].clone()),
        (json!(1), json!(0))
    );
    assert_eq!(t.export("", ADMIN).await.len(), 3);
    t.cleanup().await;
}
