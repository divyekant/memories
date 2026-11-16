//! ONNX embedding of text, with the same output as the Python `OnnxEmbedder`.
//!
//! One worker thread owns the ONNX session and the tokenizer. Callers send
//! jobs to that thread, so the model never runs on a request thread.

mod model_files;

use std::path::{Path, PathBuf};
use std::sync::{OnceLock, mpsc};
use std::thread;

use ort::environment::Environment;
use ort::session::Session;
use ort::session::builder::GraphOptimizationLevel;
use ort::value::Tensor;
use tokenizers::{PaddingParams, Tokenizer, TruncationParams};
use tokio::sync::oneshot;

pub use model_files::{ModelFiles, repo_id, resolve_model_files};

pub const DEFAULT_MODEL: &str = "all-MiniLM-L6-v2";
/// Same values as `onnx_embedder.py`.
pub const MAX_TOKENS: usize = 256;
pub const BATCH_SIZE: usize = 64;
pub const INTRA_THREADS: usize = 4;

#[derive(Debug, thiserror::Error)]
pub enum EmbedError {
    #[error("embedding provider {0:?} is not supported; use EMBED_PROVIDER=onnx")]
    UnsupportedProvider(String),
    #[error("cannot load ONNX Runtime: {0}")]
    Runtime(String),
    #[error("ONNX error: {0}")]
    Onnx(#[from] ort::Error),
    #[error("tokenizer error: {0}")]
    Tokenizer(String),
    #[error("model download failed: {0}")]
    Download(String),
    #[error("model file I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("EMBED_DIMENSION={declared} but the model {model} gives {actual}-d vectors")]
    DimensionMismatch {
        declared: usize,
        actual: usize,
        model: String,
    },
    #[error("unexpected model output shape {0:?}")]
    OutputShape(Vec<i64>),
    #[error("embedder is closed")]
    Closed,
}

pub type Result<T> = std::result::Result<T, EmbedError>;

/// Which prefix applies: `EMBED_QUERY_PREFIX` or `EMBED_DOC_PREFIX`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextKind {
    Query,
    Document,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedConfig {
    pub model: String,
    /// Hugging Face cache directory. `None` uses the Hugging Face default.
    pub cache_dir: Option<PathBuf>,
    pub query_prefix: String,
    pub document_prefix: String,
    pub declared_dim: Option<usize>,
}

impl Default for EmbedConfig {
    fn default() -> Self {
        Self {
            model: DEFAULT_MODEL.to_string(),
            cache_dir: None,
            query_prefix: String::new(),
            document_prefix: String::new(),
            declared_dim: None,
        }
    }
}

impl EmbedConfig {
    /// Reads the same variables as `EmbedderSettings.from_env()` and
    /// `MemoryEngine.__init__`. `cache_dir` is the result of
    /// [`prepare_cache_dir`], which the caller runs first.
    pub fn from_env(cache_dir: Option<PathBuf>) -> Result<Self> {
        Self::from_lookup(|k| std::env::var(k).ok(), cache_dir)
    }

    pub fn from_lookup(
        get: impl Fn(&str) -> Option<String>,
        cache_dir: Option<PathBuf>,
    ) -> Result<Self> {
        let trimmed = |k: &str| get(k).map(|v| v.trim().to_string()).unwrap_or_default();
        let provider = trimmed("EMBED_PROVIDER").to_lowercase();
        if !provider.is_empty() && provider != "onnx" {
            // shortcut: production uses onnx only (P0-3 baseline); port openai if a user needs it.
            return Err(EmbedError::UnsupportedProvider(provider));
        }
        let mut model = trimmed("EMBED_MODEL");
        if model.is_empty() {
            model = get("MODEL_NAME").unwrap_or_else(|| DEFAULT_MODEL.to_string());
        }
        // Python ignores a value that is not an integer and clamps to 1 or more.
        let declared_dim = trimmed("EMBED_DIMENSION")
            .parse::<i64>()
            .ok()
            .map(|d| d.max(1) as usize);
        Ok(Self {
            model,
            cache_dir,
            // Not trimmed, same as Python.
            query_prefix: get("EMBED_QUERY_PREFIX").unwrap_or_default(),
            document_prefix: get("EMBED_DOC_PREFIX").unwrap_or_default(),
            declared_dim,
        })
    }

    fn prefix(&self, kind: TextKind) -> &str {
        match kind {
            TextKind::Query => &self.query_prefix,
            TextKind::Document => &self.document_prefix,
        }
    }
}

/// Same logic as `MemoryEngine.__init__`: make `MODEL_CACHE_DIR`, and if it is
/// empty, copy `PRELOADED_MODEL_CACHE_DIR` into it. Returns the cache directory
/// for the embedder (`None` if `MODEL_CACHE_DIR` is not set).
pub fn prepare_cache_dir(model_cache: &str, preloaded: &str) -> Result<Option<PathBuf>> {
    let model_cache = model_cache.trim();
    if model_cache.is_empty() {
        return Ok(None);
    }
    let cache = PathBuf::from(model_cache);
    std::fs::create_dir_all(&cache)?;
    let preloaded = preloaded.trim();
    if !preloaded.is_empty() {
        let preload = Path::new(preloaded);
        let cache_empty = std::fs::read_dir(&cache).map(|mut d| d.next().is_none());
        let preload_has_files = std::fs::read_dir(preload).map(|mut d| d.next().is_some());
        if let (Ok(true), Ok(true)) = (cache_empty, preload_has_files) {
            match model_files::copy_tree(preload, &cache) {
                Ok(()) => tracing::info!(
                    "Seeded model cache from preloaded assets: {} -> {}",
                    preload.display(),
                    cache.display()
                ),
                Err(e) => {
                    tracing::warn!("Failed to seed model cache from {}: {e}", preload.display())
                }
            }
        }
    }
    Ok(Some(cache))
}

type Reply = oneshot::Sender<Result<Vec<Vec<f32>>>>;

/// Handle to the embedding worker. Cheap to clone. The worker stops when the
/// last handle is dropped.
#[derive(Clone)]
pub struct Embedder {
    jobs: mpsc::Sender<(Vec<String>, Reply)>,
    config: std::sync::Arc<EmbedConfig>,
    dim: usize,
}

impl Embedder {
    /// Finds or downloads the model files, then starts the worker thread.
    pub async fn load(config: EmbedConfig) -> Result<Self> {
        let files = resolve_model_files(&config.model, config.cache_dir.as_deref()).await?;
        let (jobs, rx) = mpsc::channel::<(Vec<String>, Reply)>();
        let (ready_tx, ready_rx) = oneshot::channel();
        thread::Builder::new()
            .name("memories-embed".into())
            .spawn(move || {
                let mut model = match Model::open(&files) {
                    Ok(m) => m,
                    Err(e) => {
                        let _ = ready_tx.send(Err(e));
                        return;
                    }
                };
                // Same as Python `_get_dim`: one test encode.
                let dim = model.encode(&["test".to_string()]).map(|v| v[0].len());
                if ready_tx.send(dim).is_err() {
                    return;
                }
                while let Ok((texts, reply)) = rx.recv() {
                    // The caller has gone (timeout or disconnect): skip the work.
                    if !reply.is_closed() {
                        let _ = reply.send(model.encode(&texts));
                    }
                }
            })?;
        let dim = ready_rx.await.map_err(|_| EmbedError::Closed)??;
        if let Some(declared) = config.declared_dim
            && declared != dim
        {
            return Err(EmbedError::DimensionMismatch {
                declared,
                actual: dim,
                model: config.model.clone(),
            });
        }
        tracing::info!("ONNX embedder loaded: model={}, dim={dim}", config.model);
        Ok(Self {
            jobs,
            config: std::sync::Arc::new(config),
            dim,
        })
    }

    pub fn dim(&self) -> usize {
        self.dim
    }

    pub fn config(&self) -> &EmbedConfig {
        &self.config
    }

    /// L2-normalized vectors, one per text, in input order. Adds the prefix
    /// of `kind` first.
    pub async fn embed(&self, texts: Vec<String>, kind: TextKind) -> Result<Vec<Vec<f32>>> {
        let prefix = self.config.prefix(kind);
        let texts = if prefix.is_empty() {
            texts
        } else {
            texts.into_iter().map(|t| format!("{prefix}{t}")).collect()
        };
        let (tx, rx) = oneshot::channel();
        self.jobs
            .send((texts, tx))
            .map_err(|_| EmbedError::Closed)?;
        rx.await.map_err(|_| EmbedError::Closed)?
    }
}

/// ONNX Runtime allows one environment per process. It is made once, so a
/// reload of the embedder makes a new session in the same environment.
fn environment() -> Result<&'static Environment> {
    static ENV: OnceLock<std::result::Result<Environment, String>> = OnceLock::new();
    ENV.get_or_init(|| {
        let path = std::env::var("ORT_DYLIB_PATH")
            .ok()
            .filter(|p| !p.is_empty())
            .unwrap_or_else(|| {
                format!(
                    "{}onnxruntime{}",
                    std::env::consts::DLL_PREFIX,
                    std::env::consts::DLL_SUFFIX
                )
            });
        ort::init_from(&path)
            .map_err(|e| e.to_string())?
            .with_name("memories")
            .build()
            .map_err(|e| e.to_string())
    })
    .as_ref()
    .map_err(|e| EmbedError::Runtime(e.clone()))
}

struct Model {
    session: Session,
    tokenizer: Tokenizer,
    token_type_ids: bool,
}

impl Model {
    fn open(files: &ModelFiles) -> Result<Self> {
        let session = Session::builder(environment()?)?
            .with_optimization_level(GraphOptimizationLevel::All)
            .map_err(ort::Error::from)?
            .with_intra_threads(INTRA_THREADS)
            .map_err(ort::Error::from)?
            // The CPU arena keeps the peak of each batch (1.27 GB after a batch of
            // 64 long texts). Without it, memory goes back after the batch.
            .with_execution_providers([ort::ep::CPU::default().with_arena_allocator(false).build()])
            .map_err(ort::Error::from)?
            .commit_from_file(&files.model)?;
        let mut tokenizer = Tokenizer::from_file(&files.tokenizer)
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        tokenizer.with_padding(Some(PaddingParams::default()));
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: MAX_TOKENS,
                ..Default::default()
            }))
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        let token_type_ids = session
            .inputs()
            .iter()
            .any(|i| i.name() == "token_type_ids");
        Ok(Self {
            session,
            tokenizer,
            token_type_ids,
        })
    }

    fn encode(&mut self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        let mut out = Vec::with_capacity(texts.len());
        for batch in texts.chunks(BATCH_SIZE) {
            self.encode_batch(batch, &mut out)?;
        }
        Ok(out)
    }

    fn encode_batch(&mut self, batch: &[String], out: &mut Vec<Vec<f32>>) -> Result<()> {
        let encodings = self
            .tokenizer
            .encode_batch(batch.to_vec(), true)
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        let rows = encodings.len();
        let seq = encodings.first().map_or(0, |e| e.get_ids().len());
        let mut ids = Vec::with_capacity(rows * seq);
        let mut mask = Vec::with_capacity(rows * seq);
        for e in &encodings {
            ids.extend(e.get_ids().iter().map(|&v| i64::from(v)));
            mask.extend(e.get_attention_mask().iter().map(|&v| i64::from(v)));
        }
        if ids.len() != rows * seq {
            return Err(EmbedError::Tokenizer(
                "batch is not padded to one length".into(),
            ));
        }
        let shape = [rows, seq];
        let mut inputs = ort::inputs![
            "input_ids" => Tensor::from_array((shape, ids))?,
            "attention_mask" => Tensor::from_array((shape, mask.clone()))?, // mask is read again for the pool
        ];
        if self.token_type_ids {
            inputs.push((
                "token_type_ids".into(),
                Tensor::from_array((shape, vec![0i64; rows * seq]))?.into(),
            ));
        }
        let outputs = self.session.run(inputs)?;
        let (shape, hidden) = outputs[0].try_extract_tensor::<f32>()?;
        let dims: Vec<i64> = shape.iter().copied().collect();
        if dims.len() != 3 || dims[0] as usize != rows || dims[1] as usize != seq || dims[2] <= 0 {
            return Err(EmbedError::OutputShape(dims));
        }
        let dim = dims[2] as usize;
        for r in 0..rows {
            out.push(mean_pool_normalize(
                (0..seq).map(|c| {
                    let at = (r * seq + c) * dim;
                    (mask[r * seq + c] as f32, &hidden[at..at + dim])
                }),
                dim,
            ));
        }
        Ok(())
    }
}

/// Mean pool over the tokens with the attention mask, then L2 normalize.
/// Both divisors are clamped to 1e-9, as in Python.
fn mean_pool_normalize<'a>(tokens: impl Iterator<Item = (f32, &'a [f32])>, dim: usize) -> Vec<f32> {
    let mut sum = vec![0f32; dim];
    let mut count = 0f32;
    for (m, row) in tokens {
        count += m;
        for (s, &v) in sum.iter_mut().zip(row.iter()) {
            *s += v * m;
        }
    }
    let count = count.max(1e-9);
    sum.iter_mut().for_each(|s| *s /= count);
    let norm = sum.iter().map(|v| v * v).sum::<f32>().sqrt().max(1e-9);
    sum.iter_mut().for_each(|s| *s /= norm);
    sum
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn config(vars: &[(&str, &str)]) -> Result<EmbedConfig> {
        let map: HashMap<String, String> = vars
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        EmbedConfig::from_lookup(|k| map.get(k).cloned(), None)
    }

    #[test]
    fn config_defaults_match_python() {
        assert_eq!(config(&[]).unwrap(), EmbedConfig::default());
    }

    #[test]
    fn config_reads_model_prefixes_and_dimension() {
        let c = config(&[
            ("MODEL_NAME", "other-model"),
            ("EMBED_QUERY_PREFIX", "search_query: "),
            ("EMBED_DOC_PREFIX", "search_document: "),
            ("EMBED_DIMENSION", " 384 "),
        ])
        .unwrap();
        assert_eq!(c.model, "other-model");
        assert_eq!(c.prefix(TextKind::Query), "search_query: ");
        assert_eq!(c.prefix(TextKind::Document), "search_document: ");
        assert_eq!(c.declared_dim, Some(384));
        let c = config(&[("MODEL_NAME", "a"), ("EMBED_MODEL", " b ")]).unwrap();
        assert_eq!(c.model, "b");
        assert_eq!(
            config(&[("EMBED_DIMENSION", "x")]).unwrap().declared_dim,
            None
        );
        assert_eq!(
            config(&[("EMBED_DIMENSION", "-5")]).unwrap().declared_dim,
            Some(1)
        );
    }

    #[test]
    fn config_refuses_other_providers() {
        assert!(config(&[("EMBED_PROVIDER", " ONNX ")]).is_ok());
        assert!(matches!(
            config(&[("EMBED_PROVIDER", "openai")]),
            Err(EmbedError::UnsupportedProvider(p)) if p == "openai"
        ));
    }

    #[test]
    fn mean_pool_ignores_masked_tokens_and_normalizes() {
        let v = mean_pool_normalize(
            [(1.0, &[3.0f32, 4.0][..]), (0.0, &[100.0, 100.0][..])].into_iter(),
            2,
        );
        assert!((v[0] - 0.6).abs() < 1e-6 && (v[1] - 0.8).abs() < 1e-6);
        assert_eq!(
            mean_pool_normalize([(0.0, &[0.0f32, 0.0][..])].into_iter(), 2),
            vec![0.0, 0.0]
        );
    }

    #[test]
    fn prepare_cache_dir_seeds_an_empty_cache_only() {
        let root =
            std::env::temp_dir().join(format!("memories-embed-cache-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let preload = root.join("preload");
        std::fs::create_dir_all(preload.join("models--x/snapshots/abc")).unwrap();
        std::fs::write(preload.join("models--x/snapshots/abc/f"), b"1").unwrap();
        let cache = root.join("cache");

        assert_eq!(
            prepare_cache_dir(" ", preload.to_str().unwrap()).unwrap(),
            None
        );
        let got = prepare_cache_dir(cache.to_str().unwrap(), preload.to_str().unwrap()).unwrap();
        assert_eq!(got, Some(cache.clone()));
        assert_eq!(
            std::fs::read(cache.join("models--x/snapshots/abc/f")).unwrap(),
            b"1"
        );

        std::fs::write(preload.join("models--x/snapshots/abc/f"), b"2").unwrap();
        prepare_cache_dir(cache.to_str().unwrap(), preload.to_str().unwrap()).unwrap();
        assert_eq!(
            std::fs::read(cache.join("models--x/snapshots/abc/f")).unwrap(),
            b"1"
        );
        std::fs::remove_dir_all(&root).unwrap();
    }
}
