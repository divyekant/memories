# Memories: Master Improvement Plan

> Created: 2026-03-15
> Last updated: 2026-03-15
> Status: Active — 10 of 16 items delivered, 4 PRs pending merge

---

## Tier 1: High Impact, Ship Soon

### 1. Release v2.1.0 — Security Hardening
- **Status**: SHIPPED (tagged, pushed 2026-03-15)
- **Effort**: Small (work is done, needs release cut)
- **What**: Ship the unreleased scoped-auth fixes sitting in main
  - Extraction AUDN scoping to allowed prefixes
  - Per-memory source auth on delete-by-source
  - Scope-correct counts for scoped keys
  - Admin-only usage/backups endpoints
  - Codex notify parity (transcript fallback, source overrides)
- **Why**: Closes the known cross-prefix update/delete vulnerability
- **Depends on**: Nothing — merge current docs branch, tag, release

---

### 2. Qdrant Payload Filtering Before Vector Search
- **Status**: MERGED (PR #22)
- **Effort**: Medium (2-3 sessions)
- **What**: Push source/timestamp/folder filtering into Qdrant query filters instead of post-filtering in Python
  - Use Qdrant `models.Filter` with `FieldCondition` on `source`, `created_at`, `folder`
  - Replace O(n) `_count_accessible_memories()` scan with Qdrant filtered count
  - Index payload fields in Qdrant collection config
- **Why**: Biggest performance win for scoped keys; eliminates the app.py:294 TODO
- **Depends on**: v2.1.0 released (clean baseline)
- **Decision needed**: Whether to make this a breaking migration or auto-migrate on startup

---

### 3. Temporal Search Weighting (Recency Boost)
- **Status**: MERGED (PR #22)
- **Effort**: Medium (1-2 sessions)
- **What**: Add configurable recency decay to RRF fusion scoring
  - New parameter: `recency_weight` (0.0 = no boost, 1.0 = heavily favor recent)
  - Decay function: exponential with configurable half-life (default 30 days)
  - Applied as a third signal in RRF alongside vector and BM25 scores
- **Why**: "What did we decide about X last week?" should prefer recent memories
- **Depends on**: Item 2 (payload filtering gives us timestamp access in queries)
- **Decision needed**: Default recency weight — 0.0 (opt-in) or 0.3 (mild default)?

---

### 4. Memory Relationships (Lightweight Graph)
- **Status**: MERGED (PR #23)
- **Effort**: Large (3-4 sessions)
- **What**: Add typed edges between memories stored as Qdrant payload metadata
  - Edge types: `supersedes`, `related_to`, `blocked_by`, `caused_by`, `reinforces`
  - API: `POST /memory/{id}/link`, `GET /memory/{id}/related`, `DELETE /memory/{id}/link/{link_id}`
  - MCP tool: `memory_related` — retrieve context graph for a memory
  - Generalize existing `supersede` endpoint into this system
- **Why**: Transforms flat fact store into knowledge graph; enables "what led to this?" queries
- **Depends on**: Item 2 (payload filtering for efficient edge traversal)

---

## Tier 2: High Impact, Larger Scope

### 5. Conflict Detection in Extraction
- **Status**: MERGED (PR #24)
- **Effort**: Medium (2 sessions)
- **What**: Add contradiction-aware step in AUDN extraction pipeline
  - After similarity search, check if top matches assert incompatible facts
  - New AUDN action: `CONFLICT` — stores both, flags for human resolution
  - Surface conflicts in Web UI and MCP tool
- **Why**: "We chose Postgres" vs "We chose SQLite" should be flagged, not silently updated
- **Depends on**: Item 4 (relationships needed to link conflicting memories)

---

### 6. Memory Lifecycle / Confidence Decay
- **Status**: MERGED (PR #25)
- **Effort**: Medium (2 sessions)
- **What**: Add confidence score that decays over time and reinforces on access
  - New field: `confidence` (0.0-1.0), initialized at 1.0
  - Decay: configurable half-life (default 90 days)
  - Reinforcement: accessing a memory in search results bumps confidence
  - Search ranking incorporates confidence as a signal
- **Why**: Old unreinforced memories shouldn't carry same weight as validated recent ones
- **Depends on**: Item 3 (temporal weighting infrastructure)

---

### 7. Event-Driven Architecture (Webhooks/SSE)
- **Status**: PR #26 (review fixes pushed, ready to merge)
- **Effort**: Large (3-4 sessions)
- **What**: Replace 5-minute polling with event system
  - Internal event bus: `memory.added`, `memory.updated`, `memory.deleted`, `extraction.completed`
  - `POST /webhooks` — register callback URLs with event filters
  - `GET /events/stream` — SSE endpoint for real-time subscribers
  - Migrate MEMORY.md hydration from polling to event-driven
- **Why**: Enables real-time integrations without polling overhead
- **Depends on**: v2.1.0 released

---

### 8. Embedding Model Migration Path
- **Status**: PR #27 (pending review)
- **Effort**: Medium (2 sessions)
- **What**: Tooling to upgrade embedding models without downtime
  - `POST /maintenance/reembed` — batch re-embed all memories with new model
  - Progress tracking via job ID (like extraction)
  - Dual-index support during migration (query both, merge results)
  - Rollback if new model performs worse
- **Why**: Locked to all-MiniLM-L6-v2 forever without this; better models exist
- **Depends on**: Nothing (can start anytime)

---

### 9. Python Client SDK
- **Status**: PR #28 (pending review)
- **Effort**: Medium (2-3 sessions)
- **What**: `memories-client` package wrapping the HTTP API
  - Type-safe methods for all endpoints
  - Async support (httpx-based)
  - Used internally by CLI, eval harness, hooks
  - Publishable to PyPI
- **Why**: Reduces duplication across CLI/MCP/hooks/eval; enables third-party integrations
- **Depends on**: API stability (after v2.1.0)

---

## Tier 3: Polish & Scale

### 10. Memory Compaction / Summarization
- **Status**: PR #29 (pending review)
- **Effort**: Medium (2 sessions)
- **What**: Maintenance operation that consolidates related memories
  - `POST /maintenance/compact` — find clusters of similar memories, LLM-summarize into one
  - Preserve original IDs as `absorbed_by` links
  - Configurable similarity threshold for grouping
- **Why**: 15 incremental memories about "auth architecture" degrade search precision
- **Depends on**: Item 4 (relationships to track absorption), Item 5 (conflict detection)

---

### 11. Search Quality Feedback Loop
- **Status**: NOT STARTED
- **Effort**: Small-Medium (1-2 sessions)
- **What**: Capture relevance signals to tune search ranking
  - Implicit: memory accessed → used in response → no user correction = positive signal
  - Explicit: optional thumbs-up/down on search results (MCP + Web UI)
  - `GET /metrics/search-quality` — precision/recall estimates over time
- **Why**: No way to know if search results are actually useful without feedback
- **Depends on**: Nothing (can start anytime)

---

### 12. Extraction Quality Dashboard
- **Status**: NOT STARTED
- **Effort**: Small (1 session)
- **What**: Surface ADD/UPDATE/DELETE/NOOP ratios in Web UI and metrics
  - Time-series chart of extraction outcomes
  - Alert when NOOP rate exceeds threshold (trigger too aggressive)
  - Per-source breakdown
- **Why**: Data exists in pipeline but isn't surfaced; enables extraction prompt tuning
- **Depends on**: Nothing (data already collected)

---

### 13. Audit Log for Multi-User
- **Status**: NOT STARTED
- **Effort**: Medium (2 sessions)
- **What**: Append-only SQLite audit trail
  - Fields: timestamp, key_id, action, resource_id, source_prefix, ip
  - `GET /audit` — query with filters (admin-only)
  - Retention policy (configurable, default 90 days)
- **Why**: Essential for team use; no visibility into who accessed/modified what
- **Depends on**: v2.1.0 (multi-auth must be solid first)

---

### 14. Load Testing Harness
- **Status**: NOT STARTED
- **Effort**: Small (1 session)
- **What**: k6 or locust profiles for concurrent AI assistant simulation
  - Scenarios: concurrent search, batch add under load, extraction queue saturation
  - Characterize the single-process lock-guarded write model limits
  - CI-integrated with regression thresholds
- **Why**: Eval harness tests quality but not throughput; lock contention is untested
- **Depends on**: Nothing

---

## Architecture Bets (v3.0)

### 15. Plugin System for Extractors and Rankers
- **Status**: NOT STARTED
- **Effort**: Large (4+ sessions)
- **What**: `class Extractor(Protocol)` / `class Ranker(Protocol)` plugin interfaces
  - Drop-in custom extractors (domain-specific fact patterns)
  - Custom rankers (e.g., org-specific boosting rules)
  - Plugin discovery via entry points or config
- **Why**: Extraction prompt and ranker are hardcoded; forks are the only extension path today

---

### 16. Cross-Project Knowledge Sharing
- **Status**: NOT STARTED
- **Effort**: Large (3-4 sessions)
- **What**: Controlled cross-prefix read access
  - ACL rules: "key B can read from project A's `learning/` prefix"
  - `shared/` prefix convention for org-wide knowledge
  - Federated search across allowed prefixes
- **Why**: Source prefixes isolate cleanly but can't share; blocks organizational knowledge bases

---

## Previously Identified Roadmap Items (from memories)

These were tracked separately and should be folded into the plan above or kept as-is:

- **Auto-rebuild watch mode** — relates to Item 7 (event-driven)
- **Multi-index support** — relates to Item 8 (embedding migration)
- **Memory tagging** — can be a standalone small feature or part of Item 4 (relationships)
- **Date filters on search** — subsumed by Item 2 (Qdrant payload filtering)
- **Scheduled rebuilds** — exists as `MAINTENANCE_ENABLED` env var; may need polish

---

## Execution Order

```
v2.1.0 ──→ #2 Qdrant Filtering ──→ #3 Temporal Weight ──→ #4 Relationships
                                          │                       │
                                          ├── #12 Extract Dashboard (parallel)
                                          ├── #14 Load Testing (parallel)
                                          │
                                          ▼                       ▼
                                    #6 Confidence Decay    #5 Conflict Detection
                                                                  │
                                          #7 Events ◄─────────────┘
                                          #8 Embed Migration (parallel anytime)
                                          #9 SDK (parallel after API stable)
                                                │
                                                ▼
                                    #10 Compaction ──→ #11 Feedback Loop
                                    #13 Audit Log
                                                │
                                                ▼
                                        v3.0: #15 Plugins, #16 Cross-Project
```
