# Benchmark: Post-Spike Memory Reclamation

Date: 2026-02-17  
Goal: verify that memory usage trends down after extraction bursts instead of staying pinned at peak.

## Setup

- Image: `memories:codex-memtrim`
- Two containers, same code, only one config difference:
  - `faiss-mem-notrim`: `MEMORY_TRIM_ENABLED=false`
  - `faiss-mem-trim`: `MEMORY_TRIM_ENABLED=true` + `MEMORY_TRIM_COOLDOWN_SEC=1`
- Extraction provider: synthetic Ollama-compatible test server returning large payloads.
- Load profile:
  - `40` extract calls
  - concurrency `4`
  - message size ~`110k` chars
  - all requests successful (`200`)

## Results

### No trim

- Baseline: `184.9 MiB`
- Peak: `258.7 MiB`
- Post 0s: `258.5 MiB`
- Post 3s: `258.6 MiB`
- Post 10s: `264.9 MiB`

### Trim enabled

- Baseline: `187.8 MiB`
- Peak: `251.7 MiB`
- Post 0s: `251.0 MiB`
- Post 3s: `250.7 MiB`
- Post 10s: `246.0 MiB`

### Delayed sample (~20s after run)

- No trim: `254.0 MiB`
- Trim enabled: `245.8 MiB`

## Interpretation

- With trimming disabled, memory remained near burst peak.
- With trimming enabled, memory moved downward after load.
- Latency impact was negligible in this profile (both runs were ~2.0s p50 with the synthetic provider).

## Notes

- This benchmark is synthetic by design (large extraction payload stress).
- Absolute numbers depend on allocator behavior, provider response shape, and host runtime.
- The intended signal is trend direction after burst, not exact MiB values.
