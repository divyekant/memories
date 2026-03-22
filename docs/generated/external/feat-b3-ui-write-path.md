---
id: feat-012
type: feature-doc
title: UI Write-Path (B3)
audience: external
generated: 2026-03-21
---

# UI Write-Path

The Memories workbench at `/ui` is no longer read-only. You can now create, edit, merge, link, and archive memories directly in the browser. Every write action works through the same API that the CLI and MCP tools use, so your changes are immediately visible everywhere.

## What's New

- **Create memories** from the toolbar without leaving the page.
- **Edit text, source, and category inline** by clicking on them in the detail panel.
- **Pin memories** to protect them from bulk operations and decay.
- **Archive and restore** with one click and a quick undo window.
- **Link memories** with a richer search modal that shows source and confidence.
- **Merge two or more memories** into one, with a side-by-side preview.
- **Bulk actions** on selected memories: archive, delete, retag, re-source, merge.
- **Extract from pasted text** with a dry-run preview and per-fact approval.
- **Lifecycle panel** showing origin, confidence history, and audit timeline.
- **Conflict resolution** directly on the Health page.

---

## Feature Guide

### Create a Memory

1. Open the Memories page and click **+ Create** in the toolbar.
2. Enter the memory text, a source identifier, and a category.
3. Click **Save**. The new memory appears in your list immediately.

Your last-used source is remembered between sessions, so if you are adding several memories for the same project you only need to type the source once.

### Inline Editing

Click any memory in the list to open its detail panel. From there:

- **Text** -- Click the memory text to edit it in place. Press Enter or click away to save.
- **Source** -- Click the source label to change it. This updates the memory's scope and auth visibility.
- **Category** -- Click the category badge to pick a new one from the dropdown.
- **Pin** -- Click the pin button to protect a memory from bulk operations, compaction, and confidence decay. Pinned memories show a pin indicator in the list view.
- **Archive** -- Click **Archive** to soft-delete the memory. An undo toast appears for a few seconds so you can reverse the action if it was accidental.

### Link Memories

1. Open a memory's detail panel and switch to the **Links** tab.
2. Click **Add Link**. The link modal opens with a search bar.
3. Search for the target memory. Results show the memory text, source, and confidence score to help you pick the right one.
4. Choose a link type (`supersedes`, `related_to`, `blocked_by`, `caused_by`, or `reinforces`) and confirm.

The Links tab displays both outgoing and incoming links. Each link shows a direction arrow so you can tell at a glance whether this memory points to the target or the target points here.

### Merge Memories

Use merge when you have two or more memories that say the same thing in different words.

1. Enter **Select** mode by clicking **Select** in the toolbar.
2. Check the memories you want to merge (minimum two).
3. Click **Merge** in the bulk-action toolbar.
4. A side-by-side comparison panel opens showing all selected memories.
5. Review and edit the proposed merged text.
6. Click **Confirm Merge**. The merged memory is created and the originals are archived (not deleted), so you can recover them if needed.

### Bulk Actions

1. Click **Select** in the toolbar to enter bulk-selection mode. Checkboxes appear next to each memory.
2. Check the memories you want to act on.
3. The toolbar updates to show available actions:

| Action | What it does |
|--------|-------------|
| **Archive** | Soft-deletes all selected memories. They can be restored later. |
| **Delete** | Permanently removes selected memories. A confirmation dialog asks you to confirm before proceeding. |
| **Retag** | Changes the category on all selected memories at once. |
| **Re-source** | Changes the source prefix on all selected memories. Useful when reorganizing projects. |
| **Merge** | Opens the merge workflow described above. |

Pinned memories are excluded from bulk Archive and Delete to prevent accidental loss of protected data. Unpin them first if you really want to include them.

### Extract from Text

1. Click **Extract** in the toolbar.
2. Paste a conversation or block of text into the input area.
3. Click **Preview**. The system runs extraction in dry-run mode and shows you a list of candidate facts.
4. Review each fact. You can approve or reject individual items before committing.
5. Click **Commit** to save the approved facts as new memories.

This is the same extraction pipeline used by the API and hooks, but with a manual approval step so you stay in control of what gets stored.

### Lifecycle Panel

The memory detail panel now has three tabs:

- **Overview** -- Edit fields, pin, and archive (the default view described above).
- **Lifecycle** -- Shows where the memory came from (origin source, creation date), its current confidence score, and a timeline of every change: creation, edits, confidence adjustments, link additions, and merge events.
- **Links** -- Outgoing and incoming links with type labels and direction arrows.

The Lifecycle tab is read-only. Use it to understand how a memory evolved over time and to diagnose unexpected confidence changes.

### Conflict Resolution

1. Open the **Health** page from the sidebar.
2. Conflicts appear as cards showing two memories that contradict each other.
3. Click **Resolve** on any conflict card.
4. Choose a resolution strategy:

| Strategy | What happens |
|----------|-------------|
| **Keep A** | Archives memory B. Memory A remains active. |
| **Keep B** | Archives memory A. Memory B remains active. |
| **Merge both** | Opens the merge editor with both memories pre-loaded. The merged result replaces both. |
| **Defer** | Dismisses the conflict for now. It will reappear on your next Health page visit. |

All resolutions use soft archive, so nothing is permanently deleted. You can find archived memories in the Archive view and restore them if a resolution turns out to be wrong.

---

## Tips and Best Practices

- **Pin before you bulk-operate.** If there are memories you never want accidentally archived or deleted, pin them first. Bulk actions skip pinned memories automatically.
- **Use dry-run extraction.** The preview step in Extract lets you catch low-quality or duplicate facts before they enter your store. It is worth the extra click.
- **Merge instead of delete.** When two memories overlap, merging preserves the information from both and archives the originals. Deleting one risks losing context.
- **Check the Lifecycle tab when debugging.** If a memory's confidence seems wrong or its text changed unexpectedly, the audit timeline shows every event that touched it.
- **Resolve conflicts early.** Unresolved conflicts can cause inconsistent retrieval results. The Health page surfaces them so you can address them before they affect downstream agents.
- **Re-source in bulk after project renames.** If you rename a project or reorganize your source prefixes, use the bulk Re-source action instead of editing memories one at a time.

---

## Keyboard and UI Shortcuts

| Shortcut | Action |
|----------|--------|
| Click memory text | Edit inline |
| Click source label | Edit source |
| Click category badge | Change category |
| Escape | Cancel inline edit without saving |
| Enter | Save inline edit |
| Pin button | Toggle pin/unpin |
| Select mode checkbox | Toggle memory selection for bulk actions |
