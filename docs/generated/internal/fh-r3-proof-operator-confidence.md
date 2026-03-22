# Feature Handoff: R3 -- Proof & Operator Confidence

**Release:** R3 (Waves 1-4, spanning v3.2.1 through v3.4.0)
**Scope:** Trust hardening, explainability, quality proof, and lifecycle policies
**Audience:** Operators, CS teams
**Date:** 2026-03-23

---

## What R3 Is

R3 closes the gap between "Memories is collecting data" and "Memories is provably dependable." It ships across four waves, each building on the previous:

| Wave | Version | Theme | Summary |
|------|---------|-------|---------|
| **Wave 1** | v3.2.1 | Trust Hardening | Close auth gaps, add audit trails, fix namespace inconsistencies |
| **Wave 2** | v3.2.1 | Explainability | Show where memories came from, why extraction made decisions, and how strong the evidence is |
| **Wave 3** | v3.3.0 | Quality Proof | Benchmark retrieval quality, validate recovery paths, reduce unnecessary LLM calls |
| **Wave 4** | v3.4.0 | Lifecycle Policies | Automated memory expiration and archival with full evidence trail, confidence as a ranking signal |

After R3, operators can answer: "Is this system trustworthy, and can I prove it?"

---

## Wave 1: Trust Hardening (v3.2.1)

Wave 1 fixes 23 trust gaps found during the R2 review. Nothing here adds new features -- it tightens what already exists.

### Auth Hardening

**`/memory/is-novel` now requires authentication.** Before Wave 1, this endpoint was unprotected. Any caller could probe the store for content similarity without a key. It now goes through `_get_auth()` like every other endpoint.

**Read-only keys fail fast on delete.** Previously, a delete call from a read-only key would proceed partway before failing. It now returns 403 immediately, before any work is done.

**`AUTH_RATE_LIMIT` default set to 50/min.** The auth failure rate limiter (tracks failed authentication attempts per IP) defaults to 50 failed attempts per minute before returning 429. Configurable via the `AUTH_RATE_LIMIT` environment variable.

### Audit Trail Additions

Four maintenance operations previously ran without audit records:

| Operation | Audit Action |
|-----------|-------------|
| Consolidate (LLM-powered merge) | `maintenance.consolidated` |
| Prune | `maintenance.pruned` |
| Index build | `maintenance.index_built` |
| Deduplicate | `maintenance.deduplicated` |

These now emit audit events with the requesting user's source and IP.

### Namespace Consistency

The `delete` audit action has been renamed to `memory.deleted`. All memory-related audit actions now live under the `memory.*` namespace:

- `memory.created`
- `memory.updated`
- `memory.deleted`
- `memory.archived`
- `memory.unarchived`
- `memory.linked`
- `memory.pinned`
- `memory.unpinned`
- `memory.policy_archived` (added in Wave 4)

### UI Timeline Color Map

The lifecycle timeline in the detail panel now color-codes all audit actions. Before Wave 1, 9 actions fell through to the default gray because they were missing from the color map. The full map now covers:

| Action | Color |
|--------|-------|
| `memory.created`, `add` | Green (success) |
| `memory.updated`, `memory.linked`, `memory.unarchived`, `memory.unpinned` | Blue (info) |
| `memory.pinned` | Gold (primary) |
| `memory.archived` | Yellow (warning) |
| `memory.deleted`, `conflict.detected` | Red (error) |
| `maintenance.*` actions | Blue (info) |

### Stale Memories Label

The stale memories section on the Health page now shows both `useful` and `not_useful` feedback counts. Before Wave 1, it only showed the `not_useful` count, making it hard to gauge whether a memory was truly problematic or just rarely evaluated.

### "Replay" Renamed to "Re-search"

The navigation link on problem queries that sent users to the Memories page with a pre-filled search was labeled "Replay." This implied re-executing a past search session, which is not what it does -- it runs a new search with the same query text. The label is now "Re-search."

### Version String Sync

Version strings were inconsistent across surfaces (API `/health`, UI footer, Docker labels). All now report the same value from a single source of truth.

---

## Wave 2: Explainability (v3.2.1)

Wave 2 makes the system's decisions visible. Operators should never need to guess where a memory came from or why extraction made a particular choice.

### Extraction Origin Tracking

The lifecycle panel's origin block now correctly identifies extraction-created memories. Before Wave 2, some extracted memories showed "Manual add" because the metadata fields (`extraction_job_id`, `extract_source`) were not always set during the AUDN commit flow.

**How origin detection works:** The UI checks metadata fields in this order:

1. `extraction_job_id` or `extract_source` present -- shows "Extraction"
2. `supersedes` or `merged_from` present -- shows "Merge"
3. `imported` or `import_source` present -- shows "Import"
4. None of the above -- shows "Manual add"

### Import Origin Tracking

Memories created via `POST /import` now carry `imported: true` in their metadata. This lets the origin block correctly label them as "Import" instead of falling through to "Manual add."

### Audit Events for Links and Pins

Two lifecycle actions that previously ran silently now emit audit events:

| Action | Audit Event |
|--------|------------|
| Creating a link between memories | `memory.linked` |
| Pinning a memory | `memory.pinned` |
| Unpinning a memory | `memory.unpinned` |

These appear in the lifecycle timeline with appropriate color coding (blue for linked/unpinned, gold for pinned).

### Extraction Reasoning in UI

When reviewing extraction results in dry-run mode, the AUDN badge for NOOP, CONFLICT, and UPDATE actions now shows related memory IDs. This answers the question "why did extraction decide to skip/conflict/update instead of adding a new memory?"

- **NOOP:** Shows the ID of the existing memory that was considered too similar.
- **CONFLICT:** Shows the ID of the existing memory that contradicts the extracted fact.
- **UPDATE:** Shows the ID of the memory being updated.

### Evidence Strength Badges

Problem queries and stale memories on the Health page now display evidence strength badges based on feedback volume:

**Problem queries** (based on total feedback count):

| Badge | Threshold | Style |
|-------|-----------|-------|
| Strong | 10+ total feedback entries | Red (error) |
| Moderate | 5-9 total feedback entries | Yellow (warning) |
| Weak | Fewer than 5 entries | Gray (muted) |

**Stale memories** (based on `not_useful` count):

| Badge | Threshold | Style |
|-------|-----------|-------|
| Strong | 5+ not_useful signals | Red (error) |
| Moderate | 2-4 not_useful signals | Yellow (warning) |
| Weak | Fewer than 2 not_useful signals | Gray (muted) |

The badges help operators triage: a "Strong" evidence problem query is a confirmed retrieval issue; a "Weak" one might just be noise.

---

## Wave 3: Quality Proof (v3.3.0)

Wave 3 proves the system works with measurable benchmarks and validates that recovery paths are sound.

### LongMemEval Benchmark Adapter

LongMemEval is a 500-question evaluation benchmark for conversational memory systems. The adapter tests five capabilities:

| Category | Questions | What It Measures |
|----------|-----------|-----------------|
| Information Extraction | ~100 | Can the system store and retrieve factual data? |
| Multi-Session Reasoning | ~100 | Can it synthesize across conversation sessions? |
| Knowledge Update | ~100 | Does it handle contradictions (newer supersedes older)? |
| Temporal Reasoning | ~100 | Does it understand event sequences and time? |
| Abstaining | ~100 | Does it correctly decline when info is insufficient? |

**How it works:**

1. Downloads the LongMemEval dataset from HuggingFace (cached locally after first download)
2. Seeds the memory store with conversation histories via `POST /memory/extract` (tests the full extraction pipeline, not just retrieval)
3. Queries for each question via `POST /search` with hybrid mode
4. Judges answer correctness using a configurable LLM provider
5. Reports per-category and overall scores with regression delta against a previous run

**How to run it:**

```bash
# Full benchmark (~$0.50 with Haiku judge)
python -m memories eval longmemeval --judge-provider anthropic

# With specific model
python -m memories eval longmemeval --judge-provider anthropic --judge-model claude-haiku-4-5-20251001

# Compare against a previous run
python -m memories eval longmemeval --compare eval/results/longmemeval-v3.2.1.json

# Save results for future comparison
python -m memories eval longmemeval --output eval/results/longmemeval-v3.3.0.json
```

**Supported judge providers:** `anthropic`, `openai`, `ollama`

**Output example:**

```
LongMemEval v3.3.0 (2026-03-23)
Judge: anthropic/claude-haiku-4-5-20251001
Overall: 72.4% (+2.1% vs v3.2.1)
  Information Extraction: 78.0% (+3.0%)
  Multi-Session Reasoning: 65.2% (+1.5%)
  Knowledge Update: 71.0% (+2.0%)
  Temporal Reasoning: 68.5% (+1.2%)
  Abstaining: 79.3% (+2.8%)
```

Results are saved as JSON for regression tracking. Run the benchmark after each release to catch quality regressions before they reach operators.

The full 500-question benchmark is NOT run in CI (it is too expensive). Unit tests validate adapter logic with a small synthetic dataset.

### Signal Keyword Pre-Filter

Extraction hooks (Claude Code, Codex, Cursor) now check for signal keywords before sending conversation text to the LLM. If no signal keywords are found, the extraction is skipped entirely -- saving LLM cost and latency.

**Default signal keywords:** `decide`, `decision`, `chose`, `bug`, `fix`, `remember`, `architecture`, `convention`, `pattern`, `learning`, `mistake`

**Behavior by hook:**

| Hook | Signal Filter Applied? | Why |
|------|----------------------|-----|
| `memory-extract.sh` (per-prompt) | Yes | Routine extraction, most conversations do not contain memorable content |
| `memory-commit.sh` (session end) | Yes | Session-end extraction is less critical |
| `memory-flush.sh` (pre-compact) | No, always extracts | Context is about to be lost; extract aggressively |

**Customization:**

- Override via `MEMORIES_SIGNAL_KEYWORDS` environment variable (pipe-separated regex)
- Setting `MEMORIES_SIGNAL_KEYWORDS=""` (empty string) disables the filter -- all extractions proceed
- Case-insensitive matching

### Snapshot Round-Trip Validation

A test validates the full backup-restore cycle:

1. Add 5 memories with known text and source
2. Create a snapshot via API
3. Delete 3 memories
4. Verify only 2 remain
5. Restore from the snapshot
6. Verify all 5 are back with correct text and source
7. Verify search finds the restored memories

This gives confidence that the snapshot mechanism actually works end-to-end, not just that it creates files.

### Import/Export Round-Trip Validation

A test validates the full export-reimport cycle:

1. Add 5 memories with various metadata (pinned, archived, category, custom fields)
2. Export via `GET /export`
3. Delete all memories
4. Import via `POST /import?strategy=add`
5. Verify all 5 restored with correct text, source, and metadata
6. Verify search finds the imported memories
7. Additional: import via `strategy=smart` with duplicates -- verify dedup works
8. Additional: export with `source` filter -- verify only matching memories exported

---

## Wave 4: Lifecycle Policies (v3.4.0)

Wave 4 turns confidence decay from a display feature into a lifecycle mechanism. Memories that expire or lose confidence are automatically archived with full evidence trails.

### Per-Prefix TTL via Extraction Profiles

Add a `ttl_days` field to any extraction profile to set a time-to-live for memories under that source prefix:

```json
{
  "source_prefix": "wip/",
  "ttl_days": 30,
  "mode": "standard",
  "max_facts": 30
}
```

- `ttl_days: null` (default) -- no TTL, memories live forever
- `ttl_days: 30` -- memories older than 30 days under this prefix get archived when policies are enforced
- Age is computed from `updated_at` (if the memory was ever edited), falling back to `created_at`, then `timestamp`
- Inherits via profile cascade: `wip/memories/` inherits the `wip/` TTL unless explicitly overridden

### Confidence-Based Auto-Archive

Add `confidence_threshold` and `min_age_days` to a profile to archive memories whose confidence has decayed below the threshold:

```json
{
  "source_prefix": "claude-code/",
  "confidence_threshold": 0.1,
  "min_age_days": 90
}
```

- `confidence_threshold: null` (default) -- no confidence-based archival
- `min_age_days` is required when `confidence_threshold` is set -- this prevents archiving fresh memories that start with low confidence before they have had time to be accessed and reinforced
- Confidence is computed using the same `compute_confidence()` formula used everywhere else in the system (UI display, ranking, search explain)

### Per-Prefix Confidence Half-Life

A new `confidence_half_life_days` field on profiles controls the decay curve separately from the extraction `half_life_days`:

- Extraction `half_life_days` (default: 30) -- controls how aggressively extraction treats old facts
- `confidence_half_life_days` (default: null, falls back to engine default of 90) -- controls the confidence decay curve used in display, ranking, and policy enforcement

This prevents changing extraction behavior from silently altering all confidence scores across the system.

### Confidence as 5th RRF Signal

Confidence can now influence search ranking via the `confidence_weight` parameter on `POST /search`:

```json
{
  "query": "deployment conventions",
  "hybrid": true,
  "confidence_weight": 0.15
}
```

The five RRF signals and their defaults:

| Signal | Default Weight | What It Does |
|--------|---------------|--------------|
| Vector similarity | Scaled from remainder | Semantic match quality |
| BM25 keyword match | Scaled from remainder | Exact keyword relevance |
| Recency | 0.0 | Favor recently created/updated memories |
| Feedback | 0.0 | Favor memories with positive feedback history |
| Confidence | 0.0 | Favor memories with higher confidence (less decayed) |

All five signals always sum to 1.0 via automatic weight scaling. The `confidence_weight` param is also available on the MCP `memory_search` tool (default 0.0 -- opt-in).

### POST /maintenance/enforce-policies

This is the core lifecycle policy endpoint. It evaluates every non-archived, non-pinned memory against its resolved policy and archives those that match.

**Key design decisions:**

- **Dry-run by default.** The `dry_run` parameter defaults to `true`. You must explicitly pass `dry_run=false` to execute archival. This is deliberate -- operators should always preview before acting.
- **Admin only.** Requires an admin API key.
- **Pinned memories are excluded.** Pinning is a hard protection -- policies never override a pin.
- **Already-archived memories are skipped.**
- **TTL takes precedence.** If both TTL and confidence rules match the same memory, the reason is reported as TTL (it is the harder policy).
- **Archival is reversible.** Policy-archived memories can be unarchived via `PATCH /memory/{id}` with `archived: false`. No data is destroyed.

**How to use it:**

1. **Preview first (always):**

```bash
curl -X POST "https://your-instance/maintenance/enforce-policies" \
  -H "X-API-Key: YOUR_ADMIN_KEY"
```

The default `dry_run=true` returns what would happen without acting:

```json
{
  "dry_run": true,
  "actions": [
    {
      "memory_id": 42,
      "source": "wip/memories",
      "age_days": 45,
      "confidence": 0.35,
      "reasons": [
        {"rule": "ttl", "ttl_days": 30, "age_days": 45, "prefix": "wip/"}
      ],
      "action": "would_archive"
    },
    {
      "memory_id": 99,
      "source": "claude-code/old-project",
      "age_days": 180,
      "confidence": 0.08,
      "reasons": [
        {"rule": "confidence", "threshold": 0.1, "confidence": 0.08, "min_age_days": 90, "prefix": "claude-code/"}
      ],
      "action": "would_archive"
    }
  ],
  "summary": {
    "candidates_scanned": 500,
    "would_archive": 12,
    "by_rule": {"ttl": 8, "confidence": 4},
    "excluded_pinned": 2,
    "excluded_already_archived": 15
  }
}
```

2. **Execute when satisfied:**

```bash
curl -X POST "https://your-instance/maintenance/enforce-policies?dry_run=false" \
  -H "X-API-Key: YOUR_ADMIN_KEY"
```

The response is the same structure but `action` fields read `"archived"` instead of `"would_archive"`. Each archived memory gets an audit event (`memory.policy_archived`).

3. **Set up scheduled enforcement (optional):**

The endpoint itself does not schedule. Wire it to an external cron or scheduler:

```bash
# Example: run policy enforcement daily at 3am
0 3 * * * curl -s -X POST "http://localhost:8000/maintenance/enforce-policies?dry_run=false" -H "X-API-Key: $ADMIN_KEY" >> /var/log/memories-policy.log
```

### Policy Evidence (Protected Metadata)

When a memory is archived by policy enforcement, evidence is stored in its metadata under the `_policy_*` namespace:

| Field | Value |
|-------|-------|
| `_policy_archived_reason` | `"ttl"` or `"confidence"` |
| `_policy_archived_policy` | Human-readable rule string, e.g., `"wip/ ttl_days=30"` |
| `_policy_archived_at` | ISO 8601 timestamp of when the policy archived it |
| `_policy_archived_confidence` | Confidence score snapshot at the time of archival |
| `_policy_archived_age_days` | Age in days at the time of archival |

**These fields are protected.** Ordinary `PATCH /memory/{id}` calls with `metadata_patch` cannot overwrite or clear `_policy_*` fields. Only the policy enforcement engine writes them. This prevents accidental evidence destruction. Operators can read the evidence in the detail panel but cannot tamper with it.

---

## API Endpoints Table

All new or modified endpoints across R3.

| Method | Endpoint | Wave | Auth | Description |
|--------|----------|------|------|-------------|
| `POST` | `/memory/is-novel` | W1 | Any valid key | Check text novelty (auth added in W1) |
| `POST` | `/maintenance/enforce-policies` | W4 | Admin only | Enforce TTL and confidence archival policies |
| `POST` | `/search` | W4 | Any valid key | New param: `confidence_weight` (float, 0.0-1.0) |
| `GET` | `/audit` | W1 | Admin | New actions: `memory.deleted`, `maintenance.consolidated`, `maintenance.pruned`, `maintenance.index_built`, `maintenance.deduplicated`, `memory.linked`, `memory.pinned`, `memory.unpinned`, `memory.policy_archived` |
| `GET` | `/export` | W3 | Admin | Round-trip validated (tested with reimport) |
| `POST` | `/import` | W3 | Admin | Sets `imported: true` metadata, round-trip validated |
| `POST` | `/snapshots/{name}/restore` | W3 | Admin | Round-trip validated (tested with mutation + restore) |

**CLI commands:**

| Command | Wave | Description |
|---------|------|-------------|
| `memories eval longmemeval` | W3 | Run LongMemEval benchmark |
| `memories eval longmemeval --judge-provider anthropic` | W3 | Specify judge LLM provider |
| `memories eval longmemeval --compare <file>` | W3 | Compare against previous results |
| `memories eval longmemeval --output <file>` | W3 | Save results to file |

**MCP tool changes:**

| Tool | Wave | Change |
|------|------|--------|
| `memory_search` | W4 | New `confidence_weight` parameter (float, 0.0-1.0, default 0.0) |

**Environment variables:**

| Variable | Wave | Default | Description |
|----------|------|---------|-------------|
| `AUTH_RATE_LIMIT` | W1 | `50` | Max failed auth attempts per IP per minute before 429 |
| `MEMORIES_SIGNAL_KEYWORDS` | W3 | `decide\|decision\|chose\|bug\|fix\|remember\|architecture\|convention\|pattern\|learning\|mistake` | Signal keywords for extraction pre-filter (empty string disables filter) |

**Extraction profile fields (new):**

| Field | Wave | Default | Description |
|-------|------|---------|-------------|
| `ttl_days` | W4 | `null` | Time-to-live in days for memories under this prefix |
| `confidence_threshold` | W4 | `null` | Archive when confidence drops below this value |
| `min_age_days` | W4 | `null` | Required with `confidence_threshold`; prevents archiving young memories |
| `confidence_half_life_days` | W4 | `null` (falls back to 90) | Decay half-life for confidence calculation (separate from extraction half-life) |

---

## Common Questions

**Q1: Does enforce-policies delete memories permanently?**

No. Policy enforcement archives memories -- it sets `archived: true`. Archived memories are excluded from search results but remain in the store. You can unarchive any policy-archived memory via `PATCH /memory/{id}` with `{ "archived": false }`. The `_policy_*` evidence metadata is preserved even after unarchiving, so you can see why it was archived.

**Q2: What happens if I run enforce-policies with no profiles configured?**

Nothing. If no profiles have `ttl_days` or `confidence_threshold` set, the endpoint scans all memories but finds zero candidates. The response shows `"would_archive": 0` (dry run) or `"archived": 0` (live run). It is safe to call with default profiles.

**Q3: Can the signal keyword filter cause missed extractions?**

Yes, by design. The filter trades recall for cost savings. If a conversation contains memorable content but none of the signal keywords, extraction is skipped. To mitigate: customize `MEMORIES_SIGNAL_KEYWORDS` with domain-specific terms, or set it to an empty string to disable the filter entirely. The pre-compact hook (`memory-flush.sh`) always extracts regardless of keywords, so context that is about to be lost is still captured.

**Q4: How much does a LongMemEval run cost?**

Approximately $0.50 with Claude Haiku as the judge for the full 500-question benchmark. The cost scales with the judge model -- Haiku is cheapest, Sonnet and Opus are more expensive. The benchmark seeds memories via the extraction pipeline, so extraction LLM costs apply as well (~$1-3 depending on model and conversation volume).

**Q5: What is the difference between `half_life_days` and `confidence_half_life_days` on profiles?**

`half_life_days` (default 30) controls how the extraction engine treats age when deciding AUDN actions -- it affects the extraction prompt's context about memory freshness. `confidence_half_life_days` (default 90 via engine fallback) controls the exponential decay curve for the confidence score displayed in the UI, used in search ranking, and used by policy enforcement. They serve different purposes and intentionally have different defaults.

**Q6: Why are `_policy_*` metadata fields protected from writes?**

Policy evidence must remain intact for audit and compliance. If an operator could clear `_policy_archived_reason` via a regular PATCH call, there would be no way to distinguish "archived by policy" from "archived manually." The protection is write-only -- operators can read all `_policy_*` fields in the UI detail panel and via the API.

**Q7: How does evidence strength help me triage the Health page?**

Start with "Strong" badge items. A problem query with strong evidence (10+ feedback entries, mostly negative) is a confirmed retrieval gap -- investigate the query, check if relevant memories exist, and consider adding or updating content. A stale memory with strong evidence (5+ not_useful signals) is a confirmed noise source -- consider archiving or editing it. "Weak" evidence items may resolve on their own as more feedback accumulates.

**Q8: If I set `confidence_weight` on search, does it change the confidence scores themselves?**

No. Confidence weight only affects ranking order. A memory's confidence score is computed from its age and access history using `compute_confidence()`. Setting `confidence_weight: 0.15` means "give higher-confidence memories a 15% boost in the ranking formula." The scores themselves are unaffected.

**Q9: Will enforce-policies archive a pinned memory?**

Never. Pinned memories are unconditionally excluded from policy evaluation. If a memory matches a TTL or confidence rule but is pinned, it is reported in the `excluded_pinned` count but not archived. To subject a pinned memory to policies, unpin it first.

**Q10: Can I see which memories were archived by policy?**

Yes, three ways:
1. **Audit log:** Filter by action `memory.policy_archived` in `GET /audit`.
2. **Memory metadata:** Look for `_policy_archived_reason` in the memory's metadata (visible in the detail panel or via API).
3. **enforce-policies response:** The response from the enforcement call lists every archived memory with its reasons and evidence.

**Q11: Does the LongMemEval benchmark test against my production data?**

It seeds its own test data. The benchmark creates memories from the LongMemEval conversation histories via the extraction pipeline, then queries against them. It uses a configurable source prefix to avoid polluting your production memories. Run it against a test instance or use a dedicated source prefix and clean up afterward.

**Q12: What is the auth rate limit actually protecting against?**

Brute-force API key guessing. The rate limiter tracks failed authentication attempts (invalid or missing API keys) per client IP address. After 50 failures within a 60-second window, all subsequent requests from that IP receive 429 (Too Many Requests) until the window expires. Successful authentications are not counted.

---

## Troubleshooting

### enforce-policies shows 0 candidates but I expect archival

**Check 1:** Verify your extraction profiles have `ttl_days` or `confidence_threshold` set. The default profile has both as `null` (disabled). Profiles are in `extraction_profiles.json` or configured via API.

**Check 2:** Verify the source prefix on your profiles matches the memories you expect to archive. Profile resolution uses longest-prefix matching: a profile for `wip/` matches memories with source `wip/memories` or `wip/tasks`, but not `claude-code/wip/`.

**Check 3:** Check if the target memories are pinned. Pinned memories are always excluded. Look at the `excluded_pinned` count in the summary.

**Check 4:** Check if the target memories are already archived. Already-archived memories are skipped. Look at `excluded_already_archived`.

**Check 5:** For confidence-based archival, verify both `confidence_threshold` AND `min_age_days` are set. If `min_age_days` is missing, confidence-based archival does not activate.

### LongMemEval benchmark fails to download dataset

The adapter downloads from HuggingFace on first run and caches to `eval/scenarios/longmemeval/`. If the download fails:

**Check:** Verify network connectivity to HuggingFace. If behind a proxy, set `HTTPS_PROXY` in your environment. If the cache directory exists but is corrupted, delete it and re-run.

### Signal keyword filter blocks all extractions

If no extraction jobs are running after upgrading to v3.3.0:

**Check:** The default keyword list is conservative. If your domain uses different terminology for memorable content (e.g., medical, legal, financial terms), override `MEMORIES_SIGNAL_KEYWORDS` with domain-appropriate terms. To disable the filter entirely, set `MEMORIES_SIGNAL_KEYWORDS=""`.

**Note:** The pre-compact hook (`memory-flush.sh`) is not affected -- it always extracts.

### Evidence strength badges do not appear on Health page

The badges require feedback data. If you see problem queries or stale memories without badges:

**Check:** The Health page views (`/health`) are admin-only. Verify your API key has admin access. Non-admin keys receive filtered or empty results from the underlying endpoints.

### Audit timeline shows "No history available" after upgrade

Audit events from before Wave 1 may use the old `delete` action name instead of `memory.deleted`. The timeline still renders these -- they fall through to the default color. New events use the correct namespace.

If all memories show "No history available," verify your API key is admin-level. The `GET /audit` endpoint requires admin access.

### confidence_weight has no visible effect on search results

**Check 1:** Verify `confidence_weight` is not 0.0 (the default). Set it to a value like 0.1 or 0.15.

**Check 2:** If all memories in the result set have similar confidence scores (e.g., all near 1.0 for recently accessed memories), the confidence signal will not differentiate between them. Confidence ranking is most useful when the result set contains a mix of fresh and decayed memories.

**Check 3:** Run `POST /search` with `explain: true` and verify the `confidence` signal appears in `scoring_weights` with a non-zero value.

### Policy-archived memories still appear in search

Archived memories are excluded from search by default. If they still appear:

**Check:** The `archived: true` flag must be set on the memory's payload in Qdrant. Verify by calling `GET /memory/{id}` and checking the `archived` field. If `enforce-policies` was interrupted mid-execution (server restart during a live run), some memories may have been partially processed. Re-run the enforcement with `dry_run=false` to complete the batch.

### Extraction origin shows "Manual add" for extracted memories

Some memories created before Wave 2 may lack the `extraction_job_id` or `extract_source` metadata fields. The origin detection falls through to "Manual add" for these. This is cosmetic -- the memories themselves are unchanged. Only memories created after v3.2.1 will reliably show extraction origin.
