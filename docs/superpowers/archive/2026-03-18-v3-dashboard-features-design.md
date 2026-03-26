# v3.0.0 Dashboard Feature Surfacing — Design Spec

## Context

v3.0.0 added 14 new API surfaces with zero dashboard coverage. This spec defines which features to surface in the web UI (`webui/`), how they integrate into the existing 5-page SPA, and what to defer.

**Existing pages:** Dashboard, Memories, Extractions, API Keys, Settings

**Design tokens:** Kalos dark brand — primary `#d4af37`, background `#0a0a0a`, elevated `#111111`, text `#e5e5e5`, muted `#a3a3a3`, faint `#666666`, border `rgba(212,175,55,0.15)`, success `#16A34A`, warning `#CA8A04`, error `#DC2626`, info `#2563EB`. Font: Inter. Spacing: 4px base. Radii: 4/8/12px.

**Color thresholds (all use decimals matching API response shape, displayed as percentages in UI):**
- Green (`#16A34A`): confidence/similarity > 0.7
- Yellow (`#CA8A04`): 0.4–0.7
- Red (`#DC2626`): < 0.4

## Tiering

| Tier | Features | Rationale |
|------|----------|-----------|
| **1 — Important** | Confidence scores, memory links, search feedback, conflict detection | Daily user value |
| **2 — Operational** | Search explainability, extraction quality, quality summary, failures | System health monitoring |
| **3 — Deferred** | Webhooks UI, SSE feed, compaction UI, reembed UI, audit log | Admin/power-user, API-only is sufficient |

**Scope:** Build Tier 1 fully + Tier 2 grouped into a new Health page. Defer Tier 3.

## Section 1: Memory Detail Panel Enhancements

**Page:** Memories (`#/memories`) — existing detail panel

### Confidence Bar
- Horizontal bar (24px wide in list, 48px in detail) with percentage label
- Color-coded per thresholds above
- Position: top-right of detail panel, next to source badge
- Also appears as mini-bar (24px × 3px) on memory list cards

### Decay Status
- Subtle text below metadata row: e.g., "Decaying · 45d half-life"
- Color: warning (`#CA8A04`)
- Only shown when decay is active (confidence < 1.0)

### Linked Memories Section
- Collapsible section below memory text, above action buttons
- Header: "Linked Memories" with "+ Add" action in primary gold
- Each link shows:
  - Relationship type label with colors:
    - `reinforces` — gold (`#d4af37`)
    - `related_to` — info (`#2563EB`)
    - `supersedes` — warning (`#CA8A04`)
    - `blocked_by` — error (`#DC2626`)
    - `caused_by` — muted (`#a3a3a3`)
  - Truncated memory text (single line)
  - Remove button (× icon, faint color)
- "+ Add" opens a modal with:
  - Search input to find target memory
  - Relationship type picker: `reinforces`, `related_to`, `supersedes`, `blocked_by`, `caused_by`
  - Confirm button

### Conflict Indicator
- Red warning badge shown if memory has unresolved conflicts
- Clicking navigates to Health page filtered to that conflict
- Badge text: "Conflict" in error red with ⚠ icon

### Empty States
- No links: show "No linked memories" with "+ Add" action
- No conflicts: conflict indicator not rendered (hidden, not empty state)

### APIs
- Confidence: existing field on `GET /memory/{id}` response
- Links: `GET /memory/{id}/links`, `POST /memory/{id}/link`, `DELETE /memory/{id}/link/{target_id}?type={link_type}`
- Conflicts: derived from `GET /memory/conflicts` (check if memory ID appears)

## Section 2: Search Results Enhancements

**Page:** Memories (`#/memories`) — search result cards

### Feedback Buttons
- `▲` / `▼` arrow buttons on the right side of each result card
- Clicking sends `POST /search/feedback` with:
  - `query`: the search query
  - `memory_id`: result memory ID
  - `signal`: `"useful"` or `"not_useful"`
- Active state: ▲ gets green background (`rgba(22,163,74,0.1)`) with green border when selected
- Buttons are 28×28px, border-radius 4px

### Score Tooltip (Explainability) — Admin Only
- Hovering over the similarity % badge shows a popover
- **Admin-only:** if caller is not admin, tooltip shows "Score details require admin access" instead
- Data fetched lazily via `POST /search/explain` on first hover, then cached per result
- Popover styled: elevated background (`#111111`), gold border (`rgba(212,175,55,0.25)`), 8px radius
- **Response mapping** (the explain response is not flat per-result):
  - Vector score: look up result ID in `explain.vector_candidates[]` by matching `id`
  - BM25 score: look up result ID in `explain.bm25_candidates[]` by matching `id`
  - Confidence: `result.confidence` (already on the result object)
  - Final: `result.rrf_score`
  - Recency: not available per-result — omit from tooltip
- Score values in gold (`#d4af37`), Final score in green if > 0.7

### Confidence Mini-Bar
- 24px × 3px horizontal bar next to the score badge
- Same color coding as detail panel (green/yellow/red)
- Small number label (9px, faint color) showing confidence value

### Color-Coded Left Border
- 3px left border on result cards
- Color per similarity thresholds (green/yellow/red)

### APIs
- `POST /search/feedback` — relevance signals (`signal`: `"useful"` | `"not_useful"`)
- `POST /search/explain` — scoring breakdown (lazy-loaded on hover, admin-only)

## Section 3: Health Page (New)

**Page:** Health (`#/health`) — new 6th sidebar page

**Sidebar position:** After API Keys, before Settings. Icon: ♥ (heart). Gold when active.

**Auth note:** `GET /metrics/extraction-quality`, `GET /metrics/quality-summary`, and `GET /metrics/failures` are admin-only. Non-admin users see stat card placeholders with "Admin access required" message. Conflicts section still works for all users.

### Header
- Title: "Health" (20px, weight 600)
- Subtitle: "System quality and conflict monitoring" (12px, faint)
- Period selector: "Last 7 days" dropdown (today / 7d / 30d / all)

### Stat Cards Row
4 cards in a grid row, each with:
- Label (10px uppercase, faint)
- Value (24px, weight 600, semantic color)
- Subtitle (10px, muted)

| Card | Value Source | Formula | Color |
|------|------------|---------|-------|
| Conflicts | `GET /memory/conflicts` | `response.length` (count of unresolved) | Error red |
| Extract Quality | `GET /metrics/extraction-quality` | `(totals.stored + totals.updated) / totals.extracted` as % | Success green |
| Search Quality | `GET /metrics/search-quality` | `feedback.useful_ratio` as decimal | Primary gold |
| Failures | `GET /metrics/failures` | `response.failures.length` | Warning yellow |

### Conflicts Section
- Header: "Conflicts" with "View all →" link if >3
- Each conflict card shows:
  - `CONTRADICTS` badge (red background, 10px uppercase)
  - Source prefix label (faint)
  - Side-by-side comparison:
    - Memory A: red left border (`#DC2626`), subtle red background
    - Memory B: green left border (`#16A34A`), subtle green background
    - Each shows truncated text with source label
  - Resolution actions:
    - "Keep A" = delete Memory B via `DELETE /memory/{id}`, then refresh conflicts
    - "Keep B" = delete Memory A via `DELETE /memory/{id}`, then refresh conflicts
    - "Dismiss" = patch memory to remove `conflicts_with` metadata via `PATCH /memory/{id}`
  - Confirmation modal before delete actions
  - Card border: subtle red (`rgba(220,38,38,0.2)`)

### Quality Metrics Grid
2-column grid below conflicts:

**Left — Extraction Quality:**
- Per-source success rate table
- Source name (muted) + percentage (color-coded)
- Data from `GET /metrics/extraction-quality`

**Right — Search Feedback:**
- Useful signals count (green)
- Not useful signals count (red)
- Top-3 rank ratio (gold) — from `rank_distribution.top_3`
- Data from `GET /metrics/search-quality`

### Empty States
- 0 conflicts: empty-state card with "No conflicts detected" + checkmark icon
- 0 extractions: "No extraction data yet"
- 0 failures: stat card shows "0" in success green (good state)
- API failures (403/network): standard error empty-state with "Admin access required" or "Failed to load" + retry button

### APIs
- `GET /memory/conflicts` — conflict list
- `GET /metrics/extraction-quality` — per-source extraction metrics (admin-only)
- `GET /metrics/search-quality` — search feedback aggregates (admin-only)
- `GET /metrics/failures` — failure list with details (admin-only)
- `DELETE /memory/{id}` — for conflict resolution (Keep A/B)
- `PATCH /memory/{id}` — for conflict dismissal

## Section 4: Lightweight Indicators on Existing Pages

### Extractions Page
- Small quality badge next to existing "Success Rate" stat card
- Links to Health page on click

### Memory Detail Panel
- Red conflict badge (described in Section 1)
- Links to Health page filtered to that memory's conflicts

### Dashboard Page
- No changes — keeps existing overview stats

## Section 5: Deferred (Tier 3)

Explicitly out of scope:

| Feature | Reason |
|---------|--------|
| Webhooks management UI | API-only sufficient for power users |
| SSE live event feed | No daily user value |
| Compaction/consolidate UI | CLI/API-only |
| Reembed migration UI | Rare operation, API-only |
| Audit log viewer | Not enough daily value yet |
| Usage/analytics tab restoration | Separate effort |

The design does not block adding any of these later.

## Implementation Notes

- All new UI is vanilla JS (no frameworks) matching existing `webui/app.js` patterns
- Use existing `h()`, `api()`, `showModal()`, `showToast()` utilities
- Lazy-load explain data on hover (don't fetch for all results upfront)
- Cache API responses using existing 30-second GET cache in `api()`
- New Health page follows same hash-routing pattern (`#/health`)
- All colors must use Kalos design tokens — no hardcoded values outside the token set
- `navigate()` function's `titles` map and hash router need updating for the new Health page

## Files Modified

| File | Changes |
|------|---------|
| `webui/app.js` | Add Health page renderer, enhance memory detail panel, enhance search results, update `navigate()` titles map and router |
| `webui/styles.css` | Add Health page styles, confidence bar, feedback buttons, tooltip, conflict cards, empty states |
| `webui/index.html` | Add Health nav item to sidebar (`data-page="health"`) |
