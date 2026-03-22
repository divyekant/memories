# Feature Handoff: R1 B3 -- UI Write-Path

**Release:** R1 Batch 3
**Scope:** 8 write-path features added to the operator workbench at `/ui`
**Audience:** Operators, CS teams
**Date:** 2026-03-21

---

## Feature Overview

B3 adds write capabilities to the Memories operator workbench. Before B3, the UI was read-only -- operators could browse, search, and view memories but had to use the API or CLI to make changes. After B3, every common write operation (create, edit, merge, archive, delete, extract, link, resolve conflicts) is available directly in the UI.

All 8 features live in two pages:

| Page | Features |
|------|----------|
| **Memories** (`#/memories`) | Create Memory, Inline Edit, Enhanced Link Modal, Merge, Bulk Actions, Extraction Trigger |
| **Health** (`#/health`) | Conflict Resolution |
| **Detail Panel** (right side of Memories page) | Lifecycle Panel (new tabs: Overview, Lifecycle, Links) |

Architecture: the frontend is vanilla JS with no framework. New shared utilities live in `webui/utils.js` (DOM factory `h`, `escHtml`, `timeAgo`, `confidenceBar`, etc.). Seven reusable components live in `webui/components.js` (`editableField`, `actionBadge`, `approvalToggle`, `bulkSelectMode`, `memoryCard`, `timelineEvent`, `comparisonPanel`). All page render functions are in `webui/app.js`.

---

## How Each Feature Works

### 1. Create Memory

**Where:** Memories page toolbar, rightmost button labeled **"+ Create"**.

**Steps:**
1. Click **+ Create** in the toolbar. A modal opens.
2. Enter the memory text in the textarea ("What do you want to remember?").
3. The **Source** field is pre-filled with the last source you used (stored in `localStorage` under `memories-last-source`). Change it if needed.
4. Pick a **Category** from the dropdown: `detail` (default), `decision`, or `learning`.
5. Click **Create**. The button changes to "Creating..." while the request is in flight.
6. On success: modal closes, toast reads "Memory created", the list reloads at page 1.
7. On failure: the button re-enables, an error toast appears with the API error message.

**Empty state:** When a store has zero memories, the list shows a "No memories found" empty state with a **"Create your first memory"** CTA button that opens the same modal.

**API call:** `POST /memory/add` with body `{ text, source, metadata: { category } }`.

---

### 2. Inline Edit

**Where:** Detail panel on the Memories page (right side of the split layout). Appears when you click any memory in the list.

**Editable fields:**
- **Text** -- click the memory text to switch to a textarea. The textarea auto-sizes to fit content.
- **Source** -- click the source value under the meta row to switch to a text input.
- **Category** -- click the category value to get a dropdown with `decision`, `learning`, `detail`.

**Steps:**
1. Click a memory in the list to open the detail panel.
2. Hover over any editable field -- the cursor changes to a text cursor.
3. Click the field. It transforms into an input/textarea/select with **Save** and **Cancel** buttons.
4. Edit the value, then click **Save**. The field calls `PATCH /memory/{id}` with the changed field.
5. On success: the detail panel refreshes, a "Memory updated" toast appears.
6. On failure: an error toast appears. The edit state remains so you can retry.

**Pin toggle:** A pin button appears in the detail panel action row. Shows as "Pin" (unpinned) or "Pinned" with a filled style (pinned). Clicking it calls `PATCH /memory/{id}` with `{ pinned: true/false }`.

**Archive button:** The detail panel has an **Archive** / **Unarchive** button (yellow). After archiving, a toast appears with an **Undo** button. The undo window is 8 seconds (the toast auto-dismisses after 8s when an action button is present, vs. 4s for standard toasts). Clicking Undo immediately reverses the archive via another PATCH call.

**API calls:**
- Text edit: `PATCH /memory/{id}` with `{ text: "new value" }`
- Source edit: `PATCH /memory/{id}` with `{ source: "new value" }`
- Category edit: `PATCH /memory/{id}` with `{ metadata_patch: { category: "decision" } }`
- Pin: `PATCH /memory/{id}` with `{ pinned: true }`
- Archive: `PATCH /memory/{id}` with `{ archived: true }`

---

### 3. Enhanced Link Modal

**Where:** Detail panel > **Links** tab > **"+ Add"** button.

**Steps:**
1. Select a memory, then click the **Links** tab in the detail panel.
2. Click **+ Add** to open the link modal.
3. Type a search query in the search box and press Enter. The UI calls `POST /search` with hybrid search enabled (k=10).
4. Results show:
   - **Source label** (small, gold text)
   - **Text preview** (first 100 characters)
   - **Confidence bar** (if available) -- a colored fill bar showing the memory's confidence score
5. Click a result to select it (outlined with the primary gold border).
6. Choose a **link type** from the dropdown: `related_to` (default), `reinforces`, `supersedes`, `blocked_by`, `caused_by`.
7. Click **Add Link**. Calls `POST /memory/{id}/link` with `{ to_id, type }`.

**Bidirectional display:** The Links tab groups linked memories into two sections:
- **Outgoing** (arrow: `->`) -- links this memory created to other memories.
- **Incoming** (arrow: `<-`) -- links other memories created pointing to this memory.

Each link row shows the link type (color-coded by type) and the target memory's text (first 80 characters). A remove button (`x`) on each row calls `DELETE /memory/{id}/link/{target_id}?type={type}`.

**API calls:**
- Search: `POST /search` with `{ query, k: 10, hybrid: true }`
- Add link: `POST /memory/{id}/link` with `{ to_id, type }`
- Load links: `GET /memory/{id}/links?include_incoming=true`
- Remove link: `DELETE /memory/{id}/link/{target_id}?type={type}`

---

### 4. Merge Memories

**Where:** Memories page, available when 2+ memories are selected in bulk mode. Also available from the Conflict Resolution modal on the Health page.

**Steps:**
1. Enter bulk mode (click **Select** in the toolbar).
2. Select 2 or more memories using checkboxes.
3. Click **Merge** in the action bar. The UI fetches full memory data via `POST /memory/get-batch`.
4. The merge modal opens showing:
   - **Side-by-side comparison** (red-bordered panels for each source memory, labeled "#ID (will be archived)")
   - For 3+ memories, pairs are stacked vertically. An odd memory out gets a single-sided panel.
5. **Merged Text** textarea is pre-filled with all source texts joined by double newlines. Edit as needed.
6. **Source** input is pre-filled with the first memory's source.
7. Click **"Merge & Archive Originals"**. Calls `POST /memory/merge` with `{ ids, merged_text, source }`.
8. On success: modal closes, toast reads "Merged into #NEW_ID, archived N originals", list reloads.

**Validation:** The merge button is disabled when fewer than 2 memories are selected. A warning toast ("Select at least 2 memories to merge") appears if you try.

**API calls:**
- Load memories: `POST /memory/get-batch` with `{ ids }`
- Merge: `POST /memory/merge` with `{ ids, merged_text, source }`

---

### 5. Bulk Actions

**Where:** Memories page toolbar, **"Select"** toggle button.

**Steps:**
1. Click **Select** in the toolbar. The button text changes to **"Cancel"** and gets an active highlight.
2. Checkboxes appear next to every memory in the list. A **bulk action bar** appears at the top of the content area with a "Select all" checkbox and a count ("N selected").
3. Select memories by clicking checkboxes or clicking the memory rows.
4. The action bar shows 5 buttons: **Archive**, **Delete**, **Retag**, **Re-source**, **Merge**. All buttons are disabled when nothing is selected.

**Actions:**

| Action | UI | API Call | Notes |
|--------|-----|----------|-------|
| **Archive** | Single click, immediate | `POST /memory/archive-batch` with `{ ids }` | No confirmation modal |
| **Delete** | Opens confirmation modal | `POST /memory/delete-batch` with `{ ids }` | Modal warns: "This action creates an automatic snapshot first but cannot be easily undone." |
| **Retag** | Opens modal with category dropdown | `PATCH /memory/{id}` for each ID (parallel) | Options: `detail`, `decision`, `learning` |
| **Re-source** | Opens modal with source text input | `PATCH /memory/{id}` for each ID (parallel) | Requires non-empty source. Validates before sending. |
| **Merge** | Opens merge modal | See "Merge Memories" above | Requires 2+ selected |

After any bulk action completes, the UI exits bulk mode automatically and reloads the memory list.

**Note on Retag and Re-source:** These actions send individual PATCH requests in parallel for each selected memory. With a large selection (100+ memories), this can generate many concurrent API calls.

---

### 6. Extraction Trigger

**Where:** Memories page toolbar, **"Extract"** button (to the left of "+ Create").

**Steps:**

**Phase 1 -- Input:**
1. Click **Extract**. A modal opens.
2. Paste conversation text into the textarea.
3. Set the **Source** (pre-filled from last used, stored in localStorage).
4. Pick a **Mode** using the toggle buttons:
   - `conservative` -- fewer extractions, higher precision
   - `standard` (default) -- balanced
   - `aggressive` -- more extractions, may include lower-confidence facts
5. **"Preview before saving"** checkbox is checked by default (dry-run mode).
6. Click **Extract**. All form inputs are disabled while processing.

**Phase 2 -- Polling:**
- The UI calls `POST /memory/extract` with `{ messages, source, context, dry_run }`.
- The `context` parameter maps from the mode toggle: standard/conservative send `"stop"`, aggressive sends `"pre_compact"`.
- A job ID is returned. The UI polls `GET /memory/extract/{job_id}` every 1 second for up to 30 seconds.
- If the job takes longer than 30s, an "Extraction timed out after 30s" error appears.

**Phase 3a -- Non-dry-run result:**
- If dry-run was unchecked, the modal closes immediately with a summary toast: "Extracted: N stored, N updated".

**Phase 3b -- Dry-run review:**
- The modal content is replaced with the extraction results.
- **Shortcut buttons:** "Approve All" and "Reject All" at the top.
- Each extracted fact shows:
  - **AUDN badge** -- color-coded: ADD (green), UPDATE (blue), DELETE (red), NOOP (gray), CONFLICT (yellow)
  - **Fact text**
  - **Approval toggle** -- tri-state button cycling through: approved (checkmark), rejected (X), skipped (dash). NOOP facts default to "skipped"; all others default to "approved".
- A **summary bar** shows counts: "N approved, N rejected, N skipped".
- The **"Commit N Memories"** button is disabled when 0 facts are approved.
- Clicking Commit sends approved facts to `POST /memory/extract/commit` with `{ actions, source }`. Each action includes an `approved: true/false` flag.

**API calls:**
- Submit extraction: `POST /memory/extract` with `{ messages, source, context, dry_run }`
- Poll status: `GET /memory/extract/{job_id}`
- Commit results: `POST /memory/extract/commit` with `{ actions, source }`

---

### 7. Lifecycle Panel

**Where:** Detail panel on the Memories page. The old single-view detail panel is now organized into three tabs.

**Tabs:**

| Tab | Contents |
|-----|----------|
| **Overview** | Memory text (inline-editable), metadata (ID, source, category, created date), decay status, conflict badge, action buttons (Pin, Archive, Delete) |
| **Lifecycle** | Origin block, confidence section, audit history timeline |
| **Links** | Bidirectional linked memories list with add/remove (see Enhanced Link Modal) |

**Lifecycle tab details:**

**Origin block:** Shows how the memory entered the system, detected from metadata:
- Extraction icon + label if `metadata.extraction_job_id` or `metadata.extract_source` exists
- Merge icon + label if `metadata.supersedes` or `metadata.merged_from` exists
- Import icon + label if `metadata.imported` or `metadata.import_source` exists
- Manual add icon + label (default fallback)
- Below the label: relative timestamp of creation ("5d ago")

**Confidence section:** If the memory has a confidence value < 1.0, shows:
- A medium-sized confidence bar (color-coded: green > 70%, yellow 40-70%, red < 40%)
- Numeric percentage

**Audit timeline:** Fetches `GET /audit?resource_id={id}&limit=20` and displays each event as a vertical timeline with:
- Color-coded dots per action type:
  - `memory.created` = green
  - `memory.updated` = blue
  - `memory.linked` = blue
  - `memory.pinned` = primary (gold)
  - `memory.archived` = yellow
  - `conflict.detected` = red
- Event title (action name, title-cased with dots replaced by spaces)
- Detail text (source prefix or key name)
- Relative timestamp

**Fallback:** If the audit endpoint returns an error (e.g., non-admin key), the timeline shows "No history available" instead of an error.

**API calls:**
- Audit log: `GET /audit?resource_id={id}&limit=20`

---

### 8. Conflict Resolution

**Where:** Health page (`#/health`), **"Resolve"** button on each conflict card.

The Health page loads conflicts from `GET /memory/conflicts` and displays up to 3 conflict cards at a time. Each card shows:
- A "CONTRADICTS" badge
- Side-by-side display of Memory A and Memory B (first 120 characters each)
- A **"Resolve"** button

**Steps:**
1. Click **Resolve** on a conflict card. The resolution modal opens.
2. The modal shows a side-by-side comparison panel with both memories.
3. Four resolution options appear:

| Option | What it does | API calls |
|--------|-------------|-----------|
| **Keep A** | Archives Memory B, clears conflict metadata on A | `PATCH /memory/{B_id}` with `{ archived: true }`, then `PATCH /memory/{A_id}` with `{ metadata_patch: { conflicts_with: null } }` |
| **Keep B** | Archives Memory A, clears conflict metadata on B | `PATCH /memory/{A_id}` with `{ archived: true }`, then `PATCH /memory/{B_id}` with `{ metadata_patch: { conflicts_with: null } }` |
| **Merge** | Closes the conflict modal, opens the merge modal with both memories pre-loaded | Uses `POST /memory/get-batch` then `POST /memory/merge` (same flow as Merge Memories) |
| **Defer** | Sets `metadata.deferred: true` on both memories, hiding them from the conflict queue | `PATCH /memory/{A_id}` with `{ metadata_patch: { deferred: true } }`, then same for B |

4. A **Cancel** button at the bottom closes the modal without changes.

**All resolutions use soft archive.** No memory is permanently deleted during conflict resolution. Archived memories remain in the store and can be unarchived later.

---

## Common Questions

**Q1: What happens if I close the browser mid-extraction?**
The extraction job continues server-side. If you were in dry-run mode, the job results are stored for a period but you will need to re-open the extraction modal and re-submit to get a new review session. The original job cannot be resumed from the UI. If you were not in dry-run mode, the extraction commits automatically upon completion.

**Q2: Can I undo a bulk delete?**
Not directly from the UI. However, the delete-batch endpoint creates an automatic snapshot before deleting. You can restore from that snapshot using the Settings page or the `/snapshots/{name}/restore` API endpoint. Bulk archive is reversible without snapshots since archived memories remain in the store.

**Q3: Does inline edit trigger re-embedding?**
Yes. When you change the memory text via inline edit, the `PATCH /memory/{id}` endpoint calls `update_memory`, which regenerates the embedding vector for the updated text. Editing source or category only updates metadata and does not trigger re-embedding.

**Q4: Why does the source field pre-fill in the Create and Extract modals?**
The UI stores the last-used source in `localStorage` under the key `memories-last-source`. This persists across browser sessions. Each time you successfully create a memory or submit an extraction, the source value is saved. This reduces repetitive typing for operators working within a single project context.

**Q5: How does the "Defer" option in conflict resolution work?**
Defer sets `metadata.deferred: true` on both conflicting memories. The conflict detection endpoint (`GET /memory/conflicts`) continues to return these memories in its response, but the Health page UI is expected to filter deferred conflicts out of the display queue in a future update. Currently, deferred conflicts may still appear after a page reload. The deferred flag can be cleared by editing the memory's metadata via PATCH.

**Q6: Why do Retag and Re-source send individual PATCH requests instead of a batch endpoint?**
There is no batch-patch endpoint in the API. The UI sends parallel PATCH requests for each selected memory. For selections under 50 items, this completes quickly. For very large selections, the requests are still parallel but may take longer and could hit rate limits if configured.

**Q7: What does the approval toggle tri-state mean in extraction review?**
- **Approved** (checkmark): the fact will be committed to memory when you click Commit.
- **Rejected** (X): the fact will be excluded from the commit.
- **Skipped** (dash): same as rejected for commit purposes. NOOP facts (no change needed) default to skipped. The distinction lets operators signal "I saw this but it doesn't need action" vs. "I actively rejected this."

**Q8: Can I merge more than 2 memories at once?**
Yes. Select 3 or more in bulk mode and click Merge. The comparison panel stacks pairs vertically. The merged text textarea is pre-filled with all source texts concatenated with double newlines. All source memories are archived when the merge completes.

---

## Troubleshooting

### "Extraction timed out after 30s"

The UI polls the extraction job for a maximum of 30 seconds at 1-second intervals. If the LLM provider is slow or the input text is very long, the job may still be processing.

**Fix:** The extraction continues server-side. Wait a minute, then check the Extractions page for recent job status. You can also call `GET /memory/extract/{job_id}` directly via the API to check completion.

### Archive toast Undo button does not appear

The Undo button is attached to the toast notification, which auto-dismisses after 8 seconds. If you scroll away or focus another tab, you may miss the toast.

**Workaround:** Use the detail panel to unarchive manually. Select the archived memory (it will show an "archived" badge), then click the **Unarchive** button in the Overview tab.

### Inline edit Save button does nothing

If clicking Save produces no visible change and no toast, the API call may be failing silently.

**Check:**
1. Open browser DevTools (Network tab) and look for a failed `PATCH` request.
2. Common cause: the API key does not have write permission for the memory's source prefix.
3. Another cause: the memory was deleted between loading the detail panel and clicking Save. The PATCH will return 404.

### Bulk actions buttons stay disabled

The action bar buttons are disabled when `selected.size === 0`. If you see checkboxes but buttons stay disabled:

**Check:** Confirm you are clicking the checkboxes or the memory rows in the list. Clicking the detail panel (right side) does not toggle selection. The bulk action bar updates its count display (`"N selected"`) -- verify this number changes.

### Merge modal shows "Could not load selected memories"

The merge modal fetches full memory data via `POST /memory/get-batch`. This can fail if:
- One or more selected memories were deleted between selection and clicking Merge.
- The API key does not have read access to the selected memories.
- The server returns fewer than 2 memories (some may have been filtered by source-prefix auth).

**Fix:** Deselect and re-select the memories, verifying they still exist in the list.

### Conflict resolution "Resolve" button does not respond

If clicking Resolve does nothing, check the browser console for JavaScript errors. The modal requires the `#modalOverlay` and `#modalContent` DOM elements. If the page HTML was not fully loaded (e.g., cached service worker serving stale HTML), these elements may be missing.

**Fix:** Hard-refresh the page (Cmd+Shift+R / Ctrl+Shift+R).

### Lifecycle tab shows "No history available" for all memories

The audit timeline fetches `GET /audit?resource_id={id}&limit=20`. This endpoint requires admin-level API key access.

**Fix:** Set an admin API key on the API Keys page (`#/keys`). Non-admin keys will silently fail and show the "No history available" fallback.

### Extraction review shows all facts as NOOP

If the extraction engine determines that all extracted facts already exist in memory, every fact will be tagged NOOP. This is expected behavior when re-extracting from a conversation that was already processed.

**Check:** Verify the source text is new/different from previously extracted content. The extraction engine deduplicates against existing memories for the given source.

### Link modal search returns no results

The link modal search uses `POST /search` with `{ query, k: 10, hybrid: true }`. If no results appear:
- Verify the search query is meaningful (not just punctuation or whitespace).
- Verify the memory store is not empty.
- Hybrid search requires both the vector index and BM25 index. If the index has not been built since memories were added, results may be incomplete.

---

## API Endpoints Used

Quick reference for all endpoints exercised by B3 write-path features.

| Method | Endpoint | Used By | Request Body |
|--------|----------|---------|-------------|
| `POST` | `/memory/add` | Create Memory | `{ text, source, metadata }` |
| `PATCH` | `/memory/{id}` | Inline Edit, Pin, Archive, Retag, Re-source, Conflict Resolution | `{ text?, source?, metadata_patch?, pinned?, archived? }` |
| `POST` | `/memory/archive-batch` | Bulk Archive | `{ ids: [int] }` |
| `POST` | `/memory/delete-batch` | Bulk Delete | `{ ids: [int] }` |
| `POST` | `/memory/merge` | Merge Memories, Conflict Resolution (Merge) | `{ ids: [int], merged_text, source }` |
| `POST` | `/memory/get-batch` | Merge (load full texts) | `{ ids: [int] }` |
| `POST` | `/memory/extract` | Extraction Trigger | `{ messages, source, context, dry_run }` |
| `GET` | `/memory/extract/{job_id}` | Extraction Trigger (polling) | -- |
| `POST` | `/memory/extract/commit` | Extraction Trigger (commit) | `{ actions: [{...action, approved}], source }` |
| `POST` | `/search` | Link Modal, Global Search | `{ query, k, hybrid }` |
| `POST` | `/memory/{id}/link` | Enhanced Link Modal | `{ to_id, type }` |
| `GET` | `/memory/{id}/links?include_incoming=true` | Links Tab | -- |
| `DELETE` | `/memory/{id}/link/{target_id}?type={type}` | Links Tab (remove) | -- |
| `GET` | `/audit?resource_id={id}&limit=20` | Lifecycle Panel | -- |
| `GET` | `/memory/conflicts` | Conflict Resolution, Conflict Badge | -- |
| `GET` | `/memory/{id}` | Detail Panel (refresh after edit) | -- |
