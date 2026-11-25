//! A BERT encoder (the model family of `all-MiniLM-L6-v2`) in plain Rust on
//! the kernels of [`crate::kernel`]. It replaces ONNX Runtime.
//!
//! Each text runs at its own length: there is no padding. One text alone
//! splits its steps over the pool threads. In a list of texts, each thread
//! takes one text (see `Model::encode` in `lib.rs`).

use crate::kernel::{self, Buf, Dense, P, Simd};
use crate::model_files::ModelFiles;
use crate::pool::Pool;
use crate::weights::{BertConfig, SafeTensors};
use crate::{EmbedError, Result};

struct Norm {
    gain: Vec<f32>,
    bias: Vec<f32>,
    eps: f32,
}

impl Norm {
    /// Layer normalization of each row of `x`.
    fn apply(&self, x: &mut [f32]) {
        let width = self.gain.len();
        for row in x.chunks_exact_mut(width) {
            let mean = row.iter().sum::<f32>() / width as f32;
            let var = row.iter().map(|v| (v - mean) * (v - mean)).sum::<f32>() / width as f32;
            let inv = 1.0 / (var + self.eps).sqrt();
            for ((v, g), b) in row.iter_mut().zip(&self.gain).zip(&self.bias) {
                *v = (*v - mean) * inv * g + b;
            }
        }
    }
}

struct Layer {
    query: Dense,
    key: Dense,
    value: Dense,
    attention_out: Dense,
    attention_norm: Norm,
    intermediate: Dense,
    out: Dense,
    out_norm: Norm,
}

pub struct Encoder {
    hidden: usize,
    heads: usize,
    max_positions: usize,
    /// `[vocab, hidden]`.
    word: Vec<f32>,
    /// `[max_positions, hidden]`: position plus token type 0.
    position: Vec<f32>,
    norm: Norm,
    layers: Vec<Layer>,
    simd: Simd,
}

/// GELU, `0.5 * x * (1 + erf(x / sqrt(2)))`, with the erf of Abramowitz and
/// Stegun 7.1.28 (largest error 3e-7). The formula has no branch and no call,
/// so the compiler makes SIMD code of a loop over it. The erf with `exp`
/// (7.1.26) took about 15% of the time of a list of texts.
fn gelu(x: f32) -> f32 {
    let z = (x * std::f32::consts::FRAC_1_SQRT_2).abs();
    let p = 1.0
        + z * (0.070_523_08
            + z * (0.042_282_01
                + z * (0.009_270_527
                    + z * (0.000_152_014_3 + z * (0.000_276_567_2 + z * 0.000_043_063_8)))));
    let p2 = p * p;
    let p4 = p2 * p2;
    let p8 = p4 * p4;
    let erf = (1.0 - 1.0 / (p8 * p8)).copysign(x);
    0.5 * x * (1.0 + erf)
}

fn par_map<T: Send>(pool: Option<&Pool>, n: usize, f: impl Fn(usize) -> T + Sync) -> Vec<T> {
    match pool {
        Some(pool) => pool.map(n, f),
        None => (0..n).map(f).collect(),
    }
}

impl Encoder {
    /// Reads `config.json` and `model.safetensors`, and packs the weights for
    /// the kernels of `simd`.
    pub fn load(files: &ModelFiles, simd: Simd) -> Result<Self> {
        let cfg = BertConfig::parse(&std::fs::read(&files.config)?)?;
        let mut file = SafeTensors::open(&files.model)?;
        let h = cfg.hidden;
        let norm = |file: &mut SafeTensors, name: &str| -> Result<Norm> {
            Ok(Norm {
                gain: file.f32(&format!("{name}.weight"), &[h])?,
                bias: file.f32(&format!("{name}.bias"), &[h])?,
                eps: cfg.layer_norm_eps,
            })
        };
        let dense = |file: &mut SafeTensors, name: &str, out: usize, inp: usize| -> Result<Dense> {
            let weight = file.f32(&format!("{name}.weight"), &[out, inp])?;
            let bias = file.f32(&format!("{name}.bias"), &[out])?;
            Ok(Dense::pack(simd, &weight, &bias, inp))
        };

        let word = file.f32("embeddings.word_embeddings.weight", &[cfg.vocab, h])?;
        let mut position = file.f32(
            "embeddings.position_embeddings.weight",
            &[cfg.max_positions, h],
        )?;
        let token_type = file.f32(
            "embeddings.token_type_embeddings.weight",
            &[cfg.type_vocab, h],
        )?;
        // Each token has type 0 (one text, no text pair).
        for row in position.chunks_exact_mut(h) {
            for (p, t) in row.iter_mut().zip(&token_type[..h]) {
                *p += t;
            }
        }
        let embedding_norm = norm(&mut file, "embeddings.LayerNorm")?;
        // Not `with_capacity`: `cfg.layers` is a value of the file, and a wrong
        // one stops the loop at the first tensor that the file does not have.
        let mut layers = Vec::new();
        for i in 0..cfg.layers {
            let at = |part: &str| format!("encoder.layer.{i}.{part}");
            layers.push(Layer {
                query: dense(&mut file, &at("attention.self.query"), h, h)?,
                key: dense(&mut file, &at("attention.self.key"), h, h)?,
                value: dense(&mut file, &at("attention.self.value"), h, h)?,
                attention_out: dense(&mut file, &at("attention.output.dense"), h, h)?,
                attention_norm: norm(&mut file, &at("attention.output.LayerNorm"))?,
                intermediate: dense(&mut file, &at("intermediate.dense"), cfg.intermediate, h)?,
                out: dense(&mut file, &at("output.dense"), h, cfg.intermediate)?,
                out_norm: norm(&mut file, &at("output.LayerNorm"))?,
            });
        }
        Ok(Self {
            hidden: h,
            heads: cfg.heads,
            max_positions: cfg.max_positions,
            word,
            position,
            norm: embedding_norm,
            layers,
            simd,
        })
    }

    /// Width of one output vector.
    pub fn dim(&self) -> usize {
        self.hidden
    }

    /// The largest number of tokens in one text.
    pub fn max_positions(&self) -> usize {
        self.max_positions
    }

    /// Number of token ids that the model knows.
    pub fn vocab(&self) -> usize {
        self.word.len() / self.hidden
    }

    /// The last hidden state of one text, `[ids.len(), dim]`. With a pool,
    /// the steps run on the pool threads.
    pub fn hidden_states(&self, pool: Option<&Pool>, ids: &[u32]) -> Result<Vec<f32>> {
        let (h, seq) = (self.hidden, ids.len());
        if seq == 0 {
            return Ok(Vec::new());
        }
        if seq > self.max_positions {
            return Err(EmbedError::Model(format!(
                "a text has {seq} tokens; the model permits {}",
                self.max_positions
            )));
        }
        let mut x = vec![0f32; seq * h];
        for ((&id, row), position) in ids
            .iter()
            .zip(x.chunks_exact_mut(h))
            .zip(self.position.chunks_exact(h))
        {
            let word = (id as usize)
                .checked_mul(h)
                .and_then(|at| self.word.get(at..at + h))
                .ok_or_else(|| {
                    EmbedError::Model(format!(
                        "token id {id} is not in the model (vocabulary of {})",
                        self.vocab()
                    ))
                })?;
            for ((x, w), p) in row.iter_mut().zip(word).zip(position) {
                *x = w + p;
            }
        }
        self.norm.apply(&mut x);
        for layer in &self.layers {
            let [query, key, value] = linear(
                pool,
                &x,
                seq,
                [&layer.query, &layer.key, &layer.value],
                false,
            );
            let context = self.attention(pool, &query, &key, &value, seq);
            let [attended] = linear(pool, &context, seq, [&layer.attention_out], false);
            x.iter_mut().zip(&attended).for_each(|(x, a)| *x += a);
            layer.attention_norm.apply(&mut x);
            let [wide] = linear(pool, &x, seq, [&layer.intermediate], true);
            let [narrow] = linear(pool, &wide, seq, [&layer.out], false);
            x.iter_mut().zip(&narrow).for_each(|(x, a)| *x += a);
            layer.out_norm.apply(&mut x);
        }
        Ok(x)
    }

    /// Self-attention of one text. `query`, `key` and `value` are
    /// `[seq, hidden]`; the result is the context, `[seq, hidden]`.
    fn attention(
        &self,
        pool: Option<&Pool>,
        query: &[f32],
        key: &[f32],
        value: &[f32],
        seq: usize,
    ) -> Vec<f32> {
        let h = self.hidden;
        let width = h / self.heads;
        let scale = 1.0 / (width as f32).sqrt();
        let heads = par_map(pool, self.heads, |head| {
            let from = head * width;
            // The key of this head, turned to [width, seq].
            let mut key_t = vec![0f32; width * seq];
            for (j, row) in key.chunks_exact(h).enumerate() {
                for (d, v) in row[from..from + width].iter().enumerate() {
                    key_t[d * seq + j] = *v;
                }
            }
            let mut scores = vec![0f32; seq * seq];
            kernel::acc(
                self.simd,
                &query[from..],
                h,
                seq,
                width,
                &key_t,
                seq,
                seq,
                &mut scores,
                seq,
            );
            for row in scores.chunks_exact_mut(seq) {
                let max = row.iter().fold(f32::MIN, |a, &b| a.max(b));
                let mut sum = 0f32;
                for s in row.iter_mut() {
                    *s = ((*s - max) * scale).exp();
                    sum += *s;
                }
                let inv = 1.0 / sum;
                row.iter_mut().for_each(|s| *s *= inv);
            }
            let mut context = vec![0f32; seq * width];
            kernel::acc(
                self.simd,
                &scores,
                seq,
                seq,
                seq,
                &value[from..],
                h,
                width,
                &mut context,
                width,
            );
            context
        });
        let mut out = vec![0f32; seq * h];
        for (head, context) in heads.iter().enumerate() {
            for (row, part) in out.chunks_exact_mut(h).zip(context.chunks_exact(width)) {
                row[head * width..][..width].copy_from_slice(part);
            }
        }
        out
    }
}

/// `x * W^T + b` for each layer of `layers`, then GELU if `activate`. `x` is
/// `[seq, inputs]`; result `i` is `[seq, layers[i].out()]`. The work is one
/// task for each panel of each layer.
fn linear<const N: usize>(
    pool: Option<&Pool>,
    x: &[f32],
    seq: usize,
    layers: [&Dense; N],
    activate: bool,
) -> [Vec<f32>; N] {
    let tasks: Vec<(usize, usize)> = layers
        .iter()
        .enumerate()
        .flat_map(|(layer, dense)| (0..dense.panels()).map(move |panel| (layer, panel)))
        .collect();
    // Each panel has its own small buffer. A direct write to the wide result
    // makes loads and stores share the low address bits (4K aliasing), and
    // that takes about 40% of the speed of the wide layers.
    let panels = par_map(pool, tasks.len(), |i| {
        let (layer, panel) = tasks[i];
        let mut t = Buf::zeroed(seq * P);
        layers[layer].panel(panel, x, seq, &mut t);
        if activate {
            t.iter_mut().for_each(|v| *v = gelu(*v));
        }
        t
    });
    let mut out = layers.map(|dense| vec![0f32; seq * dense.out()]);
    for (&(layer, panel), t) in tasks.iter().zip(&panels) {
        let width = layers[layer].out();
        let (from, n) = (panel * P, P.min(width - panel * P));
        for (row, part) in out[layer].chunks_exact_mut(width).zip(t.as_chunks::<P>().0) {
            row[from..from + n].copy_from_slice(&part[..n]);
        }
    }
    out
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::kernel::tests::noise;
    use crate::weights::tests::{safetensors, temp_dir};
    use std::path::Path;

    /// Sizes that give full panels, part panels and a head width that is not
    /// a multiple of a vector.
    pub(crate) const HIDDEN: usize = 104;
    pub(crate) const HEADS: usize = 4;
    pub(crate) const WIDE: usize = 200;
    pub(crate) const LAYERS: usize = 2;
    pub(crate) const VOCAB: usize = 50;
    pub(crate) const POSITIONS: usize = 70;

    type Tensors = Vec<(String, Vec<usize>, Vec<f32>)>;

    pub(crate) fn tiny_tensors(prefix: &str) -> Tensors {
        let mut all = Tensors::new();
        let mut seed = 100;
        let mut add = |name: String, shape: &[usize], scale: f32, shift: f32| {
            seed += 1;
            let values = noise(shape.iter().product(), seed)
                .iter()
                .map(|v| v * scale + shift)
                .collect();
            all.push((format!("{prefix}{name}"), shape.to_vec(), values));
        };
        let mut pair = |name: &str, out: usize, inp: usize| {
            if inp == 0 {
                add(format!("{name}.weight"), &[out], 0.2, 1.0);
            } else {
                add(format!("{name}.weight"), &[out, inp], 0.2, 0.0);
            }
            add(format!("{name}.bias"), &[out], 0.2, 0.0);
        };
        for i in 0..LAYERS {
            let at = |part: &str| format!("encoder.layer.{i}.{part}");
            pair(&at("attention.self.query"), HIDDEN, HIDDEN);
            pair(&at("attention.self.key"), HIDDEN, HIDDEN);
            pair(&at("attention.self.value"), HIDDEN, HIDDEN);
            pair(&at("attention.output.dense"), HIDDEN, HIDDEN);
            pair(&at("attention.output.LayerNorm"), HIDDEN, 0);
            pair(&at("intermediate.dense"), WIDE, HIDDEN);
            pair(&at("output.dense"), HIDDEN, WIDE);
            pair(&at("output.LayerNorm"), HIDDEN, 0);
        }
        pair("embeddings.LayerNorm", HIDDEN, 0);
        add(
            "embeddings.word_embeddings.weight".into(),
            &[VOCAB, HIDDEN],
            1.0,
            0.0,
        );
        add(
            "embeddings.position_embeddings.weight".into(),
            &[POSITIONS, HIDDEN],
            1.0,
            0.0,
        );
        add(
            "embeddings.token_type_embeddings.weight".into(),
            &[2, HIDDEN],
            1.0,
            0.0,
        );
        // A tensor that the encoder does not read.
        add("pooler.dense.weight".into(), &[HIDDEN, HIDDEN], 1.0, 0.0);
        all
    }

    pub(crate) fn tiny_config() -> String {
        format!(
            r#"{{"model_type": "bert", "hidden_act": "gelu", "vocab_size": {VOCAB},
            "type_vocab_size": 2, "hidden_size": {HIDDEN}, "num_hidden_layers": {LAYERS},
            "num_attention_heads": {HEADS}, "intermediate_size": {WIDE},
            "max_position_embeddings": {POSITIONS}, "layer_norm_eps": 1e-12}}"#
        )
    }

    /// Writes a small model with fixed pseudo-random weights to `dir`.
    pub(crate) fn write_tiny(dir: &Path, tensors: &Tensors) -> ModelFiles {
        let files = ModelFiles {
            model: dir.join("model.safetensors"),
            config: dir.join("config.json"),
            tokenizer: dir.join("tokenizer.json"),
        };
        std::fs::write(&files.model, safetensors(tensors)).unwrap();
        std::fs::write(&files.config, tiny_config()).unwrap();
        files
    }

    /// The same model as plain loops in f64, written from the BERT
    /// definition and not from the encoder code.
    fn reference(tensors: &Tensors, ids: &[u32]) -> Vec<f64> {
        let get = |name: &str| -> Vec<f64> {
            let found = tensors.iter().find(|t| t.0.ends_with(name)).unwrap();
            found.2.iter().map(|v| f64::from(*v)).collect()
        };
        let seq = ids.len();
        let norm = |x: &mut [f64], name: &str| {
            let (gain, bias) = (get(&format!("{name}.weight")), get(&format!("{name}.bias")));
            for row in x.as_chunks_mut::<HIDDEN>().0 {
                let mean = row.iter().sum::<f64>() / HIDDEN as f64;
                let var = row.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / HIDDEN as f64;
                for (i, v) in row.iter_mut().enumerate() {
                    *v = (*v - mean) / (var + 1e-12).sqrt() * gain[i] + bias[i];
                }
            }
        };
        let dense = |x: &[f64], name: &str, out: usize, inp: usize| -> Vec<f64> {
            let (w, b) = (get(&format!("{name}.weight")), get(&format!("{name}.bias")));
            let mut y = vec![0f64; seq * out];
            for r in 0..seq {
                for o in 0..out {
                    y[r * out + o] = b[o]
                        + (0..inp)
                            .map(|k| x[r * inp + k] * w[o * inp + k])
                            .sum::<f64>();
                }
            }
            y
        };
        // erf by its Taylor series: enough terms for |z| < 6 in f64.
        let erf = |z: f64| -> f64 {
            let (mut term, mut sum) = (z, z);
            for n in 1..200 {
                term *= -z * z / n as f64;
                sum += term / (2 * n + 1) as f64;
            }
            (2.0 / std::f64::consts::PI.sqrt() * sum).clamp(-1.0, 1.0)
        };
        let (word, position, kind) = (
            get("embeddings.word_embeddings.weight"),
            get("embeddings.position_embeddings.weight"),
            get("embeddings.token_type_embeddings.weight"),
        );
        let mut x = vec![0f64; seq * HIDDEN];
        for (t, id) in ids.iter().enumerate() {
            for d in 0..HIDDEN {
                x[t * HIDDEN + d] =
                    word[*id as usize * HIDDEN + d] + position[t * HIDDEN + d] + kind[d];
            }
        }
        norm(&mut x, "embeddings.LayerNorm");
        let width = HIDDEN / HEADS;
        for i in 0..LAYERS {
            let at = |part: &str| format!("encoder.layer.{i}.{part}");
            let q = dense(&x, &at("attention.self.query"), HIDDEN, HIDDEN);
            let k = dense(&x, &at("attention.self.key"), HIDDEN, HIDDEN);
            let v = dense(&x, &at("attention.self.value"), HIDDEN, HIDDEN);
            let mut context = vec![0f64; seq * HIDDEN];
            for head in 0..HEADS {
                let col = |d: usize| head * width + d;
                for a in 0..seq {
                    let scores: Vec<f64> = (0..seq)
                        .map(|b| {
                            (0..width)
                                .map(|d| q[a * HIDDEN + col(d)] * k[b * HIDDEN + col(d)])
                                .sum::<f64>()
                                / (width as f64).sqrt()
                        })
                        .collect();
                    let max = scores.iter().cloned().fold(f64::MIN, f64::max);
                    let total: f64 = scores.iter().map(|s| (s - max).exp()).sum();
                    for (b, s) in scores.iter().enumerate() {
                        let weight = (s - max).exp() / total;
                        for d in 0..width {
                            context[a * HIDDEN + col(d)] += weight * v[b * HIDDEN + col(d)];
                        }
                    }
                }
            }
            let attended = dense(&context, &at("attention.output.dense"), HIDDEN, HIDDEN);
            x.iter_mut().zip(&attended).for_each(|(x, a)| *x += a);
            norm(&mut x, &at("attention.output.LayerNorm"));
            let mut wide = dense(&x, &at("intermediate.dense"), WIDE, HIDDEN);
            for v in wide.iter_mut() {
                *v = 0.5 * *v * (1.0 + erf(*v / std::f64::consts::SQRT_2));
            }
            let narrow = dense(&wide, &at("output.dense"), HIDDEN, WIDE);
            x.iter_mut().zip(&narrow).for_each(|(x, a)| *x += a);
            norm(&mut x, &at("output.LayerNorm"));
        }
        x
    }

    fn ids(seq: usize) -> Vec<u32> {
        (0..seq).map(|t| ((t * 7 + 3) % VOCAB) as u32).collect()
    }

    #[test]
    fn encoder_agrees_with_the_bert_definition_on_each_cpu_path() {
        let dir = temp_dir("encoder-ref");
        let pool = Pool::new(4);
        for prefix in ["", "bert."] {
            let tensors = tiny_tensors(prefix);
            let files = write_tiny(&dir, &tensors);
            for simd in Simd::available() {
                let encoder = Encoder::load(&files, simd).unwrap();
                assert_eq!(
                    (encoder.dim(), encoder.vocab(), encoder.max_positions()),
                    (HIDDEN, VOCAB, POSITIONS)
                );
                // 1 and 2: the shortest texts. 31 to 33 and 65: the row block of the kernels.
                for seq in [1, 2, 31, 32, 33, 65, POSITIONS] {
                    let want = reference(&tensors, &ids(seq));
                    let one_thread = encoder.hidden_states(None, &ids(seq)).unwrap();
                    assert_eq!(one_thread.len(), want.len());
                    for (i, (g, w)) in one_thread.iter().zip(&want).enumerate() {
                        assert!(
                            (f64::from(*g) - w).abs() < 2e-4,
                            "{simd:?} seq={seq} index {i}: got {g}, want {w}"
                        );
                    }
                    // Threads change the order of the tasks, not the arithmetic.
                    let threads = encoder.hidden_states(Some(&pool), &ids(seq)).unwrap();
                    assert!(
                        one_thread == threads,
                        "{simd:?} seq={seq}: threads changed the result"
                    );
                }
            }
        }
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn encoder_refuses_a_bad_token_id_and_a_text_that_is_too_long() {
        let dir = temp_dir("encoder-bad-input");
        let encoder = Encoder::load(&write_tiny(&dir, &tiny_tensors("")), Simd::detect()).unwrap();
        assert!(encoder.hidden_states(None, &[]).unwrap().is_empty());
        for id in [VOCAB as u32, u32::MAX] {
            let err = encoder
                .hidden_states(None, &[1, id])
                .unwrap_err()
                .to_string();
            assert!(
                err.contains(&format!("token id {id} is not in the model")),
                "{err}"
            );
        }
        let err = encoder
            .hidden_states(None, &ids(POSITIONS + 1))
            .unwrap_err()
            .to_string();
        assert!(err.contains("71 tokens; the model permits 70"), "{err}");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn load_refuses_a_model_file_that_does_not_agree_with_the_config() {
        let dir = temp_dir("encoder-bad-file");
        let load = |tensors: &Tensors| {
            Encoder::load(&write_tiny(&dir, tensors), Simd::detect())
                .err()
                .unwrap()
                .to_string()
        };
        let mut missing = tiny_tensors("");
        missing.retain(|t| t.0 != "encoder.layer.1.output.dense.bias");
        assert!(load(&missing).contains("tensor encoder.layer.1.output.dense.bias is missing"));

        // The weights of a model with another hidden size.
        let mut other = tiny_tensors("");
        for t in other.iter_mut().filter(|t| t.0.contains("word_embeddings")) {
            t.1 = vec![VOCAB, HIDDEN / 2];
            t.2.truncate(VOCAB * HIDDEN / 2);
        }
        assert!(load(&other).contains("word_embeddings.weight has shape [50,52]"));

        let files = write_tiny(&dir, &tiny_tensors(""));
        std::fs::write(
            &files.config,
            tiny_config().replace("\"bert\"", "\"mpnet\""),
        )
        .unwrap();
        let err = Encoder::load(&files, Simd::detect())
            .err()
            .unwrap()
            .to_string();
        assert!(err.contains("model_type is mpnet"), "{err}");
        std::fs::remove_file(&files.config).unwrap();
        assert!(matches!(
            Encoder::load(&files, Simd::detect()),
            Err(EmbedError::Io(_))
        ));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn gelu_agrees_with_known_values() {
        for (x, want) in [
            (0.0f32, 0.0f32),
            (1.0, 0.841_344_7),
            (-1.0, -0.158_655_25),
            (3.0, 2.995_950_2),
            (-6.0, 0.0),
            (40.0, 40.0),
            (-40.0, 0.0),
        ] {
            assert!((gelu(x) - want).abs() < 2e-6, "gelu({x}) = {}", gelu(x));
        }
        // Against the erf by its Taylor series in f64, over the range of the model.
        for i in -6000..=6000 {
            let x = i as f32 / 1000.0;
            let z = f64::from(x) / std::f64::consts::SQRT_2;
            let (mut term, mut sum) = (z, z);
            for n in 1..200 {
                term *= -z * z / f64::from(n);
                sum += term / f64::from(2 * n + 1);
            }
            let want = 0.5 * f64::from(x) * (1.0 + 2.0 / std::f64::consts::PI.sqrt() * sum);
            assert!(
                (f64::from(gelu(x)) - want).abs() < 2e-6,
                "gelu({x}) = {}, want {want}",
                gelu(x)
            );
        }
    }
}
