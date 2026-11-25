# Own encoder in place of ONNX Runtime

Date: 2026-11-24. Status: dk asked for this change. The merge waits for dk.

## Context

The Rust server ran the embedding model `all-MiniLM-L6-v2` with ONNX Runtime
1.31.0 through the `ort` crate. dk wants no ONNX Runtime in the server, for
two reasons:

- The GitHub repository `microsoft/onnxruntime` returns HTTP 404 since
  2026-11-17, with no public reason. The project can leave open source.
- The library is 29 MB of C++ that the server cannot audit. It contains a
  telemetry client (off in the server, but present).

The `ort` crate is a release candidate (2.0.0-rc.15) with one owner. Three
pure Rust runtimes (rten, candle, tract) were measured on 2026-11-24. Each was
3 to 6 times slower than ONNX Runtime for one query.

The server runs one model family: a BERT encoder. A general runtime is not
necessary for that.

## Decision

The crate `memories-embed` has its own BERT encoder. It reads
`model.safetensors`, `config.json` and `tokenizer.json` from the Hugging Face
cache. The server does not link or load ONNX Runtime. The `ort` crate and its
dependencies are removed.

No code of ONNX Runtime is copied. Four methods of ONNX Runtime (MIT license)
are used as ideas:

| Method | Where |
|---|---|
| Weights packed one time at load, in the order in which the kernel reads them | `kernel.rs`, `Dense::pack` |
| Kernels for AVX-512, AVX2 with FMA and NEON, selected at run time | `kernel.rs` |
| Thread pool whose workers spin for a short time before they sleep | `pool.rs` |
| Buffers on a 64-byte boundary | `kernel.rs`, `Buf` |

Two methods are different from ONNX Runtime:

- No padding. Each text runs at its own length.
- In a list of texts, each thread takes one text. One text alone splits its
  steps over the threads.

The crate has its own thread pool (`pool.rs`, about 200 lines) and does not
use the `rayon` crate. In the prototype, one query took 8.5 ms with `rayon`
and 3.7 ms with a pool whose workers spin. The workers of `rayon` sleep
between the steps of a short text, and each wake costs more than the step.

## Evidence

Machine: 4 vCPU cloud Xeon at 2.1 GHz with AVX-512. Texts: the 1,000 texts of
gate G2. Queries: the first 12 words of a text, 400 calls. Each value is the
range of 3 runs in turns (`cargo run --release -p memories-embed --example
embed_memory`).

| Measure | ONNX Runtime 1.31.0 (v6.0.0) | Own encoder |
|---|---|---|
| One query, p50 | 2.3 to 2.5 ms | 2.3 to 2.4 ms |
| One query, p95 | 4.3 to 4.5 ms | 4.7 to 5.1 ms |
| List of 1,000 texts | 21.7 to 22.7 s | 6.0 to 6.1 s |
| Memory after load (RSS) | 150 to 168 MB | 109 MB |
| Memory peak in the list (RSS) | 362 to 392 MB | 130 MB |
| Gate G2, lowest cosine against Python v5.19.0 | 0.9999991 | 0.9999996 |

Other kernel families on the same machine: AVX2 with FMA gives 2.6 ms for one
query and 6.6 s for the list. Plain Rust (no AVX2) gives 9.3 ms and 27.4 s.

The vectors are not bit-identical to the vectors of ONNX Runtime. The
difference is of the same size as the difference between ONNX Runtime and the
Python reference (cosine 0.999999). Stored vectors stay valid. The name of the
embedding space (`onnx:<model>:<dim>d`) does not change, so no install embeds
its records again.

## Evidence on a copy of the production data

Date: 2026-11-25. dk approved these runs. Data: a copy of the production
records of 2026-11-17 (93,017 records and their vectors) in a test Qdrant
1.15.4 on the machine above. The copy was deleted after the runs.

The production droplet has AVX2 and FMA, and no AVX-512 (`/proc/cpuinfo`,
CPU model `DO-Regular`, 4 vCPU). On the droplet, the AVX2 kernels run. Thus
the runs below use a build of the new server that selects the AVX2 kernels.
ONNX Runtime in the v6.0.0 server used AVX-512 on the test machine: no
setting turns that off. The comparison thus favors v6.0.0.

Gate G3, search results. The two servers got the same 315 queries on the same
migrated collection (`POST /search/explain`, hybrid search, `k` 10). 300
queries are the first 10 words of a record from a random sample. 15 queries
are the queries of `benchmarks/load_test.py`.

| Measure | v6.0.0 against the new server |
|---|---|
| Queries with the same 10 results in the same order | 315 of 315 |
| Queries with the same 30 vector candidates in the same order | 315 of 315 |
| Largest difference of a vector score | 1e-6 |

A first pass ran while Qdrant built its index after the migration. In that
pass, a second run of the v6.0.0 server also gave other results than its
first run (240 of 315 the same). Compare results only after the collection
status is `green`.

Gate G5, load test (`server/scripts/gate_g5.sh`, `G5_DATA`, 5 workers, 60 s
for each scenario, mixed scenario 600 s). Each server ran 2 times, in the
order v6.0.0, new, new, v6.0.0. Each value is the range of the 2 runs.

| Measure | v6.0.0 | New server, AVX2 kernels | Limit |
|---|---|---|---|
| Search p95 | 54 to 58 ms | 35 to 39 ms | 500 ms |
| Burst search p95 | 48 to 59 ms | 41 ms | 500 ms |
| Add p95 | 25 to 26 ms | 23 ms | 250 ms |
| Mixed scenario, search p95 | 58 to 63 ms | 42 to 43 ms | 500 ms |
| Mixed scenario, add p95 | 100 to 108 ms | 69 ms | 250 ms |
| Mixed scenario, requests in 600 s | 50,236 to 52,002 | 56,833 to 57,320 | |
| Memory peak of `serve` (RSS) | 441 to 442 MB | 315 to 317 MB | 400 MB |
| Replies with status 429 | 0 | 0 | 0 |
| Jobs complete after a forced stop (200 jobs) | 200 | 200 | 200 |

`verify` after each run reports only the records that the load test added and
the `last_reinforced_at` fields that the searches set, for the two servers.

Bulk add (`POST /memory/add-batch`, 64 texts for each request, empty store, 2
runs for each server):

| Texts | v6.0.0 | New server, AVX2 kernels | New server, AVX-512 kernels |
|---|---|---|---|
| 1,920 records of the production copy (random sample) | 26.8 to 27.6 s | 12.3 to 12.6 s | 10.7 to 10.9 s |
| 64 texts of 12 words | 0.11 to 0.20 s | 0.15 to 0.19 s | 0.13 to 0.17 s |
| 64 texts of 40 words | 0.22 to 0.35 s | 0.31 to 0.37 s | 0.23 to 0.31 s |
| 64 texts at the 256-token limit | 1.2 to 1.6 s | 1.8 to 2.1 s | 1.6 to 1.9 s |

The new encoder is slower for a list in which each text is at the token
limit. ONNX Runtime pads each text of a batch to the longest text, so a list
of texts of equal length costs it nothing, and its AVX-512 kernels are faster
there. In the production copy, 0.6% of the records have 190 words or more.
For one text of 256 tokens on one thread, the softmax takes 14% of the time
(it calls `exp` for each score).

## Checks

- Each kernel family agrees with plain `f64` loops. The encoder agrees with a
  reference that is written from the BERT definition, on each kernel family,
  with and without the thread pool.
- Gate G2 holds on each kernel family (AVX-512, AVX2 with FMA, plain Rust on
  the machine above; NEON under QEMU).
- Miri (the Rust interpreter that finds undefined behavior) finds no error in
  the tests of `pool.rs` and in the tests of `kernel.rs`. The default Miri run
  reports no AVX2 and no AVX-512, so a second run turns them on
  (`RUSTFLAGS="-C target-feature=+avx,+avx2,+fma,+avx512f"`). That run has the
  AVX-512, AVX2 and plain Rust kernels. A third run for the target
  `aarch64-unknown-linux-gnu` has the NEON kernels.
- Upgrade drill with the v6.0.0 binary: the v6.0.0 server writes 200 records.
  The new server opens the same data and the same model cache, downloads only
  `model.safetensors` and `config.json`, and gives the same search results
  (largest score difference 1e-6). Then the v6.0.0 server starts again with
  `HF_HUB_OFFLINE=1`, gives the same results, and finds a record that the new
  server wrote.
- One independent review read the full change. It found no defect in the
  `unsafe` modules or in the BERT math. Its 8 other findings are fixed (for
  example: the download keeps the commit of the cache, and a panic at model
  load becomes an error).

## Consequences

- `EMBED_MODEL` must name a BERT model (GELU, absolute positions) that has
  `model.safetensors` in its Hub repository. The server refuses another model
  type at start with a clear error. With ONNX Runtime, each model with an ONNX
  export could run.
- The first start after the upgrade from v6.0.0 downloads `model.safetensors`
  (91 MB) and `config.json` from the Hugging Face Hub. The cache of v6.0.0 has
  only `onnx/model.onnx` and `tokenizer.json`. The download uses the commit
  that the cache already has, and `refs/main` does not change. The old file
  stays in the cache, so a return to v6.0.0 needs no download. A server with
  `HF_HUB_OFFLINE=1` and no `model.safetensors` in the cache does not start,
  and the error names the three files.
- `EMBED_PROVIDER=onnx` stays the only accepted value. It is now the name of
  the local embedder. A new name would change each compose file and the name
  of the embedding space.
- The workspace forbids `unsafe` code. `memories-embed` is the exception: it
  denies `unsafe` code and permits it in two modules. `kernel.rs` has the SIMD
  kernels behind safe functions that check each slice size. `pool.rs` erases
  the lifetime of the task of one job.
- The image has no `/opt/onnxruntime`, no `ORT_DYLIB_PATH` and no
  `ORT_DISABLE_TELEMETRY`. An override file that sets these variables still
  works: the server does not read them.
- A panic in the encoder becomes the error `embedder internal error` for that
  request. The embedder thread continues.
- The debug build optimizes `memories-embed` (`profile.dev.package`), because
  the parity jobs of CI run the debug server.
- The Python server v5.19.0 (rollback reference) still uses the Python
  package `onnxruntime` until task P7-3 removes it.
- The team owns the encoder: a new model family needs new code.
- The encoder runs on each CPU that Rust has a target for. It selects the
  kernels at start: AVX-512, or AVX2 with FMA, on x86-64, and NEON on ARM64.
  Each other CPU gets the plain Rust kernels. They give the same vectors, at
  about the list speed of ONNX Runtime and 4 times its query time. The
  start log line `Embedder loaded` names the kernels.

## Not done

- A speed test on the CPU of the production droplet. The runs on the copy of
  the production data used the AVX2 kernels on a cloud Xeon, and ONNX Runtime
  used AVX-512 there. The speed of ONNX Runtime on a CPU with no AVX-512 is
  not known.
- A SIMD `exp` for the softmax, and faster AVX-512 kernels for a list of long
  texts (see the bulk add table).
- NEON speed on real ARM hardware. The NEON kernels pass the tests under QEMU.
- A local build of the image. The build container of the work session cannot
  get crates. The `rust-image` job of CI builds the image for amd64 and arm64.
