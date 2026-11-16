//! The keyword (BM25) document vector of decision P0-2. The reference code is
//! `parity/keyword_index/bm25_sparse.py`. Qdrant adds the IDF part.

use std::collections::BTreeMap;

pub const K1: f64 = 1.5;
pub const B: f64 = 0.75;

/// Python `text.lower().split()`. Python `str.split()` also splits on the
/// separators U+001C to U+001F, which Rust does not count as white space.
pub fn doc_tokens(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .filter(|t| !t.is_empty())
        .map(str::to_owned)
        .collect()
}

/// FNV-1a 32-bit hash of the UTF-8 bytes.
pub fn token_id(token: &str) -> u32 {
    token.bytes().fold(0x811C_9DC5, |h: u32, b| {
        (h ^ u32::from(b)).wrapping_mul(0x0100_0193)
    })
}

/// (index, weight) pairs in index order: the term frequency part of BM25
/// Okapi. Two tokens with the same hash share one index.
pub fn doc_vector(text: &str, avgdl: f64) -> Vec<(u32, f32)> {
    let tokens = doc_tokens(text);
    let mut tf: BTreeMap<&str, f64> = BTreeMap::new();
    for t in &tokens {
        *tf.entry(t).or_default() += 1.0;
    }
    let norm = K1 * (1.0 - B + B * tokens.len() as f64 / avgdl);
    let mut out: BTreeMap<u32, f64> = BTreeMap::new();
    for (t, n) in tf {
        *out.entry(token_id(t)).or_default() += n * (K1 + 1.0) / (n + norm);
    }
    out.into_iter().map(|(i, w)| (i, w as f32)).collect()
}

/// The average token count of `texts`, as rank_bm25 calculates it. An empty
/// corpus gives 1.0, so that a later division is safe.
pub fn avgdl<'a>(texts: impl IntoIterator<Item = &'a str>) -> f64 {
    let (sum, n) = texts.into_iter().fold((0usize, 0usize), |(s, n), t| {
        (s + doc_tokens(t).len(), n + 1)
    });
    if sum == 0 { 1.0 } else { sum as f64 / n as f64 }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Values from parity/keyword_index/bm25_sparse.py (Python v5.19.0 rules).
    #[test]
    fn same_vectors_as_the_python_reference() {
        type Case<'a> = (&'a str, f64, &'a [(u32, f64)]);
        let cases: [Case; 4] = [
            (
                "Hello hello World",
                2.5,
                &[
                    (933488787, 0.9174311926605506),
                    (1335831723, 1.342281879194631),
                ],
            ),
            (
                "Ünïcode  café CAFÉ",
                3.0,
                &[(1173504043, 1.0), (2821410889, 1.4285714285714286)],
            ),
            ("", 4.0, &[]),
            (
                "a b a c a",
                1.0,
                &[
                    (3826002220, 0.8333333333333334),
                    (3859557458, 0.35714285714285715),
                    (3876335077, 0.35714285714285715),
                ],
            ),
        ];
        for (text, avg, want) in cases {
            let got = doc_vector(text, avg);
            let want: Vec<(u32, f32)> = want.iter().map(|&(i, w)| (i, w as f32)).collect();
            assert_eq!(got, want, "{text:?}");
        }
    }

    #[test]
    fn python_split_rules() {
        assert_eq!(doc_tokens(" A\u{1f}b\u{a0}c\n"), ["a", "b", "c"]);
        assert_eq!(avgdl(["a b", "c d e f"]), 3.0);
        assert_eq!(avgdl(["", ""]), 1.0);
    }
}
