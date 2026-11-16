//! Model files in the Hugging Face cache layout, the same layout that
//! `hf_hub_download` of the Python server reads and writes:
//! `<cache>/models--<org>--<name>/refs/main` holds the commit, and the files
//! are in `<cache>/models--<org>--<name>/snapshots/<commit>/`.

use std::path::{Path, PathBuf};

use crate::{EmbedError, Result};

const MODEL_FILE: &str = "onnx/model.onnx";
const TOKENIZER_FILE: &str = "tokenizer.json";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelFiles {
    pub model: PathBuf,
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

fn cached(repo_dir: &Path) -> Option<ModelFiles> {
    let commit = std::fs::read_to_string(repo_dir.join("refs/main")).ok()?;
    let snapshot = repo_dir.join("snapshots").join(commit.trim());
    let files = ModelFiles {
        model: snapshot.join(MODEL_FILE),
        tokenizer: snapshot.join(TOKENIZER_FILE),
    };
    (files.model.is_file() && files.tokenizer.is_file()).then_some(files)
}

/// Returns the cached files of `model`. If they are not in the cache,
/// downloads them from the Hugging Face Hub (`HF_ENDPOINT`, `HF_TOKEN`).
pub async fn resolve_model_files(model: &str, cache_dir: Option<&Path>) -> Result<ModelFiles> {
    let cache = cache_dir.map_or_else(default_hub_cache, Path::to_path_buf);
    let repo = repo_id(model);
    let dir = repo_dir(&cache, repo);
    if let Some(files) = cached(&dir) {
        return Ok(files);
    }
    download(repo, &dir).await?;
    cached(&dir)
        .ok_or_else(|| EmbedError::Download(format!("{repo}: files missing after download")))
}

async fn download(repo: &str, dir: &Path) -> Result<()> {
    let endpoint = std::env::var("HF_ENDPOINT")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "https://huggingface.co".into());
    let endpoint = endpoint.trim_end_matches('/');
    let token = std::env::var("HF_TOKEN").ok().filter(|v| !v.is_empty());
    let client = reqwest::Client::new();
    let get = |url: String| {
        let mut req = client.get(url);
        if let Some(t) = &token {
            req = req.bearer_auth(t);
        }
        async move {
            req.send()
                .await
                .and_then(reqwest::Response::error_for_status)
                .map_err(|e| EmbedError::Download(e.to_string()))
        }
    };

    let info = get(format!("{endpoint}/api/models/{repo}/revision/main"))
        .await?
        .bytes()
        .await
        .map_err(|e| EmbedError::Download(e.to_string()))?;
    let commit = serde_json::from_slice::<serde_json::Value>(&info)
        .ok()
        .and_then(|v| v["sha"].as_str().map(str::to_owned))
        .filter(|s| !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric()))
        .ok_or_else(|| EmbedError::Download(format!("{repo}: no commit in the revision reply")))?;
    tracing::info!("Downloading model {repo} at commit {commit}");

    let snapshot = dir.join("snapshots").join(&commit);
    for file in [MODEL_FILE, TOKENIZER_FILE] {
        let target = snapshot.join(file);
        if target.is_file() {
            continue;
        }
        let body = get(format!("{endpoint}/{repo}/resolve/{commit}/{file}"))
            .await?
            .bytes()
            .await
            .map_err(|e| EmbedError::Download(e.to_string()))?;
        write_atomic(&target, &body).await?;
    }
    write_atomic(&dir.join("refs/main"), commit.as_bytes()).await
}

/// Writes to a temporary file, then renames, so that a stop in the middle
/// never leaves a part file at `path`.
async fn write_atomic(path: &Path, data: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let tmp = path.with_extension(format!("tmp-{}", std::process::id()));
    tokio::fs::write(&tmp, data).await?;
    tokio::fs::rename(&tmp, path).await?;
    Ok(())
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
        std::fs::create_dir_all(snap.join("onnx")).unwrap();
        std::fs::create_dir_all(dir.join("refs")).unwrap();
        std::fs::write(dir.join("refs/main"), "abc123").unwrap();
        std::fs::write(snap.join("onnx/model.onnx"), b"m").unwrap();
        assert_eq!(cached(&dir), None, "tokenizer missing");
        std::fs::write(snap.join("tokenizer.json"), b"t").unwrap();
        assert_eq!(
            cached(&dir),
            Some(ModelFiles {
                model: snap.join("onnx/model.onnx"),
                tokenizer: snap.join("tokenizer.json"),
            })
        );
        std::fs::remove_dir_all(&cache).unwrap();
    }
}
