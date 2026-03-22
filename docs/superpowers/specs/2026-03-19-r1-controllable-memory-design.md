# R1: Controllable Memory — Design Spec

## Context

v3.0.0 shipped with API-level features (links, search explain, extract debug, events/webhooks, quality metrics, re-embed, compact, audit log) and a dashboard that surfaces them. The functional scope document (`docs/plans/2026-03-18-memories-functional-scope.md`) defines the next phase: making memory objects and write decisions inspectable and editable.

**Product thesis:** Self-hosted memory workbench for serious agent teams. The UI is a first-class operator workbench, not optional polish.

**Existing pages:** Dashboard, Memories, Extractions, API Keys, Health, Settings

**Design tokens:** Kalos dark brand — primary `#d4af37`, background `#0a0a0a`, elevated `#111111`, text `#e5e5e5`, muted `#a3a3a3`, faint `#666666`, border `rgba(212,175,55,0.15)`, success `#16A34A`, warning `#CA8A04`, error `#DC2626`, info `#2563EB`. Font: Inter. Spacing: 4px base. Radii: 4/8/12px.

**Approach:** Engine-first. Build backend capabilities in B1 and B2, then layer UI in B3. No new pages — all changes are modifications to existing `webui/app.js`, `webui/styles.css`, and `webui/index.html`.

## Batching Strategy

| Batch | Items | Theme | Dependency |
|-------|-------|-------|------------|
| **B1: Safety + Foundations** | 3 engine + 1 CLI | Pre-delete snapshots, pin/protect, soft archive, links CLI | None |
| **B2: Extraction Engine** | 5 engine | Extraction profiles, per-source rules, single-call mode, dry-run, missed memory | None (parallel with B1) |
| **B3: UI Write-Path** | 8 UI | Create, edit, link, merge, bulk ops, extraction trigger, lifecycle panel, conflict resolution | B1 + B2 |

CLI commands for search-explain, quality-metrics, and audit-log are deferred to post-R3 — the underlying APIs will change in R2/R3.

---

## B1: Safety + Foundations

### Item 16: Pre-Delete Qdrant Snapshots

**Files:** `memory_engine.py`, `qdrant_store.py`, `app.py`

**QdrantStore methods** (new in `qdrant_store.py`):
```python
def create_snapshot(self) -> str:
    """POST /collections/{collection}/snapshots. Returns snapshot name.
    For remote Qdrant: uses HTTP API.
    For local/embedded Qdrant (path mode): falls back to filesystem
    copy of the storage directory. Returns path instead of snapshot name."""

def list_snapshots(self) -> List[Dict]:
    """GET /collections/{collection}/snapshots. Returns list of
    {name, creation_time, size}. Local mode: lists backup dirs."""

def restore_snapshot(self, name: str) -> None:
    """PUT /collections/{collection}/snapshots/{name}/recover.
    Local mode: restores from filesystem backup."""
```

**Engine changes:**

New method on `MemoryEngine`:
```python
def _snapshot_before_delete(self, reason: str) -> str:
    """Create snapshot before destructive operation.
    Delegates to self.qdrant_store.create_snapshot().
    Stores metadata (timestamp, reason, point_count) in /data/snapshots/manifest.json.
    Returns snapshot name/path."""
```

Wrap these methods with auto-snapshot:
- `delete_by_source(source_pattern, skip_snapshot=False)`
- `delete_by_prefix(source_prefix, skip_snapshot=False)`
- `delete_memories(memory_ids, skip_snapshot=False)` — only when `len(ids) > 10`

**Dry-run mode:**

Add `dry_run: bool = False` to `delete_by_source`, `delete_by_prefix`, `delete_memories`. When true, returns `{"would_delete": [ids], "count": N}` without executing.

**API endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/snapshots` | List snapshots with metadata |
| `POST` | `/snapshots` | Create manual snapshot |
| `POST` | `/snapshots/{name}/restore` | Restore from snapshot |

**MCP:** `memory_delete_by_source` and `memory_delete_batch` auto-snapshot with no opt-out from agents.

### Item 12: Pin/Protect

**Files:** `memory_engine.py`, `app.py`, `llm_extract.py`

**Schema change:** `pinned` and `archived` are **top-level fields on `PatchMemoryRequest`** (not metadata), because they have behavioral side effects (filter exclusion, delete protection). Add to Pydantic model:
```python
class PatchMemoryRequest(BaseModel):
    text: Optional[str] = None
    source: Optional[str] = None
    metadata_patch: Optional[Dict] = None
    pinned: Optional[bool] = None    # NEW
    archived: Optional[bool] = None  # NEW
```

Update `update_memory()` in `memory_engine.py` to accept `pinned` and `archived` kwargs and store them in the Qdrant point payload via `set_payload()`.

**Qdrant payload:** `pinned` stored in point payload (not just metadata dict). No payload index needed — only checked during delete operations, not filtered at query time.

**Behavior:**
- `PATCH /memory/{id}` accepts `{"pinned": true/false}`
- Pinned memories excluded from bulk delete operations (`delete_by_source`, `delete_by_prefix`)
- Pinned memories excluded from extraction AUDN `DELETE` and `UPDATE` actions (extraction can still `NOOP`)
- `GET /memory/list` gains `pinned` filter param
- Audit log: emit `pinned`/`unpinned` events

**Migration:** No backfill needed. All filter logic uses positive match (`pinned == true`) rather than negative (`pinned != false`), so memories without the field are unaffected.

### Item 13: Soft Archive

**Files:** `memory_engine.py`, `app.py`, `qdrant_store.py`

**Schema change:** `archived` stored in both metadata dict AND Qdrant point payload (needed for Qdrant-level filter exclusion).

**Qdrant changes:**
- Add `archived` to `_point_payload()` output
- Create KEYWORD payload index on `archived` field in `ensure_payload_indexes()`
- Extend `_build_source_filter()` to add `archived == true` exclusion by default, with `include_archived` override

**Behavior:**
- `PATCH /memory/{id}` accepts `{"archived": true/false}` — reversible
- Archived memories excluded from `search()` and `hybrid_search()` via Qdrant filter `archived != true`. Use positive match filter: `must_not: [{key: "archived", match: {value: true}}]` — this correctly handles points that lack the `archived` field (they pass the filter)
- New param on search endpoints: `include_archived: bool = False`
- `GET /memory/list?archived=true` to browse archived items
- New endpoint: `POST /memory/archive-batch` with `{"ids": [...]}`
- Archived memories excluded from extraction AUDN operations
- Audit log: emit `archived`/`unarchived` events

### Item 34: CLI Links Command

**Files:** New `cli/commands/links.py`, modify `cli/__init__.py`

**Commands:**
- `memories links list <memory_id>` — show all links (incoming + outgoing)
- `memories links add <from_id> <to_id> --type related_to` — create link
- `memories links remove <from_id> <to_id> --type related_to` — delete link

Uses existing API endpoints: `GET /memory/{id}/links`, `POST /memory/{id}/link`, `DELETE /memory/{id}/link/{target_id}`.

---

## B2: Extraction Engine

### Item 1: Extraction Profiles Per Source

**Files:** New `extraction_profiles.py`, modify `llm_extract.py`, `app.py`

**Storage:** JSON config at `/data/extraction_profiles.json`.

**Schema per profile:**
```json
{
  "source_prefix": "claude-code/",
  "mode": "aggressive|standard|conservative",
  "max_facts": 30,
  "max_fact_chars": 500,
  "half_life_days": 30,
  "single_call": false,
  "enabled": true,
  "rules": {}
}
```

- `conservative` mode: only `decision` and `learning` categories, skip `detail`
- Profiles cascade: `claude-code/memories/` inherits from `claude-code/` unless overridden
- `run_extraction()` looks up profile by source prefix, falls back to global defaults

**API endpoints:**

Profile prefix contains slashes (e.g., `claude-code/memories/`). Use FastAPI path type: `prefix: str = Path(...)` with `{prefix:path}` to capture the full path.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/extraction/profiles` | List all profiles |
| `GET` | `/extraction/profiles/{prefix:path}` | Get specific profile |
| `PUT` | `/extraction/profiles/{prefix:path}` | Create/update profile |
| `DELETE` | `/extraction/profiles/{prefix:path}` | Delete profile |

### Item 2: User-Definable Extraction Rules

**Files:** `extraction_profiles.py`, `llm_extract.py`

**Schema extension** — `rules` field on profile:
```json
{
  "rules": {
    "always_remember": ["API contracts", "architectural decisions"],
    "never_remember": ["temp debugging state", "WIP commit hashes"],
    "custom_instructions": "Treat all port numbers as durable facts"
  }
}
```

**Prompt injection:** Rules injected into AUDN prompt as `## Project-Specific Rules` section before similar-memories context. `always_remember` biases toward `ADD`, `never_remember` biases toward `NOOP`, `custom_instructions` is free-text appended.

### Item 3: Single-Call Extraction Mode

**Files:** `llm_extract.py`

**New function:** `extract_and_decide_single_call(provider, messages, source, engine, ...)`

Combines fact extraction + AUDN into one LLM call with a merged prompt. Returns same `List[{"action", "fact", ...}]` shape as `run_audn()`.

**Trade-off:** Less accurate (no per-fact similar-memory lookup), ~50% cost/latency reduction.

**Activation:** Profile field `single_call: bool = false`. `run_extraction()` dispatches based on profile.

### Item 4: Extraction Dry-Run

**Files:** `app.py`, `llm_extract.py`

`POST /memory/extract` gains `dry_run: bool = false`. When true:
- Runs full pipeline (both stages)
- Skips `execute_actions()`
- Returns action list showing what would happen
- Same job polling pattern via `GET /memory/extract/{job_id}`
- No side effects: no Qdrant writes, no events, no audit log

**Commit endpoint** for selective execution after dry-run:

`POST /memory/extract/commit` with:
```json
{
  "actions": [
    {"action": "ADD", "fact": {"text": "...", "category": "decision"}, "approved": true},
    {"action": "UPDATE", "old_id": 42, "fact": {...}, "new_text": "...", "approved": true},
    {"action": "CONFLICT", "fact": {...}, "approved": false}
  ],
  "source": "claude-code/memories"
}
```

Executes only actions where `approved: true`. Calls `execute_actions()` with the filtered list. This avoids re-running the LLM (which could produce different results).

### Item 5: Missed Memory Capture Flow

**Files:** `app.py`, `memory_engine.py`, `mcp-server/index.js`

**API:** `POST /memory/missed` with:
```json
{
  "text": "the thing I wish it remembered",
  "source": "claude-code/memories",
  "context": "optional conversation snippet"
}
```

**Two effects:**
1. Stores memory directly (like `/memory/add` but tagged with `origin: "missed_capture"` metadata)
2. Increments `missed_count` per source in extraction quality metrics

**Response:**
```json
{"id": 12345, "source": "claude-code/memories", "missed_count": 3}
```

**MCP tool:** `memory_missed` — agents flag missed memories during sessions.

---

## B3: UI Write-Path

All changes to existing `webui/app.js`, `webui/styles.css`. No new pages.

### Item 6: Create Memory from UI

**Location:** Memories page topbar

- `+ Create` button (primary gold) in topbar next to search
- Opens modal: textarea for text, source input (prefilled with last-used), category dropdown (`decision`/`learning`/`detail`)
- Submits `POST /memory/add` with `{text, source, metadata: {category}}` — category is a metadata field, not top-level
- On success: toast, invalidate cache, refresh list, select new memory
- Empty state on Memories page gets "Create your first memory" CTA

### Item 7: Inline Edit

**Location:** Memories detail panel

- Click-to-edit on memory text: `<div>` transforms to `<textarea>` with save/cancel
- Click-to-edit on source and category (dashed underline indicates editable)
- Save sends `PATCH /memory/{id}`
- Pin toggle badge next to ID — clicks `PATCH /memory/{id}` with `{pinned: true/false}`
- Archive button in actions row — patches `{archived: true}`, shows action toast with "Undo" button (re-patches to `false`). Requires extending `showToast()` to accept optional `action: {label, onClick}` param alongside the existing string+type signature.

### Item 8: Link Memories

**Location:** Detail panel linked memories section (already partially built)

**Enhancements to existing:**
- Link modal search results show source + text + confidence bar
- After adding, linked section auto-expands with new link
- Bidirectional display: outgoing and incoming links with direction indicator

### Item 9: Merge Memories

**Backend (ships in B1 alongside archive):** New `POST /memory/merge` endpoint:
```json
{"ids": [id1, id2], "merged_text": "...", "source": "..."}
```
Creates new memory, links to originals via `supersedes`, archives originals. Returns `{"id": new_id, "archived": [id1, id2]}`.

Engine method: `MemoryEngine.merge_memories(ids, merged_text, source)` — added in B1 since it depends on `add_link()` and soft archive.

**UI (B3):**
- Available when 2+ memories selected in multi-select mode
- Merge modal shows source memories (red left border, "will be archived") and editable merged result (green left border)
- Preview before confirm
- "Merge & Archive Originals" button

### Item 10: Bulk Actions

**Location:** Memories page toolbar

- "Select" toggle enables multi-select mode with checkboxes on cards
- "Select all on page" checkbox in header
- Bulk actions bar appears when items selected: count badge + Archive / Delete / Retag / Re-source / Merge buttons
- Archive: `POST /memory/archive-batch`
- Delete: `POST /memory/delete-batch` with confirmation modal + auto-snapshot
- Retag: modal with category dropdown, applies `PATCH` to each
- Re-source: modal with source input, applies `PATCH` to each

### Item 11: Extraction Trigger from UI

**Location:** Topbar "Extract" button (secondary style), accessible from Memories and Extractions pages

- Modal with: textarea for pasting text, source input, mode toggle (standard/aggressive/conservative), dry-run checkbox (checked by default)
- Submits `POST /memory/extract`, polls `GET /memory/extract/{job_id}`
- Results shown as reviewable list: each fact with AUDN action badge (ADD/UPDATE/DELETE/NOOP/CONFLICT) and ✓/✕ approve/reject toggles
- NOOP facts shown dimmed at 50% opacity
- "Approve All" / "Reject All" shortcuts
- Summary bar: "3 approved · 1 rejected · 1 skipped"
- "Commit N Memories" submits approved facts to `POST /memory/extract/commit` (not a re-run of extract — avoids re-running LLM which could produce different results)

### Item 14: Lifecycle Panel

**Location:** Detail panel — new tabbed layout: Overview | Lifecycle | Links

**Lifecycle tab shows:**

**Origin block:**
- How memory entered: extraction (with job ID link), manual add, import, merge
- Icon + method + timestamp

**Confidence section:**
- Mini sparkline showing decay curve over time
- Half-life label and current → original values
- Color transitions from green to yellow to red

**History timeline:**
- Vertical timeline with color-coded dots
- Events from audit log (`GET /audit?memory_id={id}`)
- **Backend pre-task:** Add `resource_id` filter param to `GET /audit` endpoint and `audit_log.query()`. Add SQLite index on `resource_id` column for performance.
- Event types: created (green), updated (blue), linked (blue), pinned (gold), reinforced (green), conflict detected (red)
- Each row: action description + timestamp + actor

### Item 15: Conflict Resolution

**Location:** Health page — enhanced from current one-shot actions

**Current:** Keep A / Keep B / Dismiss (inline buttons)

**New:** Clicking "Resolve" opens modal:
1. Full text side-by-side comparison (not truncated)
2. Source and confidence shown for both sides
3. Radio options:
   - **Keep A** — archives B
   - **Keep B** — archives A (recommendation shown if one has higher confidence + is newer)
   - **Merge** — opens merge modal pre-filled with both texts
   - **Defer** — adds `deferred: true` metadata, hides from conflict queue
4. All resolutions use soft archive — nothing permanently deleted. **Migration:** existing Keep A / Keep B handlers currently use `DELETE` — must be migrated to `PATCH {archived: true}`
5. Resolution logged to audit trail

---

## Visual Design Decisions

### Browse vs Search Mode

Memory cards have two distinct appearances:

**Browse mode:** Clean cards, no score indicators. Source, ID, pin badge, timestamp. Active card has subtle gold border. Archived items at 50% opacity with badge.

**Search mode:** Small 6px dot (green/yellow/red) next to source label indicates similarity. Score percentage badge (color-coded). ▲/▼ feedback buttons. Query term highlighting in gold. Score tooltip on hover (explain breakdown, admin-only).

### Modals

All modals use existing `showModal()`/`hideModal()` infrastructure. Dark elevated background (`#111111`), gold border accent, 12px radius. Primary action in gold, cancel in muted.

### Color Thresholds (consistent with existing)

- Green (`#16A34A`): confidence/similarity > 0.7
- Yellow (`#CA8A04`): 0.4–0.7
- Red (`#DC2626`): < 0.4

---

## Reusable UI Components

All B3 UI work builds on existing utilities (`h()`, `api()`, `showModal()`, `showToast()`, `confidenceBar()`, `linkTypeColor()`) and adds new reusable components — not per-feature one-offs.

### New Reusable Functions (add to `app.js` utility section)

```javascript
// Click-to-edit field — used by Item 7 (inline edit), Item 9 (merge source picker)
editableField(container, {value, type, onSave, onCancel})
// type: "text" → textarea, "input" → input, "select" → dropdown with options
// Renders display mode with dashed underline; click transforms to edit mode

// Action badge — used by Item 11 (extract results), Item 14 (lifecycle timeline)
actionBadge(action)
// action: "ADD"|"UPDATE"|"DELETE"|"NOOP"|"CONFLICT"
// Returns styled span with semantic background color per action type

// Approval toggle — used by Item 11 (extract per-fact), Item 15 (conflict resolution)
approvalToggle({state, onChange})
// state: "approved"|"rejected"|"skipped"
// Returns ✓/✕/— button with colored background, clickable to cycle

// Bulk select manager — used by Item 10 (bulk actions), Item 9 (merge trigger)
bulkSelectMode({onSelect, onDeselect, onSelectAll, actions})
// Manages checkbox state, selection count, action bar rendering
// actions: [{label, style, onClick}] — configurable per context

// Memory card — used everywhere (browse, search, bulk select modes)
memoryCard(mem, {mode, isSelected, onSelect, onActivate})
// mode: "browse"|"search"|"select" — controls which extras render
// Renders source, text, ID, pin badge, confidence; conditionally adds
// checkboxes (select), dots+score+feedback (search)

// Timeline event — used by Item 14 (lifecycle history)
timelineEvent({color, title, detail, timestamp})
// Renders a dot + text row for vertical timeline

// Side-by-side comparison — used by Item 9 (merge), Item 15 (conflict resolution)
comparisonPanel({memA, memB, labelA, labelB, colorA, colorB})
// Renders two memories side-by-side with colored left borders and metadata
```

### Existing Components Reused (no changes needed)

| Component | Used By |
|-----------|---------|
| `showModal()` / `hideModal()` | All modals (create, merge, extract, conflict, retag, re-source, link add) |
| `showToast()` | All write operations (success/error feedback) |
| `confidenceBar(value, size)` | Detail panel, search results, conflict comparison |
| `linkTypeColor(type)` | Linked memories section, lifecycle events |
| `api()` with cache invalidation | All POST/PATCH/DELETE operations |

---

## Test Strategy

**Convention:** TDD — write failing tests before implementation. One test file per batch feature group.

### B1 Tests

| File | Key Scenarios |
|------|--------------|
| `tests/test_snapshots.py` | Snapshot created before delete_by_source; snapshot list/restore; local mode fallback; skip_snapshot param |
| `tests/test_pin_archive.py` | Pinned memory excluded from bulk delete; pinned memory excluded from AUDN DELETE/UPDATE; archive excludes from search; archive-batch; unarchive restores searchability; missing field backward compat |
| `tests/test_merge_api.py` | Merge creates new memory; originals archived; supersedes links created; merge with pinned memory |
| `tests/test_cli_links.py` | links list/add/remove; invalid memory ID; duplicate link |

### B2 Tests

| File | Key Scenarios |
|------|--------------|
| `tests/test_extraction_profiles.py` | Profile CRUD; cascade resolution; mode affects categories; rules injection in AUDN prompt |
| `tests/test_single_call_extraction.py` | Single-call returns same shape as two-call; profile opt-in |
| `tests/test_extraction_dry_run.py` | Dry-run returns actions without side effects; commit endpoint executes approved subset only |
| `tests/test_missed_memory.py` | Memory created with origin metadata; missed_count incremented |

### B3 Tests

| File | Key Scenarios |
|------|--------------|
| `tests/test_web_ui_write.py` | app.js contains editableField, actionBadge, bulkSelectMode, memoryCard functions; styles.css has new classes; audit events emitted for pin/archive/merge |

---

## Audit Event Coverage

R1 adds these audit event types (emit via existing `_audit()` calls):

| Event | Trigger | Batch |
|-------|---------|-------|
| `memory.pinned` | Pin toggled on | B1 |
| `memory.unpinned` | Pin toggled off | B1 |
| `memory.archived` | Memory archived | B1 |
| `memory.unarchived` | Memory unarchived | B1 |
| `memory.merged` | Merge operation | B1 |
| `snapshot.created` | Pre-delete snapshot | B1 |
| `extraction.profile_updated` | Profile created/modified | B2 |
| `memory.missed` | Missed memory flagged | B2 |
| `conflict.resolved` | Conflict resolution (keep/merge/defer) | B3 |

---

## Files Modified

| File | Batch | Changes |
|------|-------|---------|
| `memory_engine.py` | B1, B2 | Snapshot wrapper, pin/archive fields + filters, merge method, update_memory kwargs |
| `qdrant_store.py` | B1 | Snapshot create/list/restore (remote + local fallback), archived payload index |
| `app.py` | B1, B2 | New endpoints: snapshots, archive-batch, merge, extract/commit, profiles, missed |
| `llm_extract.py` | B2 | Profile-aware extraction, single-call mode, rules injection, pin exclusion |
| `extraction_profiles.py` | B2 | New file: profile CRUD, cascade resolution, defaults |
| `cli/commands/links.py` | B1 | New file: links list/add/remove commands |
| `cli/__init__.py` | B1 | Register links command group |
| `mcp-server/index.js` | B1, B2 | Auto-snapshot on deletes, memory_missed tool, version bump to 3.0.0 |
| `webui/app.js` | B3 | Reusable components (editableField, actionBadge, approvalToggle, bulkSelectMode, memoryCard, timelineEvent, comparisonPanel), modals, lifecycle tab |
| `webui/styles.css` | B3 | Inline edit, bulk select, timeline, extract results, comparison styles |
| `tests/test_snapshots.py` | B1 | Snapshot lifecycle tests |
| `tests/test_pin_archive.py` | B1 | Pin/archive field behavior and filter tests |
| `tests/test_merge_api.py` | B1 | Merge endpoint + engine tests |
| `tests/test_cli_links.py` | B1 | CLI links command tests |
| `tests/test_extraction_profiles.py` | B2 | Profile CRUD + cascade + rules injection |
| `tests/test_single_call_extraction.py` | B2 | Single-call mode tests |
| `tests/test_extraction_dry_run.py` | B2 | Dry-run + commit flow tests |
| `tests/test_missed_memory.py` | B2 | Missed memory endpoint tests |
| `tests/test_web_ui_write.py` | B3 | UI function existence + behavioral tests |

## B3 Architectural Decision: Component Extraction

**Decision (2026-03-22):** Extract the 7 new reusable components into `webui/components.js` rather than adding them to `app.js`. ES modules already supported (`index.html` loads with `type="module"`, `app.js` uses `export`).

- `components.js` exports: `editableField`, `actionBadge`, `approvalToggle`, `bulkSelectMode`, `memoryCard`, `timelineEvent`, `comparisonPanel`
- `app.js` imports from `./components.js` and uses them in page render functions
- Existing page code in `app.js` stays untouched
- `components.js` imports shared utilities from `app.js` (`h`, `confidenceColor`, `confidenceBar`, `linkTypeColor`, `escHtml`, `timeAgo`). Note: this creates a circular import — safe because all shared utilities are `export function` declarations (hoisted). Do NOT import non-function values (state, caches) into `components.js`.
- `memoryCard` replaces the list view card rendering only; grid view keeps its existing separate layout

**Build order:** Components → #6 Create → #7 Inline Edit → #8 Links → #10 Bulk → #9 Merge → #11 Extract → #14 Lifecycle → #15 Conflicts

---

## What Is Explicitly Out of Scope

- Auto-archive based on confidence thresholds (R3 item 25)
- Feedback-weighted retrieval (R2 item 19)
- Lifecycle policies and retention rules (R3)
- Project isolation / project entity (R3)
- CLI commands for search-explain, quality-metrics, audit-log (post-R3)
- New UI pages — all work is on existing pages
