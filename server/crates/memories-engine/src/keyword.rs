//! The keyword vectors of decision P0-2: BM25 as a Qdrant sparse vector with
//! the IDF modifier. Reference code: `parity/keyword_index/bm25_sparse.py`.

use std::collections::BTreeMap;

pub use crate::rank::scores::doc_tokens;
use crate::rank::scores::query_tokens;

pub const K1: f64 = 1.5;
pub const B: f64 = 0.75;

/// FNV-1a 32-bit hash of the UTF-8 bytes of `token`.
pub fn token_id(token: &str) -> u32 {
    token.bytes().fold(0x811C_9DC5_u32, |h, b| {
        (h ^ u32::from(b)).wrapping_mul(0x0100_0193)
    })
}

/// Counts of each token in first-seen order, then merged by index (two
/// tokens with one hash add their values), sorted by index.
fn merge(pairs: impl Iterator<Item = (u32, f64)>) -> Vec<(u32, f32)> {
    let mut out: BTreeMap<u32, f64> = BTreeMap::new();
    for (idx, v) in pairs {
        *out.entry(idx).or_insert(0.0) += v;
    }
    out.into_iter().map(|(i, v)| (i, v as f32)).collect()
}

fn counts(tokens: &[String]) -> Vec<(&str, usize)> {
    let mut seen: Vec<(&str, usize)> = Vec::new();
    for t in tokens {
        match seen.iter_mut().find(|(s, _)| s == t) {
            Some(e) => e.1 += 1,
            None => seen.push((t, 1)),
        }
    }
    seen
}

/// The document vector: the term-frequency part of BM25 Okapi.
pub fn doc_vector(text: &str, avgdl: f64) -> Vec<(u32, f32)> {
    let tokens = doc_tokens(text);
    let dl = tokens.len() as f64;
    let norm = K1 * (1.0 - B + B * dl / avgdl);
    merge(
        counts(&tokens)
            .into_iter()
            .map(|(t, tf)| (token_id(t), tf as f64 * (K1 + 1.0) / (tf as f64 + norm))),
    )
}

/// The query vector: the count of each query token (`_bm25_query_tokens`).
pub fn query_vector(query: &str) -> Vec<(u32, f32)> {
    merge(
        counts(&query_tokens(query))
            .into_iter()
            .map(|(t, n)| (token_id(t), n as f64)),
    )
}

/// The average document length of `texts` (1.0 for no text).
pub fn avgdl<'a>(texts: impl IntoIterator<Item = &'a str>) -> f64 {
    let (sum, n) = texts.into_iter().fold((0usize, 0usize), |(s, n), t| {
        (s + doc_tokens(t).len(), n + 1)
    });
    if n == 0 || sum == 0 {
        1.0
    } else {
        sum as f64 / n as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Values from parity/keyword_index/bm25_sparse.py.
    #[test]
    fn token_ids_match_python() {
        assert_eq!(token_id(""), 0x811C_9DC5);
        assert_eq!(token_id("a"), 0xE40C_292C);
        assert_eq!(token_id("foobar"), 0xBF9C_F968);
        assert_eq!(token_id("é"), 0x1E9D_E8C1);
    }

    #[test]
    fn query_vector_counts_tokens_without_stopwords() {
        let v = query_vector("What is the rate limit? rate");
        assert_eq!(
            v,
            merge([(token_id("rate"), 2.0), (token_id("limit"), 1.0)].into_iter())
        );
        // Only stopwords: all the words stay.
        assert_eq!(query_vector("what is").len(), 2);
    }

    #[test]
    fn doc_vector_matches_python() {
        // python: bm25_sparse.doc_vector("a b a".split(), 2.0)
        let v = doc_vector("A b a", 2.0);
        let norm = K1 * (1.0 - B + B * 3.0 / 2.0);
        let w = |tf: f64| (tf * (K1 + 1.0) / (tf + norm)) as f32;
        assert_eq!(
            v,
            merge(
                [
                    (token_id("a"), w(2.0) as f64),
                    (token_id("b"), w(1.0) as f64)
                ]
                .into_iter()
            )
        );
        assert_eq!(avgdl(["a b", "c"]), 1.5);
        assert_eq!(avgdl([]), 1.0);
    }
}
