# Rust server: embedder (task P1-2)

Date: 2026-11-16
Status: adopted (task P1-2 of the Rust rewrite plan, gate G2)

## Decision

The crate `memories-embed` runs the ONNX model `all-MiniLM-L6-v2` with the
crates `ort` (ONNX Runtime 1.31.0, loaded from `ORT_DYLIB_PATH`) and
`tokenizers`. It keeps the rules of `onnx_embedder.py` in v5.19.0:

- Cut at 256 tokens. Pad each batch to its longest text.
- Mean pool with the attention mask, then L2 normalization. Both divisors
  are clamped to 1e-9.
- Batches of 64 texts. 4 intra-op threads. Graph optimization level "all".
- `token_type_ids` is all zeros when the model has that input.
- `EMBED_QUERY_PREFIX` and `EMBED_DOC_PREFIX` go before the text.
- `MODEL_NAME`, `EMBED_MODEL`, `EMBED_DIMENSION`, `MODEL_CACHE_DIR` and
  `PRELOADED_MODEL_CACHE_DIR` have the same effect as in Python.

One worker thread (`memories-embed`) owns the ONNX session and the tokenizer.
Request handlers send jobs to it and wait without a block. This is the
"embedding pool" of the plan. Jobs run one at a time, as with the lock of the
Python embedder.

## Model files

The crate reads the Hugging Face cache layout that `hf_hub_download` writes:
`models--sentence-transformers--all-MiniLM-L6-v2/refs/main` and
`snapshots/<commit>/`. A cache that the Python server filled works without a
download. If the files are not in the cache, the crate gets the current commit
of `main` from the Hub, downloads the two files of that commit, and writes
them in the same layout. Each file goes to a temporary name first, then a
rename, after a sync. Each download has a limit of 10 seconds to connect
and 10 seconds for each read, the same as `huggingface_hub`. A streamed file
must have the length that the Hub gave. The variables `HF_ENDPOINT`,
`HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HF_TOKEN_PATH`, `HF_HUB_OFFLINE`,
`HF_HUB_CACHE` and `HF_HOME` have the same effect as in Python. The Python server reads a cache that the Rust server filled (checked
with `HF_HUB_OFFLINE=1`).

## Gate G2

- Fixture: 1,000 texts (`server/crates/memories-embed/tests/fixtures/`) and
  their vectors from the Python embedder of v5.19.0 (onnxruntime 1.23.2,
  tokenizers 0.22.2). `make_embed_fixture.py` makes the fixture again.
- The texts include empty and whitespace texts, Unicode, emoji, code, and
  texts longer than 256 tokens.
- Result on 2026-11-16: the lowest cosine similarity is 0.9999997. The pass
  condition is 0.9999.
- CI runs the test in the `rust` job, with the same ONNX Runtime archive and
  hash as the image.

## Memory

Measured with `cargo run --release -p memories-embed --example embed_memory`
on 4 vCPU. The values are the resident memory of the process (`VmRSS`) and
its peak (`VmHWM`).

| State | Python v5.19.0 | Rust, CPU arena on | Rust, CPU arena off (adopted) |
|---|---|---|---|
| Model loaded | 200 MB | 155 MB | 155 MB |
| After 200 short queries | not measured | not measured | 155 MB |
| After 1,000 texts in batches of 64 | 1,347 MB | 1,273 MB | 225 MB |
| Peak during the batches | 1,347 MB | 1,273 MB | 830 MB |
| Time for 1,000 texts | 27 s | 26 s | 30 s |

- The ONNX Runtime CPU arena keeps the largest allocation of each batch for
  the life of the process. The arena is off in the Rust server, so the memory
  goes back after the batch. The time cost is about 10% on bulk embedding.
- The production RSS of 1.37 GB at start (P0-3 baseline) has the same cause in
  Python.

CAUTION: The steady memory (155 MB to 225 MB) is in the 400 MB target of
gate G5. The peak during a batch of 64 long texts (about 830 MB) is not. The
attention tensors of a batch of 64 texts at 256 tokens cause the peak. The
crate `tract` would need the same tensors, so it is not a fix. A smaller
batch for long texts lowers the peak and gives the same vectors, because
padding does not change a vector. That change waits for a decision from dk.

## Differences from Python

- Cache first: if the model is in the cache, the Rust server does not ask the
  Hub for a newer commit. `hf_hub_download` asks at each start when it is
  online. A new commit of the model needs a new fixture and a G2 run anyway.
- `EMBED_PROVIDER=openai` is refused at start with a clear error. Production
  uses `onnx` (P0-3 baseline). The OpenAI embedder is not ported.
- The old variable `HUGGINGFACE_HUB_CACHE` is not read.
- A model name is not checked as Python `validate_repo_id` does. A bad name
  gives a download error, not a name error.
- An embed call with no text returns no vector. Python raises `ValueError`.
- The worker skips a job when its caller has gone.
- The embedding-space registry (`embedding_spaces.json`) and the collection
  names are not in this crate. The store tasks own them.
