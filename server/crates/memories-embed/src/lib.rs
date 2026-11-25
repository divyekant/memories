//! Embedding of text, with the same output as the Python `OnnxEmbedder`.
//!
//! The model runs on the encoder of this crate (`encoder.rs`), not on ONNX
//! Runtime. Decision record: `docs/decisions/2026-11-24-own-encoder.md`.
//!
//! One worker thread owns the model and the tokenizer. Callers send jobs to
//! that thread, so the model never runs on a request thread. The worker
//! splits each job over the threads of `pool.rs`.

mod encoder;
mod kernel;
mod model_files;
mod pool;
mod weights;

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;

use tokenizers::{Tokenizer, TruncationParams};
use tokio::sync::oneshot;

use encoder::Encoder;

pub use model_files::{ModelFiles, repo_id, resolve_model_files};

pub const DEFAULT_MODEL: &str = "all-MiniLM-L6-v2";
/// Same value as `onnx_embedder.py`.
pub const MAX_TOKENS: usize = 256;
/// The largest number of threads for one job.
pub const INTRA_THREADS: usize = 4;

#[derive(Debug, thiserror::Error)]
pub enum EmbedError {
    #[error(
        "EMBED_PROVIDER={0} is not supported: this server has only the onnx embedder; set EMBED_PROVIDER=onnx"
    )]
    UnsupportedProvider(String),
    #[error("model file error: {0}")]
    Model(String),
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
    #[error("embedder internal error: {0}")]
    Internal(String),
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
            // shortcut: production uses onnx only (P0-3 baseline, P5-7); port openai_embedder.py if a host needs it.
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
                let model = match contain(|| Model::open(&files)) {
                    Ok(m) => m,
                    Err(e) => {
                        let _ = ready_tx.send(Err(e));
                        return;
                    }
                };
                // Same as Python `_get_dim`: one test encode.
                let dim = contain(|| model.encode(&["test".to_string()])).map(|v| v[0].len());
                if ready_tx.send(dim).is_err() {
                    return;
                }
                while let Ok((texts, reply)) = rx.recv() {
                    // The caller has gone (timeout or disconnect): skip the work.
                    if !reply.is_closed() {
                        // A panic of the encoder is an error for this job, and the worker continues.
                        let _ = reply.send(contain(|| model.encode(&texts)));
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
        tracing::info!(
            "Embedder loaded: model={}, dim={dim}, kernels={:?}, threads={}",
            config.model,
            kernel::Simd::detect(),
            pool::global().threads()
        );
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

struct Model {
    encoder: Encoder,
    tokenizer: Tokenizer,
}

impl Model {
    fn open(files: &ModelFiles) -> Result<Self> {
        Self::open_with(files, kernel::Simd::detect())
    }

    fn open_with(files: &ModelFiles, simd: kernel::Simd) -> Result<Self> {
        let encoder = Encoder::load(files, simd)?;
        let mut tokenizer = Tokenizer::from_file(&files.tokenizer)
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        // Each text runs at its own length. `tokenizer.json` can ask for a fixed length.
        tokenizer.with_padding(None);
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: MAX_TOKENS.min(encoder.max_positions()),
                ..Default::default()
            }))
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        let known = tokenizer.get_vocab_size(true);
        if known > encoder.vocab() {
            return Err(EmbedError::Model(format!(
                "the tokenizer has {known} tokens, but the model has {}; the two files are not from the same model",
                encoder.vocab()
            )));
        }
        Ok(Self { encoder, tokenizer })
    }

    /// One text alone uses the pool for its steps. In a list, each pool
    /// thread takes one text at a time and runs it with no more threads.
    fn encode(&self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        let pool = pool::global();
        match texts {
            [text] => Ok(vec![self.encode_one(Some(pool), text)?]),
            _ => pool
                .map(texts.len(), |i| self.encode_one(None, &texts[i]))
                .into_iter()
                .collect(),
        }
    }

    fn encode_one(&self, pool: Option<&pool::Pool>, text: &str) -> Result<Vec<f32>> {
        let encoding = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| EmbedError::Tokenizer(e.to_string()))?;
        let dim = self.encoder.dim();
        let hidden = self.encoder.hidden_states(pool, encoding.get_ids())?;
        Ok(mean_pool_normalize(
            encoding
                .get_attention_mask()
                .iter()
                .map(|m| *m as f32)
                .zip(hidden.chunks_exact(dim)),
            dim,
        ))
    }
}

/// Runs `job`. A panic in it becomes [`EmbedError::Internal`].
fn contain<T>(job: impl FnOnce() -> Result<T>) -> Result<T> {
    catch_unwind(AssertUnwindSafe(job)).unwrap_or_else(|panic| {
        let message = panic
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| panic.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "panic".into());
        tracing::error!("the embedder panicked: {message}");
        Err(EmbedError::Internal(message))
    })
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
        assert_eq!(
            config(&[("EMBED_PROVIDER", "openai")])
                .unwrap_err()
                .to_string(),
            "EMBED_PROVIDER=openai is not supported: this server has only the onnx embedder; set EMBED_PROVIDER=onnx"
        );
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

    /// A WordPiece tokenizer for the small model of the encoder tests. It
    /// asks for a fixed length of 16, which the embedder must ignore.
    fn tiny_tokenizer(extra_tokens: usize) -> String {
        let mut vocab = serde_json::Map::new();
        for (id, token) in [
            "[PAD]", "[UNK]", "[CLS]", "[SEP]", "alpha", "beta", "gamma", "delta",
        ]
        .into_iter()
        .map(String::from)
        .chain((0..extra_tokens).map(|i| format!("extra{i}")))
        .enumerate()
        {
            vocab.insert(token, id.into());
        }
        serde_json::json!({
            "version": "1.0",
            "truncation": null,
            "padding": {"strategy": {"Fixed": 16}, "direction": "Right", "pad_to_multiple_of": null,
                "pad_id": 0, "pad_type_id": 0, "pad_token": "[PAD]"},
            "added_tokens": [],
            "normalizer": {"type": "Lowercase"},
            "pre_tokenizer": {"type": "Whitespace"},
            "post_processor": {"type": "BertProcessing", "sep": ["[SEP]", 3], "cls": ["[CLS]", 2]},
            "decoder": null,
            "model": {"type": "WordPiece", "unk_token": "[UNK]", "continuing_subword_prefix": "##",
                "max_input_chars_per_word": 100, "vocab": vocab},
        })
        .to_string()
    }

    /// A model cache in the Hugging Face layout with the small model as `tiny/model`.
    fn tiny_cache(name: &str, extra_tokens: usize) -> PathBuf {
        let cache = weights::tests::temp_dir(name);
        let snapshot = cache.join("models--tiny--model/snapshots/abc");
        std::fs::create_dir_all(&snapshot).unwrap();
        std::fs::create_dir_all(cache.join("models--tiny--model/refs")).unwrap();
        std::fs::write(cache.join("models--tiny--model/refs/main"), "abc").unwrap();
        let files = encoder::tests::write_tiny(&snapshot, &encoder::tests::tiny_tensors(""));
        std::fs::write(files.tokenizer, tiny_tokenizer(extra_tokens)).unwrap();
        cache
    }

    fn tiny_config(cache: &Path) -> EmbedConfig {
        EmbedConfig {
            model: "tiny/model".into(),
            cache_dir: Some(cache.to_path_buf()),
            ..EmbedConfig::default()
        }
    }

    #[tokio::test]
    async fn embedder_gives_the_same_vector_alone_and_in_a_list() {
        let cache = tiny_cache("embedder", 0);
        let embedder = Embedder::load(tiny_config(&cache)).await.unwrap();
        assert_eq!(embedder.dim(), encoder::tests::HIDDEN);

        // 200 words: more than the 70 positions of the model, so it is cut.
        let long = "alpha beta ".repeat(100);
        let texts: Vec<String> = ["alpha", "beta gamma delta", "", "unknown words here", &long]
            .into_iter()
            .chain(std::iter::repeat_n("gamma alpha", 40))
            .map(String::from)
            .collect();
        let list = embedder
            .embed(texts.clone(), TextKind::Document)
            .await
            .unwrap();
        assert_eq!(list.len(), texts.len());
        for (text, in_list) in texts.iter().zip(&list).take(6) {
            let norm = in_list.iter().map(|v| v * v).sum::<f32>().sqrt();
            assert!((norm - 1.0).abs() < 1e-5, "{text:?}: norm {norm}");
            // The same arithmetic with threads in the text or threads over the texts.
            let alone = embedder
                .embed(vec![text.clone()], TextKind::Query)
                .await
                .unwrap();
            assert!(
                &alone[0] == in_list,
                "{text:?}: the vector changed in a list"
            );
        }
        assert!(list[0] != list[1] && list[5] == list[44]);
        assert!(
            embedder
                .embed(vec![], TextKind::Query)
                .await
                .unwrap()
                .is_empty()
        );

        // Many callers at the same time share the one worker.
        let jobs: Vec<_> = (0..8)
            .map(|_| {
                let e = embedder.clone();
                tokio::spawn(async move {
                    e.embed(vec!["beta gamma delta".into()], TextKind::Query)
                        .await
                })
            })
            .collect();
        for job in jobs {
            assert!(job.await.unwrap().unwrap()[0] == list[1]);
        }

        let wrong = EmbedConfig {
            declared_dim: Some(384),
            ..tiny_config(&cache)
        };
        assert!(matches!(
            Embedder::load(wrong).await,
            Err(EmbedError::DimensionMismatch {
                declared: 384,
                actual: 104,
                ..
            })
        ));
        std::fs::remove_dir_all(&cache).unwrap();
    }

    #[tokio::test]
    async fn embedder_refuses_a_tokenizer_of_another_model() {
        // 8 + 43 tokens, and the model has 50.
        let cache = tiny_cache("embedder-vocab", 43);
        let err = Embedder::load(tiny_config(&cache))
            .await
            .err()
            .unwrap()
            .to_string();
        assert!(
            err.contains("the tokenizer has 51 tokens, but the model has 50"),
            "{err}"
        );
        std::fs::remove_dir_all(&cache).unwrap();
    }

    /// Gate G2 for each kernel family that this CPU can run, not only for
    /// the fastest one. Run: `cargo test -p memories-embed --lib -- --ignored --nocapture`
    #[tokio::test]
    #[ignore = "needs the model"]
    async fn g2_holds_on_each_cpu_path() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
        let fixture: serde_json::Value =
            serde_json::from_slice(&std::fs::read(dir.join("embed_texts.json")).unwrap()).unwrap();
        let texts: Vec<String> = fixture["texts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t.as_str().unwrap().to_owned())
            .collect();
        let raw = std::fs::read(dir.join("embed_vectors.f32")).unwrap();
        let want: Vec<f32> = raw
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| f32::from_le_bytes(*b))
            .collect();
        let cache = std::env::var("MEMORIES_TEST_MODEL_CACHE")
            .ok()
            .map(PathBuf::from);
        let files = resolve_model_files(DEFAULT_MODEL, cache.as_deref())
            .await
            .unwrap();
        for simd in kernel::Simd::available() {
            let model = Model::open_with(&files, simd).unwrap();
            let start = std::time::Instant::now();
            let got = model.encode(&texts).unwrap();
            let seconds = start.elapsed().as_secs_f64();
            let mut times: Vec<f64> = texts
                .iter()
                .take(100)
                .map(|text| {
                    let query = text
                        .split_whitespace()
                        .take(12)
                        .collect::<Vec<_>>()
                        .join(" ");
                    let start = std::time::Instant::now();
                    model.encode(&[query]).unwrap();
                    start.elapsed().as_secs_f64() * 1000.0
                })
                .collect();
            times.sort_by(f64::total_cmp);
            let lowest = got
                .iter()
                .zip(want.as_chunks::<384>().0)
                .map(|(g, w)| g.iter().zip(w).map(|(a, b)| a * b).sum::<f32>())
                .fold(1.0f32, f32::min);
            println!(
                "{simd:?}: lowest cosine {lowest:.7}, {} texts in {seconds:.1} s, query p50 {:.2} ms",
                texts.len(),
                times[50]
            );
            assert!(lowest >= 0.9999, "{simd:?}: cosine {lowest}");
        }
    }

    #[test]
    fn a_panic_in_a_job_becomes_an_error() {
        assert_eq!(contain(|| Ok(1)).unwrap(), 1);
        let err = contain::<()>(|| panic!("index {} out of range", 7)).unwrap_err();
        assert_eq!(
            err.to_string(),
            "embedder internal error: index 7 out of range"
        );
        assert!(
            matches!(contain::<()>(|| panic!("fixed")), Err(EmbedError::Internal(m)) if m == "fixed")
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
