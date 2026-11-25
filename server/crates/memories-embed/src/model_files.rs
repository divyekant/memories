//! Model files in the Hugging Face cache layout, the same layout that
//! `hf_hub_download` of the Python server reads and writes:
//! `<cache>/models--<org>--<name>/refs/main` holds the commit, and the files
//! are in `<cache>/models--<org>--<name>/snapshots/<commit>/`.

use std::path::{Path, PathBuf};

use crate::{EmbedError, Result};

const MODEL_FILE: &str = "model.safetensors";
const CONFIG_FILE: &str = "config.json";
const TOKENIZER_FILE: &str = "tokenizer.json";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelFiles {
    /// The weights, in the safetensors format.
    pub model: PathBuf,
    pub config: PathBuf,
    pub tokenizer: PathBuf,
}

/// Same map as `MODEL_MAP` in `onnx_embedder.py`.
pub fn repo_id(model: &str) -> &str {
    match model {
        "all-MiniLM-L6-v2" => "sentence-transformers/all-MiniLM-L6-v2",
        other => other,
    }
}

fn default_hub_cache() -> PathBuf {
    let var = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    if let Some(dir) = var("HF_HUB_CACHE") {
        return dir.into();
    }
    let home = var("HF_HOME").map(PathBuf::from).unwrap_or_else(|| {
        var("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(var("HOME").unwrap_or_default()).join(".cache"))
            .join("huggingface")
    });
    home.join("hub")
}

fn repo_dir(cache: &Path, repo: &str) -> PathBuf {
    cache.join(format!("models--{}", repo.replace('/', "--")))
}

/// The commit in `refs/main`, if it is a plain name (no path parts).
fn cached_commit(repo_dir: &Path) -> Option<String> {
    let commit = std::fs::read_to_string(repo_dir.join("refs/main")).ok()?;
    let commit = commit.trim();
    (!commit.is_empty() && commit.chars().all(|c| c.is_ascii_alphanumeric()))
        .then(|| commit.to_string())
}

fn cached(repo_dir: &Path) -> Option<ModelFiles> {
    let snapshot = repo_dir.join("snapshots").join(cached_commit(repo_dir)?);
    let files = ModelFiles {
        model: snapshot.join(MODEL_FILE),
        config: snapshot.join(CONFIG_FILE),
        tokenizer: snapshot.join(TOKENIZER_FILE),
    };
    (files.model.is_file() && files.config.is_file() && files.tokenizer.is_file()).then_some(files)
}

/// Returns the cached files of `model`. Downloads each file that is not in
/// the cache from the Hugging Face Hub. A cache of version 6.0.0 or older has
/// `onnx/model.onnx` and no `model.safetensors`, so the first start after an
/// upgrade downloads the weights again.
pub async fn resolve_model_files(model: &str, cache_dir: Option<&Path>) -> Result<ModelFiles> {
    let cache = cache_dir.map_or_else(default_hub_cache, Path::to_path_buf);
    resolve_with(&Hub::from_env()?, repo_id(model), &cache).await
}

async fn resolve_with(hub: &Hub, repo: &str, cache: &Path) -> Result<ModelFiles> {
    let dir = repo_dir(cache, repo);
    if let Some(files) = cached(&dir) {
        return Ok(files);
    }
    if hub.offline {
        return Err(EmbedError::Download(format!(
            "{repo}: {MODEL_FILE}, {CONFIG_FILE} or {TOKENIZER_FILE} is not in the cache {} and HF_HUB_OFFLINE is set",
            cache.display()
        )));
    }
    hub.download(repo, &dir).await?;
    cached(&dir)
        .ok_or_else(|| EmbedError::Download(format!("{repo}: files missing after download")))
}

/// Hub settings, read from the same variables as `huggingface_hub`.
struct Hub {
    client: reqwest::Client,
    endpoint: String,
    token: Option<String>,
    offline: bool,
}

impl Hub {
    fn from_env() -> Result<Self> {
        let var = |k: &str| {
            std::env::var(k)
                .ok()
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
        };
        let token = var("HF_TOKEN")
            .or_else(|| var("HUGGING_FACE_HUB_TOKEN"))
            .or_else(|| {
                let file = var("HF_TOKEN_PATH").map(PathBuf::from).unwrap_or_else(|| {
                    default_hub_cache()
                        .parent()
                        .map_or_else(PathBuf::new, |home| home.join("token"))
                });
                std::fs::read_to_string(file)
                    .ok()
                    .map(|t| t.trim().to_string())
                    .filter(|t| !t.is_empty())
            });
        Ok(Self {
            client: client(reqwest::Client::builder())?,
            endpoint: var("HF_ENDPOINT").unwrap_or_else(|| "https://huggingface.co".into()),
            token,
            offline: var("HF_HUB_OFFLINE")
                .is_some_and(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes" | "on")),
        })
    }

    async fn get(&self, path: &str) -> Result<reqwest::Response> {
        let mut req = self
            .client
            .get(format!("{}/{path}", self.endpoint.trim_end_matches('/')));
        if let Some(t) = &self.token {
            req = req.bearer_auth(t);
        }
        req.send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map_err(|e| EmbedError::Download(e.to_string()))
    }

    async fn download(&self, repo: &str, dir: &Path) -> Result<()> {
        // A cache with a commit keeps that commit: the server does not move an
        // install to a newer model revision, and the files that an older
        // server version put in that snapshot stay in use (rollback).
        if let Some(commit) = cached_commit(dir) {
            match self.download_at(repo, dir, &commit).await {
                Ok(()) => return Ok(()),
                Err(e) => tracing::warn!(
                    "cannot complete the cached revision {commit} of {repo}: {e}; trying the current revision"
                ),
            }
        }
        let info = self
            .get(&format!("api/models/{repo}/revision/main"))
            .await?
            .bytes()
            .await
            .map_err(|e| EmbedError::Download(e.to_string()))?;
        let commit = serde_json::from_slice::<serde_json::Value>(&info)
            .ok()
            .and_then(|v| v["sha"].as_str().map(str::to_owned))
            .filter(|s| !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric()))
            .ok_or_else(|| {
                EmbedError::Download(format!("{repo}: no commit in the revision reply"))
            })?;
        self.download_at(repo, dir, &commit).await
    }

    /// Downloads each model file that `snapshots/<commit>` does not have,
    /// then points `refs/main` to that commit.
    async fn download_at(&self, repo: &str, dir: &Path, commit: &str) -> Result<()> {
        tracing::info!("Downloading model {repo} at commit {commit}");

        let snapshot = dir.join("snapshots").join(commit);
        for file in [MODEL_FILE, CONFIG_FILE, TOKENIZER_FILE] {
            let target = snapshot.join(file);
            if target.is_file() {
                continue;
            }
            let resp = self
                .get(&format!("{repo}/resolve/{commit}/{file}"))
                .await
                .map_err(|e| match e {
                    // A model with no safetensors weights gives 404 here.
                    EmbedError::Download(e) => EmbedError::Download(format!(
                        "{e}; this server needs {MODEL_FILE}, {CONFIG_FILE} and {TOKENIZER_FILE} of the model"
                    )),
                    other => other,
                })?;
            write_atomic(&target, Body::Stream(resp)).await?;
        }
        write_atomic(&dir.join("refs/main"), Body::Bytes(commit.as_bytes())).await
    }
}

/// Same limits as `huggingface_hub`: 10 s to connect and 10 s for each read.
fn client(builder: reqwest::ClientBuilder) -> Result<reqwest::Client> {
    builder
        .connect_timeout(std::time::Duration::from_secs(10))
        .read_timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| EmbedError::Download(e.to_string()))
}

enum Body<'a> {
    Bytes(&'a [u8]),
    Stream(reqwest::Response),
}

/// Writes to a temporary file, syncs it, then renames it, so that a stop or
/// a power loss never leaves a part file at `path`. A streamed body must have
/// the length that the server gave.
async fn write_atomic(path: &Path, body: Body<'_>) -> Result<()> {
    use std::sync::atomic::{AtomicU64, Ordering};
    use tokio::io::AsyncWriteExt;
    static SEQ: AtomicU64 = AtomicU64::new(0);

    let parent = path.parent().unwrap_or(Path::new("."));
    tokio::fs::create_dir_all(parent).await?;
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let tmp = parent.join(format!(
        ".{name}.tmp-{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    ));
    let result = async {
        let mut file = tokio::fs::File::create(&tmp).await?;
        match body {
            Body::Bytes(data) => file.write_all(data).await?,
            Body::Stream(mut resp) => {
                let expected = resp.content_length();
                let mut written = 0u64;
                while let Some(chunk) = resp
                    .chunk()
                    .await
                    .map_err(|e| EmbedError::Download(e.to_string()))?
                {
                    file.write_all(&chunk).await?;
                    written += chunk.len() as u64;
                }
                if expected.is_some_and(|n| n != written) {
                    return Err(EmbedError::Download(format!(
                        "{}: got {written} bytes, expected {}",
                        path.display(),
                        expected.unwrap_or_default()
                    )));
                }
            }
        }
        file.sync_all().await?;
        tokio::fs::rename(&tmp, path).await?;
        Ok(())
    }
    .await;
    if result.is_err() {
        let _ = tokio::fs::remove_file(&tmp).await;
    }
    result
}

/// Same as Python `shutil.copytree(src, dst, dirs_exist_ok=True)`: symlinks
/// are followed and copied as files.
pub(crate) fn copy_tree(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if std::fs::metadata(&from)?.is_dir() {
            copy_tree(&from, &to)?;
        } else {
            std::fs::copy(&from, &to)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_id_maps_the_short_name_only() {
        assert_eq!(
            repo_id("all-MiniLM-L6-v2"),
            "sentence-transformers/all-MiniLM-L6-v2"
        );
        assert_eq!(repo_id("BAAI/bge-small-en-v1.5"), "BAAI/bge-small-en-v1.5");
    }

    #[test]
    fn cached_reads_the_python_layout() {
        let cache =
            std::env::temp_dir().join(format!("memories-embed-layout-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cache);
        let dir = repo_dir(&cache, "sentence-transformers/all-MiniLM-L6-v2");
        assert!(dir.ends_with("models--sentence-transformers--all-MiniLM-L6-v2"));
        assert_eq!(cached(&dir), None);

        let snap = dir.join("snapshots/abc123");
        // The cache of version 6.0.0: the ONNX file and the tokenizer.
        std::fs::create_dir_all(snap.join("onnx")).unwrap();
        std::fs::create_dir_all(dir.join("refs")).unwrap();
        std::fs::write(dir.join("refs/main"), "abc123").unwrap();
        std::fs::write(snap.join("onnx/model.onnx"), b"m").unwrap();
        std::fs::write(snap.join("tokenizer.json"), b"t").unwrap();
        assert_eq!(cached(&dir), None, "weights and config missing");
        std::fs::write(snap.join("model.safetensors"), b"m").unwrap();
        assert_eq!(cached(&dir), None, "config missing");
        std::fs::write(snap.join("config.json"), b"c").unwrap();
        assert_eq!(
            cached(&dir),
            Some(ModelFiles {
                model: snap.join("model.safetensors"),
                config: snap.join("config.json"),
                tokenizer: snap.join("tokenizer.json"),
            })
        );
        std::fs::remove_dir_all(&cache).unwrap();
    }

    /// A local stub of the Hub: the revision reply, then `files` files. A
    /// path that contains `missing` gets the status 404.
    async fn stub_hub(
        sha: &'static str,
        files: usize,
        missing: &'static str,
    ) -> (String, tokio::task::JoinHandle<Vec<String>>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move {
            let mut paths = Vec::new();
            while let Ok((mut sock, _)) = listener.accept().await {
                let mut buf = vec![0u8; 4096];
                let n = sock.read(&mut buf).await.unwrap();
                let req = String::from_utf8_lossy(&buf[..n]).to_string();
                let path = req
                    .split_whitespace()
                    .nth(1)
                    .unwrap_or_default()
                    .to_string();
                let body = if path.ends_with("/revision/main") {
                    format!("{{\"sha\":\"{sha}\"}}")
                } else {
                    format!("content of {path}")
                };
                let status = if !missing.is_empty() && path.contains(missing) {
                    "404 Not Found"
                } else {
                    "200 OK"
                };
                let reply = format!(
                    "HTTP/1.1 {status}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                    body.len()
                );
                sock.write_all(reply.as_bytes()).await.unwrap();
                paths.push(path);
                if paths.len() == 1 + files {
                    break;
                }
            }
            paths
        });
        (url, task)
    }

    fn stub(endpoint: String, offline: bool) -> Hub {
        Hub {
            client: client(reqwest::Client::builder().no_proxy()).unwrap(),
            endpoint,
            token: None,
            offline,
        }
    }

    #[tokio::test]
    async fn download_writes_the_python_layout() {
        let cache = std::env::temp_dir().join(format!("memories-embed-dl-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cache);
        let (url, server) = stub_hub("abc123", 3, "").await;
        let files = resolve_with(&stub(url, false), "org/model", &cache)
            .await
            .unwrap();
        let paths = server.await.unwrap();
        assert_eq!(
            paths,
            [
                "/api/models/org/model/revision/main",
                "/org/model/resolve/abc123/model.safetensors",
                "/org/model/resolve/abc123/config.json",
                "/org/model/resolve/abc123/tokenizer.json",
            ]
        );
        let dir = cache.join("models--org--model");
        assert_eq!(
            std::fs::read_to_string(dir.join("refs/main")).unwrap(),
            "abc123"
        );
        assert_eq!(files.model, dir.join("snapshots/abc123/model.safetensors"));
        assert_eq!(
            std::fs::read_to_string(&files.model).unwrap(),
            "content of /org/model/resolve/abc123/model.safetensors"
        );
        let leftovers = std::fs::read_dir(dir.join("snapshots/abc123"))
            .unwrap()
            .filter(|e| {
                e.as_ref()
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .contains(".tmp-")
            })
            .count();
        assert_eq!(leftovers, 0);
        // The second call uses the cache and makes no request.
        assert_eq!(
            resolve_with(
                &stub("http://127.0.0.1:1".into(), true),
                "org/model",
                &cache
            )
            .await
            .unwrap(),
            files
        );
        std::fs::remove_dir_all(&cache).unwrap();
    }

    /// The upgrade from 6.0.0: the tokenizer is in the cache, the weights and
    /// the config are not.
    #[tokio::test]
    async fn download_gets_only_the_files_that_the_cache_does_not_have() {
        let cache = std::env::temp_dir().join(format!("memories-embed-up-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cache);
        let snap = cache.join("models--org--model/snapshots/abc123");
        std::fs::create_dir_all(snap.join("onnx")).unwrap();
        std::fs::create_dir_all(cache.join("models--org--model/refs")).unwrap();
        std::fs::write(cache.join("models--org--model/refs/main"), "abc123").unwrap();
        std::fs::write(snap.join("onnx/model.onnx"), b"old").unwrap();
        std::fs::write(snap.join("tokenizer.json"), b"old tokenizer").unwrap();

        let offline = resolve_with(
            &stub("http://127.0.0.1:1".into(), true),
            "org/model",
            &cache,
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(
            offline
                .contains("model.safetensors, config.json or tokenizer.json is not in the cache"),
            "{offline}"
        );

        // The Hub is at a newer commit. The cache keeps its commit, and the
        // server does not ask for the current revision.
        let (url, server) = stub_hub("fff999", 1, "").await;
        let files = resolve_with(&stub(url, false), "org/model", &cache)
            .await
            .unwrap();
        assert_eq!(
            server.await.unwrap(),
            [
                "/org/model/resolve/abc123/model.safetensors",
                "/org/model/resolve/abc123/config.json",
            ]
        );
        assert_eq!(
            std::fs::read_to_string(cache.join("models--org--model/refs/main")).unwrap(),
            "abc123"
        );
        assert_eq!(std::fs::read(&files.tokenizer).unwrap(), b"old tokenizer");
        assert_eq!(std::fs::read(snap.join("onnx/model.onnx")).unwrap(), b"old");
        std::fs::remove_dir_all(&cache).unwrap();
    }

    #[tokio::test]
    async fn download_uses_the_current_revision_if_the_cached_one_has_no_weights() {
        let cache =
            std::env::temp_dir().join(format!("memories-embed-move-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cache);
        let dir = cache.join("models--org--model");
        std::fs::create_dir_all(dir.join("refs")).unwrap();
        std::fs::write(dir.join("refs/main"), "abc123\n").unwrap();
        // 404 for the cached commit, then the revision reply and 3 files.
        let (url, server) = stub_hub("def456", 4, "abc123").await;
        let files = resolve_with(&stub(url, false), "org/model", &cache)
            .await
            .unwrap();
        let paths = server.await.unwrap();
        assert_eq!(paths[0], "/org/model/resolve/abc123/model.safetensors");
        assert_eq!(paths[1], "/api/models/org/model/revision/main");
        assert_eq!(files.model, dir.join("snapshots/def456/model.safetensors"));
        assert_eq!(
            std::fs::read_to_string(dir.join("refs/main")).unwrap(),
            "def456"
        );
        std::fs::remove_dir_all(&cache).unwrap();
    }

    #[tokio::test]
    async fn download_names_the_needed_files_if_the_model_has_no_safetensors() {
        let cache = std::env::temp_dir().join(format!("memories-embed-404-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cache);
        let (url, _server) = stub_hub("abc123", 1, "model.safetensors").await;
        let err = resolve_with(&stub(url, false), "org/model", &cache)
            .await
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("404") && err.contains("this server needs model.safetensors"),
            "{err}"
        );
        let _ = std::fs::remove_dir_all(&cache);
    }

    #[tokio::test]
    async fn download_refuses_a_bad_commit_and_offline_mode() {
        let cache = std::env::temp_dir().join(format!("memories-embed-bad-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&cache);
        let (url, _server) = stub_hub("../../etc", 0, "").await;
        let err = resolve_with(&stub(url, false), "org/model", &cache)
            .await
            .unwrap_err();
        assert!(err.to_string().contains("no commit"), "{err}");
        let err = resolve_with(
            &stub("http://127.0.0.1:1".into(), true),
            "org/model",
            &cache,
        )
        .await
        .unwrap_err();
        assert!(err.to_string().contains("HF_HUB_OFFLINE"), "{err}");
        let _ = std::fs::remove_dir_all(&cache);
    }
}
