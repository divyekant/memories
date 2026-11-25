//! Readers for the two model files: `config.json` and `model.safetensors`.
//!
//! The files come from the network, so each value is checked before use. A
//! bad file gives an error and never a panic.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use serde_json::{Map, Value};

use crate::{EmbedError, Result};

fn bad(message: String) -> EmbedError {
    EmbedError::Model(message)
}

/// The sizes of a BERT encoder, from `config.json`.
#[derive(Debug, Clone, PartialEq)]
pub struct BertConfig {
    pub vocab: usize,
    pub type_vocab: usize,
    pub hidden: usize,
    pub layers: usize,
    pub heads: usize,
    pub intermediate: usize,
    pub max_positions: usize,
    pub layer_norm_eps: f32,
}

impl BertConfig {
    /// Refuses a model that this encoder cannot run: only BERT with GELU and
    /// absolute positions is supported.
    pub fn parse(json: &[u8]) -> Result<Self> {
        let v: Value =
            serde_json::from_slice(json).map_err(|e| bad(format!("config.json: {e}")))?;
        let text = |key: &str, default: &str| -> Result<String> {
            match &v[key] {
                Value::Null => Ok(default.to_string()),
                Value::String(s) => Ok(s.clone()),
                other => Err(bad(format!("config.json: {key} is {other}, not a string"))),
            }
        };
        let size = |key: &str| -> Result<usize> {
            v[key]
                .as_u64()
                .filter(|n| *n > 0)
                .and_then(|n| usize::try_from(n).ok())
                .ok_or_else(|| bad(format!("config.json: {key} must be a positive integer")))
        };
        for (key, default, want) in [
            ("model_type", "bert", "bert"),
            ("hidden_act", "gelu", "gelu"),
            ("position_embedding_type", "absolute", "absolute"),
        ] {
            let got = text(key, default)?;
            if got != want {
                return Err(bad(format!(
                    "config.json: {key} is {got}; this server runs only BERT models with {key}={want}"
                )));
            }
        }
        let cfg = Self {
            vocab: size("vocab_size")?,
            type_vocab: size("type_vocab_size")?,
            hidden: size("hidden_size")?,
            layers: size("num_hidden_layers")?,
            heads: size("num_attention_heads")?,
            intermediate: size("intermediate_size")?,
            max_positions: size("max_position_embeddings")?,
            layer_norm_eps: match &v["layer_norm_eps"] {
                Value::Null => 1e-12,
                // Checked as f32: a value such as 1e-50 is 0 in f32.
                eps => eps
                    .as_f64()
                    .map(|e| e as f32)
                    .filter(|e| *e > 0.0 && *e < 1.0)
                    .ok_or_else(|| {
                        bad("config.json: layer_norm_eps must be between 0 and 1".into())
                    })?,
            },
        };
        if !cfg.hidden.is_multiple_of(cfg.heads) {
            return Err(bad(format!(
                "config.json: hidden_size {} is not a multiple of num_attention_heads {}",
                cfg.hidden, cfg.heads
            )));
        }
        // The kernels read the inputs of a dense layer 8 at a time.
        if !cfg.hidden.is_multiple_of(8) || !cfg.intermediate.is_multiple_of(8) {
            return Err(bad(format!(
                "config.json: hidden_size {} and intermediate_size {} must be multiples of 8",
                cfg.hidden, cfg.intermediate
            )));
        }
        Ok(cfg)
    }
}

/// The largest header that the safetensors format permits.
const MAX_HEADER: u64 = 100 * 1024 * 1024;

/// A `model.safetensors` file: 8 bytes with the header length (little
/// endian), the JSON header, then the tensor data.
pub struct SafeTensors {
    file: File,
    name: String,
    tensors: Map<String, Value>,
    data_start: u64,
    data_len: u64,
}

impl SafeTensors {
    pub fn open(path: &Path) -> Result<Self> {
        let name = path.display().to_string();
        let mut file = File::open(path)?;
        let file_len = file.metadata()?.len();
        let mut prefix = [0u8; 8];
        file.read_exact(&mut prefix)
            .map_err(|_| bad(format!("{name}: the file is too short")))?;
        let header_len = u64::from_le_bytes(prefix);
        let after_prefix = file_len.saturating_sub(8);
        if header_len > MAX_HEADER || header_len > after_prefix {
            return Err(bad(format!(
                "{name}: header length {header_len} is not valid for a file of {file_len} bytes"
            )));
        }
        let mut header = vec![0u8; header_len as usize];
        file.read_exact(&mut header)?;
        let tensors = match serde_json::from_slice(&header) {
            Ok(Value::Object(map)) => map,
            Ok(_) => return Err(bad(format!("{name}: the header is not a JSON object"))),
            Err(e) => return Err(bad(format!("{name}: header: {e}"))),
        };
        Ok(Self {
            file,
            name,
            tensors,
            data_start: 8 + header_len,
            data_len: after_prefix - header_len,
        })
    }

    /// The tensor `name`, or `bert.<name>` (the name in a full BERT
    /// checkpoint). It must hold 32-bit floats with exactly `shape`, and each
    /// value must be finite.
    pub fn f32(&mut self, name: &str, shape: &[usize]) -> Result<Vec<f32>> {
        let file = &self.name;
        let entry = self
            .tensors
            .get(name)
            .or_else(|| self.tensors.get(&format!("bert.{name}")))
            .ok_or_else(|| bad(format!("{file}: tensor {name} is missing")))?;
        if entry["dtype"] != "F32" {
            return Err(bad(format!(
                "{file}: tensor {name} has dtype {}; this server reads only F32",
                entry["dtype"]
            )));
        }
        let got: Option<Vec<u64>> = entry["shape"]
            .as_array()
            .and_then(|dims| dims.iter().map(Value::as_u64).collect());
        if got.as_deref() != Some(&shape.iter().map(|d| *d as u64).collect::<Vec<_>>()[..]) {
            return Err(bad(format!(
                "{file}: tensor {name} has shape {}, but config.json gives {shape:?}",
                entry["shape"]
            )));
        }
        let bytes = shape.iter().try_fold(4u64, |n, d| n.checked_mul(*d as u64));
        let offsets = (
            entry["data_offsets"][0].as_u64(),
            entry["data_offsets"][1].as_u64(),
        );
        let (begin, len) = match (offsets, bytes) {
            ((Some(begin), Some(end)), Some(bytes))
                if begin <= end && end <= self.data_len && end - begin == bytes =>
            {
                (begin, bytes)
            }
            _ => {
                return Err(bad(format!(
                    "{file}: tensor {name} has data offsets {} that do not agree with its shape or the file size",
                    entry["data_offsets"]
                )));
            }
        };
        // `len` is not more than the file size, so the buffer is not larger than the file.
        let mut raw = vec![0u8; len as usize];
        self.file.seek(SeekFrom::Start(self.data_start + begin))?;
        self.file.read_exact(&mut raw)?;
        let values: Vec<f32> = raw
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| f32::from_le_bytes(*b))
            .collect();
        if !values.iter().all(|v| v.is_finite()) {
            return Err(bad(format!(
                "{file}: tensor {name} has a value that is not finite"
            )));
        }
        Ok(values)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::path::PathBuf;

    /// A new empty folder in the temporary folder.
    pub(crate) fn temp_dir(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("memories-embed-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// The bytes of a safetensors file with these F32 tensors.
    pub(crate) fn safetensors(tensors: &[(String, Vec<usize>, Vec<f32>)]) -> Vec<u8> {
        let mut header = Map::new();
        let mut data = Vec::new();
        for (name, shape, values) in tensors {
            let begin = data.len();
            data.extend(values.iter().flat_map(|v| v.to_le_bytes()));
            header.insert(
                name.clone(),
                serde_json::json!({"dtype": "F32", "shape": shape, "data_offsets": [begin, data.len()]}),
            );
        }
        with_header(&Value::Object(header).to_string(), &data)
    }

    fn with_header(header: &str, data: &[u8]) -> Vec<u8> {
        let mut out = (header.len() as u64).to_le_bytes().to_vec();
        out.extend(header.as_bytes());
        out.extend(data);
        out
    }

    const CONFIG: &str = r#"{"model_type": "bert", "hidden_act": "gelu", "vocab_size": 30522,
        "type_vocab_size": 2, "hidden_size": 384, "num_hidden_layers": 6,
        "num_attention_heads": 12, "intermediate_size": 1536,
        "max_position_embeddings": 512, "layer_norm_eps": 1e-12}"#;

    #[test]
    fn config_reads_the_sizes_of_all_minilm_l6_v2() {
        assert_eq!(
            BertConfig::parse(CONFIG.as_bytes()).unwrap(),
            BertConfig {
                vocab: 30522,
                type_vocab: 2,
                hidden: 384,
                layers: 6,
                heads: 12,
                intermediate: 1536,
                max_positions: 512,
                layer_norm_eps: 1e-12,
            }
        );
    }

    #[test]
    fn config_refuses_what_the_encoder_cannot_run() {
        let with = |from: &str, to: &str| {
            assert!(CONFIG.contains(from), "{from}");
            BertConfig::parse(CONFIG.replace(from, to).as_bytes())
                .unwrap_err()
                .to_string()
        };
        for (from, to, want) in [
            ("\"bert\"", "\"xlm-roberta\"", "model_type is xlm-roberta"),
            ("\"gelu\"", "\"relu\"", "hidden_act is relu"),
            ("\"gelu\"", "7", "hidden_act is 7, not a string"),
            (
                "\"model_type\"",
                "\"position_embedding_type\": \"relative_key\", \"model_type\"",
                "position_embedding_type is relative_key",
            ),
            (
                "\"hidden_size\": 384",
                "\"hidden_size\": 0",
                "hidden_size must be a positive integer",
            ),
            (
                "\"hidden_size\": 384",
                "\"hidden_size\": -384",
                "hidden_size must be a positive integer",
            ),
            (
                "\"hidden_size\": 384",
                "\"hidden_size\": \"384\"",
                "hidden_size must be a positive integer",
            ),
            (
                "\"hidden_size\": 384",
                "\"hidden_size\": 380",
                "not a multiple of num_attention_heads",
            ),
            (
                "\"hidden_size\": 384",
                "\"hidden_size\": 372",
                "must be multiples of 8",
            ),
            ("1536", "1540", "must be multiples of 8"),
            (
                "\"num_hidden_layers\": 6,",
                "",
                "num_hidden_layers must be a positive integer",
            ),
            ("1e-12", "0", "layer_norm_eps"),
            ("1e-12", "1e-50", "layer_norm_eps"),
            ("1e-12", "\"x\"", "layer_norm_eps"),
        ] {
            let got = with(from, to);
            assert!(got.contains(want), "{from} -> {to}: {got}");
        }
        assert!(
            BertConfig::parse(b"[1, 2")
                .unwrap_err()
                .to_string()
                .contains("config.json")
        );
        assert!(BertConfig::parse(b"[]").is_err());
    }

    #[test]
    fn safetensors_reads_a_tensor_by_name_or_with_the_bert_prefix() {
        let dir = temp_dir("st-read");
        let path = dir.join("model.safetensors");
        let file = safetensors(&[
            ("a".into(), vec![2, 3], vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
            ("bert.b".into(), vec![2], vec![7.0, 8.0]),
        ]);
        std::fs::write(&path, file).unwrap();
        let mut st = SafeTensors::open(&path).unwrap();
        assert_eq!(st.f32("b", &[2]).unwrap(), [7.0, 8.0]);
        assert_eq!(
            st.f32("a", &[2, 3]).unwrap(),
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        );
        for (name, shape, want) in [
            ("c", vec![2], "tensor c is missing"),
            (
                "a",
                vec![3, 2],
                "has shape [2,3], but config.json gives [3, 2]",
            ),
            ("a", vec![6], "has shape"),
        ] {
            let got = st.f32(name, &shape).unwrap_err().to_string();
            assert!(got.contains(want), "{got}");
        }
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn safetensors_refuses_a_bad_file_with_an_error() {
        let dir = temp_dir("st-bad");
        let path = dir.join("model.safetensors");
        let open = |bytes: &[u8]| {
            std::fs::write(&path, bytes).unwrap();
            SafeTensors::open(&path)
        };
        let open_err = |bytes: &[u8]| open(bytes).err().unwrap().to_string();
        assert!(open_err(b"").contains("too short"));
        assert!(open_err(b"1234567").contains("too short"));
        assert!(open_err(&u64::MAX.to_le_bytes()).contains("header length"));
        assert!(open_err(&9u64.to_le_bytes()).contains("header length"));
        assert!(open_err(&with_header("{\"a\": ", b"")).contains("header:"));
        assert!(open_err(&with_header("[1]", b"")).contains("not a JSON object"));

        let tensor = |entry: Value, data: &[u8]| {
            open(&with_header(
                &serde_json::json!({"t": entry}).to_string(),
                data,
            ))
            .unwrap()
            .f32("t", &[2])
            .unwrap_err()
            .to_string()
        };
        let data = [0u8; 8];
        let entry = |dtype: &str, shape: Value, offsets: Value| serde_json::json!({"dtype": dtype, "shape": shape, "data_offsets": offsets});
        let two = || serde_json::json!([2]);
        assert!(tensor(entry("F16", two(), serde_json::json!([0, 8])), &data).contains("only F32"));
        assert!(tensor(serde_json::json!("x"), &data).contains("dtype"));
        assert!(
            tensor(
                entry("F32", serde_json::json!("x"), serde_json::json!([0, 8])),
                &data
            )
            .contains("shape")
        );
        for offsets in [
            serde_json::json!([0, 4]),
            serde_json::json!([4, 12]),
            serde_json::json!([8, 0]),
            serde_json::json!([0]),
            serde_json::json!("x"),
            serde_json::json!([-8, 0]),
            serde_json::json!([u64::MAX - 7, u64::MAX]),
        ] {
            let got = tensor(entry("F32", two(), offsets.clone()), &data);
            assert!(got.contains("data offsets"), "{offsets}: {got}");
        }
        let mut nan = 1f32.to_le_bytes().to_vec();
        nan.extend(f32::NAN.to_le_bytes());
        assert!(
            tensor(entry("F32", two(), serde_json::json!([0, 8])), &nan).contains("not finite")
        );
        // A shape whose byte count is more than a u64.
        let huge = open(&with_header(
            &serde_json::json!({"t": entry("F32", serde_json::json!([u64::MAX, 8]), serde_json::json!([0, 8]))})
                .to_string(),
            &data,
        ))
        .unwrap()
        .f32("t", &[usize::MAX, 8])
        .unwrap_err()
        .to_string();
        assert!(huge.contains("data offsets"), "{huge}");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
