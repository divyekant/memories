# Search routes (task P2-2)

The Rust server answers five search routes with the same replies as the Python
server v5.19.0:

- `POST /search`
- `POST /search/batch` (at most 8 searches at the same time)
- `POST /search/explain` (admin only)
- `POST /search/evidence`
- `POST /memory/is-novel` (default threshold 0.88)

## Exit check

`server/scripts/parity_search.sh` is the exit check. CI runs it in the job
`rust-parity`.

1. A Python server runs the golden cases before `usage-headers-search`, and
   `usage-headers-add`. This writes the seed corpus.
2. The test loader `memories-server/examples/load_metadata.rs` copies
   `metadata.json` into Qdrant. The dense vectors come from the Rust embedder.
3. The Rust server replays the 25 cases of `parity/golden/search/routes.ndjson`.

Result on 2026-11-16: 0 of 25 cases differ.

Search p95 on the seed corpus (246 records, 200 searches, `k` = 10, hybrid
false, debug build, local Qdrant 1.15.4): p50 5.9 ms, p95 8.9 ms and 10.6 ms in two runs.

Two more golden files check parts of the routes in `cargo test`:

- `validation.ndjson`: the 422 replies of the request models. The file comes
  from the pydantic models of `app.py`.
- `evidence.ndjson`: the evidence packets of `build_evidence_packet`.

`python -m parity.search_golden --check` makes sure that the two files match
the Python code.

## Parts

| Part | Crate and file | Use |
|---|---|---|
| Engine handle | `memories-engine/src/engine.rs` | Opens `state.db`, the embedder and the Qdrant store. Loads the catalog. |
| Catalog | `memories-engine/src/catalog.rs` | Source, archive flag and links of each record. The scope filter and the graph step read it. |
| Keyword query | `memories-engine/src/keyword.rs` | BM25 sparse vectors (decision P0-2): FNV-1a token IDs, k1 = 1.5, b = 0.75. |
| Reinforcement buffer | `memories-engine/src/reinforce.rs` | Keeps `last_reinforced_at` of each hit. The server writes the buffer to Qdrant each 2 s and at shutdown. Reads apply the buffer first. |
| Scope rules | `memories-engine/src/scope.rs` | Source prefix, boundary, exact source, key prefixes, reserved sources and archive flag. |
| Search | `memories-engine/src/search.rs` | Vector search, hybrid search, explain and `is_novel`. |
| Evidence | `memories-engine/src/evidence.rs` | Port of `build_evidence_packet`. |
| Request models | `memories-server/src/model.rs` | Lax validation and 422 items in the pydantic format. |
| Routes | `memories-server/src/search.rs` | The five handlers. |

The Python flow stays the same: `k = min(k, count, 100)`, 3 × `k` candidates
from each side, the scope filter in the Qdrant query, and the key scope filter
after the search.

## Python behaviors that the port keeps

- A vector-only result shows the old `last_reinforced_at`, because Python
  reinforces the hits after it makes the result.
- `/memory/is-novel` reinforces its hit, because it calls `search(k=1)`.
- `/memory/is-novel` does not apply the key scope.
- An error in the intent step gives a plain-text 500 `Internal Server Error`.
  An error in the engine gives the JSON 500 `{"detail":"Internal server error"}`.

## Differences and deferred work

| Item | State | Task |
|---|---|---|
| Feedback scores in the fusion | Not used. `usage.db` is not ported. | P5-1 |
| Usage rows (retrievals, graph and temporal counts) | Not written. | P5-1 |
| Hybrid results with real keyword scores, and `bm25_candidates` of explain | Not checked here. The parity cases mask them. | P2-4 (gate G3) |
| Confidence half-life of extraction profiles | The default of 90 days applies. | Later task |
| Graph cache | The graph is made again for each search. | Later task |
| No `QDRANT_URL` | The routes reply 503 `Memory engine not available: QDRANT_URL is not set`. The embedded Qdrant mode is not ported (production does not use it, see `baseline.md`). | None |
| Write of reinforcements | Each 2 s, not at once. A crash loses at most 2 s of `last_reinforced_at` values. | None |
