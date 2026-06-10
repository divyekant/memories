# Embedder Upgrade: from all-MiniLM-L6-v2 to a modern small embedder

Status: candidate decision (2026-06)
Scope: ~28k memories, ~472 MB Qdrant index, Mac Mini M4 Pro 24 GB host,
backend in Docker (OrbStack), oMLX serving OpenAI-compatible models on
host port 11434.

> Knowledge caveat: model facts and benchmark figures below come from the
> author's training knowledge (cutoff January 2026), not live retrieval.
> MTEB numbers are approximate and shift between leaderboard revisions.
> Treat them as directional; the deciding evidence is the tier-1 eval run
> described in "Eval hook-in" below.

## Why upgrade

`all-MiniLM-L6-v2` (384d, trained ~2019-2021 data, 256-token window) is the
weakest link in retrieval quality. It predates instruction-tuned and
long-context embedding training; on BEIR-style retrieval it sits around
~41-44 nDCG@10 average, while modern small models score 50+. At 28k points
the index is small enough that storage and latency are *not* constraints —
quality is the whole game.

## Candidates

| | all-MiniLM-L6-v2 (today) | nomic-embed-text-v1.5 | Qwen3-Embedding-0.6B | bge-m3 |
|---|---|---|---|---|
| Params | 22M | 137M | ~600M | 568M |
| Dimension | 384 | 768 (MRL: 64-768) | 1024 (MRL: 32-1024) | 1024 |
| Context window | 256 (as configured) | 8192 | 32768 | 8192 |
| Retrieval quality (approx, English) | baseline (~41-44 BEIR) | strong (~53-55 BEIR; beats ada-002) | strongest of the three (top of small-model MTEB as of mid-2025, ~64 MTEB multilingual) | strong, esp. multilingual (~54+ BEIR dense-only) |
| Asymmetric prefixes | none | required (`search_query:` / `search_document:`) | query-side instruction recommended, docs bare | none required |
| ONNX in-process viability | yes (current) | yes — official ONNX weights, ~140 MB int8 / ~550 MB fp32, CPU latency fine for single queries | poor — 600M params on container CPU is slow (hundreds of ms) and memory-heavy | marginal — 568M, same problem |
| oMLX servability (M4 GPU) | pointless | possible | yes — MLX community builds exist, fast on M4 Pro | spotty MLX support |
| License | Apache-2.0 | Apache-2.0 | Apache-2.0 | MIT |

Also considered and rejected:

- **snowflake-arctic-embed-s** (33M, 384d): cheapest swap, but same
  dimension as MiniLM is exactly the silent-mixing hazard this design
  guards against, gains are modest (~51-55 BEIR), and it's English-only.
  Not worth a migration for a half-step.
- **text-embedding-3-small** (OpenAI cloud): already supported via
  `EMBED_PROVIDER=openai`, good quality, but sends every memory and every
  prompt-hook query to a cloud API. Conflicts with the local-first design
  goal; keep as BYOK option only.

## Storage impact at ~30k points

float32 vectors only (HNSW graph + payloads add roughly proportional
overhead; payloads dominate the current 472 MB):

| dim | vectors @30k | delta vs 384d |
|---|---|---|
| 384 | ~46 MB | — |
| 768 | ~92 MB | +46 MB |
| 1024 | ~123 MB | +77 MB |

Conclusion: storage is a non-issue at this corpus size. Even 1024d adds
under 100 MB of raw vector data. Dimension choice should be driven by
quality and serving cost, not disk. (If it ever matters, both recommended
models are Matryoshka-trained and can be truncated.)

## Servability trade-off (the real decision)

The memories backend is on the hot path of *every* user prompt
(`memory-query.sh` UserPromptSubmit hook) and must keep working when the
rest of the Mac is busy or oMLX is down (model swaps, reboots, an LLM
hogging the queue). Two serving shapes:

1. **ONNX in-process** (current shape): self-contained container, no
   network dependency, no coupling to oMLX uptime. Costs container RAM and
   CPU latency.
2. **OpenAI-compatible endpoint via oMLX** (`EMBED_BASE_URL=
   http://host.docker.internal:11434/v1`): GPU-fast, zero container bloat,
   but every embed (including every prompt-time query) now depends on an
   external process that also serves LLM generation. An oMLX outage breaks
   vector search and writes.

## Recommendation

**Primary: nomic-embed-text-v1.5 at 768d, served in-process via ONNX**
(`EMBED_PROVIDER=onnx`, `EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5`,
`EMBED_QUERY_PREFIX="search_query: "`, `EMBED_DOC_PREFIX="search_document: "`).

Rationale, in this project's priority order:

- **Scalable**: 137M params is comfortably servable on CPU inside the
  existing container for single-query workloads; quality jump over MiniLM
  is the largest per unit of operational risk.
- **Future-proof**: keeps the memory service self-contained (local-first,
  no runtime coupling to oMLX); Matryoshka training means 512d/256d
  truncation remains available if the corpus grows 10x.
- **Extensible**: the same env plumbing added with this change makes
  **Qwen3-Embedding-0.6B via oMLX** a config-only experiment
  (`EMBED_PROVIDER=openai`, `EMBED_BASE_URL=http://host.docker.internal:11434/v1`,
  `EMBED_MODEL=<served model id>`, `EMBED_DIMENSION=1024`). It is the
  quality ceiling of the small-model class; run it through the eval
  harness as an A/B before committing the hot path to an external
  endpoint.

Trade-offs accepted: container image/RAM grows (~150-550 MB depending on
quantization choice); nomic requires asymmetric prefixes (now first-class
via `EMBED_QUERY_PREFIX`/`EMBED_DOC_PREFIX`, and part of the embedding-space
signature so prefixed and unprefixed vectors can never mix); English-leaning
model (corpus is English).

Explicitly not chosen as primary: Qwen3-0.6B-via-oMLX (availability
coupling on the prompt hot path), bge-m3 (heavyweight for in-process CPU
with no English quality edge over nomic to justify it).

Implementation notes for the ONNX path: `onnx_embedder.py` loads
`onnx/model.onnx` + `tokenizer.json` from the given HF repo id and applies
mean pooling — verify the chosen repo ships those files (the official
`nomic-ai/nomic-embed-text-v1.5` repo does). The tokenizer is currently
truncated at 256 tokens; fine for memory-sized chunks, revisit if longer
documents are ever embedded whole.

## Configuration (implemented)

All embedding config is env-driven; embedding spaces are explicit and
guarded:

| Variable | Meaning |
|---|---|
| `EMBED_PROVIDER` | `onnx` (in-process) or `openai` (any OpenAI-compatible API) |
| `EMBED_MODEL` | model name / HF repo id |
| `EMBED_BASE_URL` | OpenAI-compatible endpoint, e.g. oMLX `http://host.docker.internal:11434/v1` |
| `EMBED_API_KEY` | key for that endpoint (falls back to `OPENAI_API_KEY`; optional for local endpoints) |
| `EMBED_DIMENSION` | declared dim; validated against the loaded model, fails fast on mismatch |
| `EMBED_QUERY_PREFIX` / `EMBED_DOC_PREFIX` | asymmetric prefixes for prefix-trained models |
| `EMBED_COLLECTION` | pin an exact Qdrant collection name (skips auto naming) |
| `EMBED_ALLOW_SPACE_REBIND` | opt-in to re-record a collection under a new signature (requires re-embed) |

Space rules (`embedding_space.py`):

- Collection names carry model+dim: a non-default embedder resolves to
  `<base>__<model_slug>_<dim>d` (e.g. `memories__nomic_ai_nomic_embed_text_v1_5_768d`).
  The legacy default (onnx / all-MiniLM-L6-v2, no prefixes) keeps the bare
  base name so existing deployments are untouched.
- `data/embedding_spaces.json` records the signature
  (`provider:model:<dim>d[+pfx-<hash>]`) that created each collection.
  On startup the engine refuses to attach to a collection recorded under a
  different signature — this catches the dangerous case dimension checks
  miss: two different models with the same dimension.

## Migration (implemented: `scripts/reembed.py`)

Blue/green: build a NEW collection from existing payload text; the old
collection is never written.

```bash
# 1. Migrate (resumable; state file holds the scroll cursor)
uv run python scripts/reembed.py migrate \
  --url http://localhost:6333 --source memories \
  --provider onnx --model nomic-ai/nomic-embed-text-v1.5 \
  --doc-prefix "search_document: " --dimension 768 \
  --batch-size 64 --max-rps 8

# 2. Sanity-check neighbor structure (old-vs-new top-k overlap on N samples)
uv run python scripts/reembed.py verify \
  --url http://localhost:6333 --source memories \
  --target memories__nomic_ai_nomic_embed_text_v1_5_768d \
  --model nomic-ai/nomic-embed-text-v1.5 --dimension 768 \
  --samples 25 --k 10 --report reembed-verify.json

# 3. Cutover — dry-run first (prints plan + rollback values), then --execute
uv run python scripts/reembed.py cutover \
  --url http://localhost:6333 --source memories \
  --target memories__nomic_ai_nomic_embed_text_v1_5_768d \
  --provider onnx --model nomic-ai/nomic-embed-text-v1.5 \
  --query-prefix "search_query: " --doc-prefix "search_document: " \
  --dimension 768 --env-file .env
# review output, then append: --execute   (and restart the service)
```

Notes:

- Interrupt at any time; re-running `migrate` resumes from the saved
  cursor. `--max-batches N` supports deliberate chunked runs; `--max-rps`
  throttles embedding calls (relevant when oMLX shares the box).
- `verify` overlap is a plumbing sanity check, not a quality score — a
  better model *should* rank somewhat differently. Near-zero overlap means
  something is wired wrong (text field, endpoint, ids).
- `cutover` refuses on point-count mismatch (override with `--force`),
  backs up the env file, never writes `EMBED_API_KEY`, and prints the
  rollback values.

**Rollback**: restore the previous `EMBED_*` values (the cutover output and
the `.env.bak-<ts>` file both contain them — for the current deployment
that's `EMBED_PROVIDER=onnx`, `EMBED_MODEL=` unset, `EMBED_COLLECTION=`
unset), restart the service, and you are back on the old collection, which
was never modified. Delete the new collection only after a soak period.

Operational guardrail: do not run migrate/cutover against the production
instance while validating — use the isolated eval stack first (below).

## Eval hook-in (documented, not run)

Tier-1 retrieval scoring uses the existing LongMemEval harness in **tool
mode** (raw `/search` API, no agent in the loop — `eval/run_longmemeval.py`),
which reports judge score and `R@5` per category against whatever embedder
the target service is running.

1. Bring up the isolated eval stack (ports 8901/6335, separate volumes;
   production on 8900/6333 is untouched) with the candidate embedder:

   ```bash
   EMBED_PROVIDER=onnx \
   EMBED_MODEL=nomic-ai/nomic-embed-text-v1.5 \
   EMBED_QUERY_PREFIX="search_query: " \
   EMBED_DOC_PREFIX="search_document: " \
   docker compose -f docker-compose.eval.yml up -d --build
   ```

2. Run the tier-1 (tool-mode) eval against it:

   ```bash
   MEMORIES_API_KEY=<eval key> \
   EVAL_MEMORIES_URL=http://localhost:8901 \
   .venv/bin/python eval/run_longmemeval.py --mode tool --questions 100 \
     --output eval/results/longmemeval-v4.0.0-tool-nomic-v1.5.json
   ```

3. Baseline: the same command against the eval stack started *without* the
   `EMBED_*` overrides (MiniLM), output to
   `eval/results/longmemeval-v4.0.0-tool-minilm.json`. Compare `overall`,
   `recall_any_at_5`, and the per-category `recall_categories` blocks.
   The harness seeds and cleans its own corpus per question, so each run
   scores the embedder actually serving the target instance.

Decision gate: adopt the new embedder if tool-mode `recall_any_at_5`
improves by ≥3 points without category regressions; otherwise stay and
re-evaluate the Qwen3-via-oMLX variant.

## Tier-1 A/B result (2026-06-10) — VERDICT: do not promote

Sampled LongMemEval tool-mode A/B, 20 questions x 6 categories (n=120), isolated
eval stack (`docker-compose.eval.yml`), recall_any_at_5 (judge disabled via
recall-only mode — recall is mechanical and judge-independent). nomic ran with
correct `search_query:`/`search_document:` prefixes and the embedding-space
registry collection `memories__nomic_ai_nomic_embed_text_v1_5_768d`.

| category | all-MiniLM-L6-v2 | nomic-embed-text-v1.5 | delta |
|---|---:|---:|---:|
| single-session-user | 0.900 | 0.800 | -0.100 |
| single-session-assistant | 0.950 | 1.000 | +0.050 |
| single-session-preference | 0.950 | 1.000 | +0.050 |
| multi-session | 0.950 | 0.950 | 0.000 |
| knowledge-update | 1.000 | 0.950 | -0.050 |
| temporal-reasoning | 1.000 | 0.950 | -0.050 |
| **overall (equal weight)** | **0.958** | **0.942** | **-0.017** |

Promotion gate was +0.03 overall; measured -0.017 — **FAIL**. MiniLM is already
near-ceiling on this corpus (0.958), so the "2019-era embedder is leaving recall
on the table" hypothesis is refuted at tier 1 on this eval set. The default
embedder stays all-MiniLM-L6-v2. The embedding-space registry, env-selectable
embedder, and `scripts/reembed.py` migration rails ship anyway — they are the
prerequisite for ANY future swap (e.g. Qwen3-Embedding-0.6B via oMLX, untested,
or if the corpus/query mix changes). Operational note from the run: nomic 768d
ONNX trips the embedder auto-reload RSS threshold (1.2GB) — disable
`EMBEDDER_AUTO_RELOAD_ENABLED` or raise the threshold for any future 768d eval.
