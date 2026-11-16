//! Gate G2: the Rust embedder gives the vectors of the Python reference
//! (v5.19.0) for 1,000 fixed texts, with a cosine similarity of 0.9999 or more.
//!
//! These tests need ONNX Runtime (`ORT_DYLIB_PATH`) and the model (in the
//! cache, or a download). Run them with:
//! `cargo test --locked -p memories-embed --test parity -- --ignored`

use std::path::PathBuf;

use memories_embed::{EmbedConfig, Embedder, TextKind};

const MIN_COSINE: f32 = 0.9999;

fn fixture_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn config() -> EmbedConfig {
    EmbedConfig {
        cache_dir: std::env::var("MEMORIES_TEST_MODEL_CACHE")
            .ok()
            .map(PathBuf::from),
        ..EmbedConfig::default()
    }
}

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let nb = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    dot / (na * nb)
}

#[tokio::test]
#[ignore = "needs ONNX Runtime and the model"]
async fn g2_vectors_match_python_reference() {
    let fixture: serde_json::Value =
        serde_json::from_slice(&std::fs::read(fixture_dir().join("embed_texts.json")).unwrap())
            .unwrap();
    let texts: Vec<String> = fixture["texts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t.as_str().unwrap().to_owned())
        .collect();
    let dim = fixture["dim"].as_u64().unwrap() as usize;
    let raw = std::fs::read(fixture_dir().join("embed_vectors.f32")).unwrap();
    let expected: Vec<f32> = raw
        .as_chunks::<4>()
        .0
        .iter()
        .map(|b| f32::from_le_bytes(*b))
        .collect();
    assert_eq!(texts.len(), 1000);
    assert_eq!(expected.len(), texts.len() * dim);

    let embedder = Embedder::load(config()).await.unwrap();
    assert_eq!(embedder.dim(), dim);
    let got = embedder
        .embed(texts.clone(), TextKind::Document)
        .await
        .unwrap();
    assert_eq!(got.len(), texts.len());

    let mut worst = (1.0f32, 0usize);
    for (i, v) in got.iter().enumerate() {
        let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-4, "text {i}: norm {norm}");
        let c = cosine(v, &expected[i * dim..(i + 1) * dim]);
        if c < worst.0 {
            worst = (c, i);
        }
    }
    println!(
        "G2: {} texts, lowest cosine {:.7} (text {})",
        texts.len(),
        worst.0,
        worst.1
    );
    assert!(
        worst.0 >= MIN_COSINE,
        "text {}: cosine {} < {MIN_COSINE}",
        worst.1,
        worst.0
    );
}

#[tokio::test]
#[ignore = "needs ONNX Runtime and the model"]
async fn prefixes_batches_and_reload() {
    let embedder = Embedder::load(EmbedConfig {
        query_prefix: "search_query: ".into(),
        document_prefix: "search_document: ".into(),
        ..config()
    })
    .await
    .unwrap();
    let plain = Embedder::load(config()).await.unwrap();

    let q = embedder
        .embed(vec!["rust".into()], TextKind::Query)
        .await
        .unwrap();
    let d = embedder
        .embed(vec!["rust".into()], TextKind::Document)
        .await
        .unwrap();
    let q_plain = plain
        .embed(vec!["search_query: rust".into()], TextKind::Query)
        .await
        .unwrap();
    let d_plain = plain
        .embed(vec!["search_document: rust".into()], TextKind::Document)
        .await
        .unwrap();
    assert!(cosine(&q[0], &q_plain[0]) >= MIN_COSINE);
    assert!(cosine(&d[0], &d_plain[0]) >= MIN_COSINE);
    assert!(
        cosine(&q[0], &d[0]) < 0.999,
        "the prefixes must change the vector"
    );

    // Padding to the longest text of a batch must not change a vector.
    let long = "word ".repeat(300);
    let batch = plain
        .embed(
            vec!["short".into(), long.clone(), String::new()],
            TextKind::Document,
        )
        .await
        .unwrap();
    let alone = plain
        .embed(vec!["short".into()], TextKind::Document)
        .await
        .unwrap();
    assert!(cosine(&batch[0], &alone[0]) >= MIN_COSINE);
    assert_eq!(batch.len(), 3);
    assert!(
        plain
            .embed(vec![], TextKind::Query)
            .await
            .unwrap()
            .is_empty()
    );

    // Many callers at the same time share the one worker.
    let jobs: Vec<_> = (0..8)
        .map(|i| {
            let e = plain.clone();
            tokio::spawn(async move { e.embed(vec![format!("text {i}")], TextKind::Query).await })
        })
        .collect();
    for j in jobs {
        assert_eq!(j.await.unwrap().unwrap().len(), 1);
    }
}
