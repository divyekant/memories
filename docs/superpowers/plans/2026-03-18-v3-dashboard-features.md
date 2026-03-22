# v3.0.0 Dashboard Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface v3.0.0 API features in the memories web dashboard — confidence scores, memory links, search feedback, conflict detection, and a new Health page.

**Architecture:** Vanilla JS SPA (`webui/app.js`, `webui/styles.css`, `webui/index.html`). All rendering uses the existing `h()` DOM builder, `api()` fetch wrapper, `showModal()`/`showToast()` helpers, and `registerPage()` hash router. No frameworks. New Health page registered as a 6th route. All colors use CSS custom properties from the existing design token system.

**Tech Stack:** Vanilla JS (ES6 modules), CSS custom properties, FastAPI backend (read-only — no backend changes needed)

**Spec:** `docs/superpowers/specs/2026-03-18-v3-dashboard-features-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `webui/styles.css` | New CSS classes for confidence bars, feedback buttons, tooltips, conflict cards, Health page layout | Modify (append new sections) |
| `webui/index.html` | Add Health nav item to sidebar | Modify (add 1 `<a>` tag) |
| `webui/app.js` | Enhanced detail panel, search results, new Health page renderer, router update | Modify (enhance existing functions + add new page) |
| `tests/test_web_ui.py` | Test that new nav item, page title, and JS functions exist | Modify (add test cases) |

---

### Task 1: Add CSS classes for confidence bars and color utilities

**Files:**
- Modify: `webui/styles.css` (append after existing styles, before `@media` responsive section)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_styles_contain_confidence_and_health_classes(client):
    css_response = client.get("/ui/static/styles.css")
    assert css_response.status_code == 200
    assert ".confidence-bar" in css_response.text
    assert ".feedback-btn" in css_response.text
    assert ".score-tooltip" in css_response.text
    assert ".conflict-card" in css_response.text
    assert ".health-stat-grid" in css_response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_styles_contain_confidence_and_health_classes -v`
Expected: FAIL — classes don't exist yet

- [ ] **Step 3: Add CSS classes to styles.css**

Find the line `/* -- Responsive ---` (or end of file before `@media`) in `webui/styles.css` and insert these new sections before it:

```css
/* -- Confidence Bar -------------------------------------------------------- */

.confidence-bar {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.confidence-bar-track {
  height: 3px;
  background: var(--color-border-subtle);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.confidence-bar-track--sm { width: 24px; }
.confidence-bar-track--md { width: 48px; height: 6px; }

.confidence-bar-fill {
  height: 100%;
  border-radius: var(--radius-sm);
  transition: width var(--transition-normal);
}

.confidence-bar-label {
  font-size: 0.6875rem;
  font-weight: 600;
}

.color-success { color: var(--color-success); }
.color-warning { color: var(--color-warning); }
.color-error { color: var(--color-error); }
.bg-success { background: var(--color-success); }
.bg-warning { background: var(--color-warning); }
.bg-error { background: var(--color-error); }

.color-success-dim { background: rgba(22, 163, 74, 0.1); border-color: rgba(22, 163, 74, 0.25); }
.color-warning-dim { background: rgba(202, 138, 4, 0.1); border-color: rgba(202, 138, 4, 0.25); }
.color-error-dim { background: rgba(220, 38, 38, 0.1); border-color: rgba(220, 38, 38, 0.25); }

.border-left-success { border-left: 3px solid var(--color-success); }
.border-left-warning { border-left: 3px solid var(--color-warning); }
.border-left-error { border-left: 3px solid var(--color-error); }

/* -- Feedback Buttons ------------------------------------------------------ */

.feedback-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  cursor: pointer;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  font-size: 13px;
  color: var(--color-text-faint);
  transition: background var(--transition-fast), border-color var(--transition-fast);
}

.feedback-btn:hover {
  background: var(--color-primary-dim);
  border-color: var(--color-border);
}

.feedback-btn.active-useful {
  background: rgba(22, 163, 74, 0.1);
  border-color: rgba(22, 163, 74, 0.25);
  color: var(--color-success);
}

.feedback-btn.active-not-useful {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.25);
  color: var(--color-error);
}

/* -- Score Tooltip --------------------------------------------------------- */

.score-tooltip {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: 4px;
  background: var(--color-bg-elevated);
  border: 1px solid rgba(212, 175, 55, 0.25);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  z-index: 100;
  white-space: nowrap;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.score-tooltip-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  color: var(--color-text-faint);
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}

.score-tooltip-grid {
  display: flex;
  gap: 16px;
  font-size: 0.75rem;
}

.score-tooltip-item {
  text-align: center;
}

.score-tooltip-item-label {
  color: var(--color-text-faint);
  font-size: 0.625rem;
  margin-bottom: 2px;
}

.score-tooltip-item-value {
  color: var(--color-primary);
  font-weight: 600;
}

/* -- Linked Memories ------------------------------------------------------- */

.linked-memories-section {
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
  margin-top: 12px;
}

.linked-memories-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.linked-memories-title {
  font-size: 0.6875rem;
  text-transform: uppercase;
  color: var(--color-text-faint);
  letter-spacing: 0.5px;
}

.linked-memory-add {
  font-size: 0.6875rem;
  color: var(--color-primary);
  cursor: pointer;
  background: none;
  border: none;
  font-family: inherit;
}

.linked-memory-add:hover {
  color: var(--color-primary-hover);
}

.linked-memory-item {
  background: var(--color-bg-surface);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.linked-memory-type {
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.linked-memory-text {
  margin-top: 2px;
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

.linked-memory-remove {
  color: var(--color-text-faint);
  cursor: pointer;
  font-size: 0.625rem;
  background: none;
  border: none;
  padding: 2px 4px;
}

.linked-memory-remove:hover {
  color: var(--color-error);
}

/* -- Conflict Badge -------------------------------------------------------- */

.conflict-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.6875rem;
  color: var(--color-error);
  background: rgba(220, 38, 38, 0.1);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  text-decoration: none;
}

.conflict-badge:hover {
  background: rgba(220, 38, 38, 0.2);
}

/* -- Conflict Card (Health page) ------------------------------------------- */

.conflict-card {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-md);
  padding: 14px;
  margin-bottom: 8px;
  border: 1px solid rgba(220, 38, 38, 0.2);
}

.conflict-type-badge {
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
  background: rgba(220, 38, 38, 0.15);
  color: var(--color-error);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

.conflict-pair {
  display: flex;
  gap: 12px;
  margin-top: 8px;
  font-size: 0.75rem;
  line-height: 1.5;
}

.conflict-side {
  flex: 1;
  padding: 8px;
  border-radius: var(--radius-sm);
}

.conflict-side--a {
  background: rgba(220, 38, 38, 0.05);
  border-left: 2px solid var(--color-error);
}

.conflict-side--b {
  background: rgba(22, 163, 74, 0.05);
  border-left: 2px solid var(--color-success);
}

.conflict-side-label {
  font-size: 0.625rem;
  color: var(--color-text-muted);
  margin-bottom: 4px;
}

.conflict-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.conflict-action-btn {
  font-size: 0.6875rem;
  color: var(--color-primary);
  background: var(--color-primary-dim);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  border: 1px solid rgba(212, 175, 55, 0.2);
  font-family: inherit;
}

.conflict-action-btn:hover {
  background: rgba(212, 175, 55, 0.2);
}

.conflict-dismiss-btn {
  font-size: 0.6875rem;
  color: var(--color-text-muted);
  background: none;
  border: none;
  padding: 4px 10px;
  cursor: pointer;
  font-family: inherit;
}

.conflict-dismiss-btn:hover {
  color: var(--color-text);
}

/* -- Health Page ----------------------------------------------------------- */

.health-stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}

.health-stat-card {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-md);
  padding: 14px;
  border: 1px solid var(--color-border);
}

.health-stat-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--color-text-faint);
  margin-bottom: 4px;
}

.health-stat-value {
  font-size: 1.5rem;
  font-weight: 600;
}

.health-stat-sub {
  font-size: 0.625rem;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.quality-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.quality-panel {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-md);
  padding: 14px;
  border: 1px solid var(--color-border);
}

.quality-panel-title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 10px;
}

.quality-row {
  display: flex;
  justify-content: space-between;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  margin-bottom: 6px;
}

@media (max-width: 768px) {
  .health-stat-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .quality-grid {
    grid-template-columns: 1fr;
  }
  .conflict-pair {
    flex-direction: column;
  }
}

/* -- Decay Status ---------------------------------------------------------- */

.decay-status {
  font-size: 0.6875rem;
  color: var(--color-warning);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_styles_contain_confidence_and_health_classes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/styles.css tests/test_web_ui.py
git commit -m "feat(ui): add CSS classes for confidence bars, feedback buttons, tooltips, conflict cards, and Health page"
```

---

### Task 2: Add Health nav item to sidebar and update router

**Files:**
- Modify: `webui/index.html:51` (add nav item after API Keys, before Settings)
- Modify: `webui/app.js:275-281` (add "health" to titles map)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_health_nav_item_exists(client):
    response = client.get("/ui")
    assert response.status_code == 200
    assert 'data-page="health"' in response.text
    assert "Health" in response.text


def test_app_js_has_health_page_title(client):
    js_response = client.get("/ui/static/app.js")
    assert js_response.status_code == 200
    assert '"health"' in js_response.text or "'health'" in js_response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_health_nav_item_exists tests/test_web_ui.py::test_app_js_has_health_page_title -v`
Expected: FAIL

- [ ] **Step 3: Add Health nav item to index.html**

In `webui/index.html`, after the API Keys nav item (line 51) and before the Settings nav item, add:

```html
        <a href="#/health" class="nav-item" data-page="health">
          <span class="nav-icon">&#9829;</span>
          <span class="nav-label">Health</span>
        </a>
```

- [ ] **Step 4: Update the titles map in app.js**

In `webui/app.js`, find the `titles` object (around line 275) and add the health entry:

```javascript
  const titles = {
    dashboard: "Dashboard",
    memories: "Memories",
    extractions: "Extractions",
    keys: "API Keys",
    health: "Health",
    settings: "Settings",
  };
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_health_nav_item_exists tests/test_web_ui.py::test_app_js_has_health_page_title -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/index.html webui/app.js tests/test_web_ui.py
git commit -m "feat(ui): add Health nav item to sidebar and router titles"
```

---

### Task 3: Add confidence bar and decay status helper functions

**Files:**
- Modify: `webui/app.js` (add utility functions after the `escHtml` function, around line 252)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_app_js_has_confidence_and_link_helpers(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "confidenceColor" in text
    assert "confidenceBar" in text
    assert "linkTypeColor" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_confidence_and_link_helpers -v`
Expected: FAIL

- [ ] **Step 3: Add helper functions to app.js**

Insert after the `escHtml` function (after line 251) in `webui/app.js`:

```javascript
// -- Confidence & Link Helpers ---------------------------------------------

/**
 * Return the semantic color class suffix for a confidence/similarity value (0-1).
 * >0.7 = success (green), 0.4-0.7 = warning (yellow), <0.4 = error (red)
 */
export function confidenceColor(value) {
  if (value > 0.7) return "success";
  if (value >= 0.4) return "warning";
  return "error";
}

/**
 * Build a confidence bar DOM element.
 * @param {number} value - Confidence value 0-1
 * @param {"sm"|"md"} size - Bar size
 * @returns {HTMLElement}
 */
export function confidenceBar(value, size = "sm") {
  const color = confidenceColor(value);
  const pct = Math.round(value * 100);
  return h("span", { className: "confidence-bar" },
    h("span", { className: `confidence-bar-track confidence-bar-track--${size}` },
      h("span", { className: `confidence-bar-fill bg-${color}`, style: { width: `${pct}%` } })
    ),
    h("span", { className: `confidence-bar-label color-${color}` }, size === "sm" ? String(pct) : `${pct}%`)
  );
}

/**
 * Return the CSS color variable for a link type.
 */
export function linkTypeColor(type) {
  const map = {
    reinforces: "var(--color-primary)",
    related_to: "var(--color-info)",
    supersedes: "var(--color-warning)",
    blocked_by: "var(--color-error)",
    caused_by: "var(--color-text-muted)",
  };
  return map[type] || "var(--color-text-faint)";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_confidence_and_link_helpers -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/app.js tests/test_web_ui.py
git commit -m "feat(ui): add confidence bar, color, and link type helper functions"
```

---

### Task 4: Enhance memory detail panel with confidence, decay, links, and conflict badge

**Files:**
- Modify: `webui/app.js:728-770` (rewrite `updateDetailPanel()` function)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_app_js_has_detail_panel_enhancements(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "confidenceBar" in text
    assert "loadLinks" in text
    assert "conflict-badge" in text
    assert "/links" in text  # API calls for memory links
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_detail_panel_enhancements -v`
Expected: FAIL

- [ ] **Step 3: Rewrite updateDetailPanel() in app.js**

Replace the `updateDetailPanel()` function (lines 728-770) with the enhanced version. The new function:

1. Shows confidence bar (md size) next to source badge in the header
2. Shows decay status text below metadata if confidence < 1.0
3. Fetches links from `GET /memory/{id}/links` and renders the linked memories section
4. Checks `GET /memory/conflicts` (cached) to show a conflict badge if the memory has conflicts
5. Adds "+ Add" link action that opens a modal with search + type picker

```javascript
  // -- Update detail panel with v3 enhancements --
  async function updateDetailPanel() {
    const detailPanel = document.getElementById("memoryDetailPanel");
    if (!detailPanel) return;

    if (!memState.selected) {
      detailPanel.innerHTML = "";
      detailPanel.appendChild(
        h("div", { className: "empty-state", style: { border: "none", padding: "60px 20px" } },
          h("div", { className: "empty-state-icon" }, "\u25C6"),
          h("div", { className: "empty-state-text" }, "Select a memory to view details")
        )
      );
      return;
    }

    const mem = memState.selected;
    detailPanel.innerHTML = "";

    // Header: source + confidence bar
    const header = h("div", { className: "detail-header" });
    const headerLeft = h("div", null,
      h("span", { className: "memory-item-source", style: { fontSize: "0.85rem" } }, mem.source || "")
    );
    const headerRight = h("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
      h("span", { className: "memory-item-id", style: { fontSize: "0.78rem" } }, `#${mem.id}`)
    );
    // Add confidence bar if available
    if (mem.confidence != null) {
      headerRight.appendChild(confidenceBar(mem.confidence, "md"));
    }
    header.appendChild(headerLeft);
    header.appendChild(headerRight);
    detailPanel.appendChild(header);

    // Text
    detailPanel.appendChild(h("div", { className: "detail-text" }, mem.text || ""));

    // Meta row
    const meta = h("div", { className: "detail-meta" });
    meta.appendChild(
      h("div", { className: "meta-item" },
        h("div", { className: "meta-label" }, "ID"),
        h("div", { className: "meta-value font-mono" }, String(mem.id))
      )
    );
    meta.appendChild(
      h("div", { className: "meta-item" },
        h("div", { className: "meta-label" }, "Source"),
        h("div", { className: "meta-value" }, mem.source || "N/A")
      )
    );
    meta.appendChild(
      h("div", { className: "meta-item" },
        h("div", { className: "meta-label" }, "Created"),
        h("div", { className: "meta-value" }, mem.created_at ? timeAgo(mem.created_at) : "N/A")
      )
    );
    detailPanel.appendChild(meta);

    // Decay status
    if (mem.confidence != null && mem.confidence < 1.0) {
      detailPanel.appendChild(
        h("div", { className: "decay-status" }, `Decaying \u00B7 confidence ${Math.round(mem.confidence * 100)}%`)
      );
    }

    // Conflict badge (check asynchronously)
    const conflictArea = h("div");
    detailPanel.appendChild(conflictArea);
    loadConflictBadge(mem.id, conflictArea);

    // Linked memories section
    const linksSection = h("div", { className: "linked-memories-section" });
    detailPanel.appendChild(linksSection);
    loadLinks(mem.id, linksSection);

    // Action buttons
    const actions = h("div", { className: "detail-actions", style: { marginTop: "12px" } });
    const deleteBtn = h("button", { className: "btn btn-danger btn-sm" }, "Delete");
    deleteBtn.addEventListener("click", () => deleteMemory(mem.id));
    actions.appendChild(deleteBtn);
    detailPanel.appendChild(actions);
  }

  // -- Load linked memories for detail panel --
  async function loadLinks(memoryId, container) {
    container.innerHTML = "";
    const header = h("div", { className: "linked-memories-header" },
      h("span", { className: "linked-memories-title" }, "Linked Memories"),
      h("button", { className: "linked-memory-add", onClick: () => showAddLinkModal(memoryId) }, "+ Add")
    );
    container.appendChild(header);

    try {
      const data = await api(`/memory/${memoryId}/links`);
      const links = data.links || [];
      if (links.length === 0) {
        container.appendChild(
          h("div", { style: { fontSize: "0.75rem", color: "var(--color-text-faint)" } }, "No linked memories")
        );
        return;
      }
      links.forEach((link) => {
        const targetId = link.direction === "outgoing" ? link.to_id : link.from_id;
        const item = h("div", { className: "linked-memory-item" },
          h("div", null,
            h("span", { className: "linked-memory-type", style: { color: linkTypeColor(link.link_type) } }, link.link_type),
            h("div", { className: "linked-memory-text" }, `Memory #${targetId}`)
          ),
          h("button", {
            className: "linked-memory-remove",
            onClick: async () => {
              try {
                await api(`/memory/${memoryId}/link/${targetId}?type=${encodeURIComponent(link.link_type)}`, { method: "DELETE" });
                invalidateCache(`/memory/${memoryId}/links`);
                showToast("Link removed", "success");
                loadLinks(memoryId, container);
              } catch (err) { showToast(err.message, "error"); }
            },
          }, "\u00D7")
        );
        container.appendChild(item);
      });
    } catch (err) {
      container.appendChild(
        h("div", { style: { fontSize: "0.75rem", color: "var(--color-text-faint)" } }, "Failed to load links")
      );
    }
  }

  // -- Show add-link modal --
  function showAddLinkModal(memoryId) {
    showModal((content) => {
      content.appendChild(h("h3", { style: { marginBottom: "16px" } }, "Link Memory"));

      const searchInput = h("input", {
        type: "text",
        className: "topbar-search",
        placeholder: "Search for a memory...",
        style: { width: "100%", marginBottom: "12px" },
      });
      content.appendChild(searchInput);

      const typeSelect = h("select", { className: "source-select", style: { marginBottom: "12px", width: "100%" } });
      ["related_to", "reinforces", "supersedes", "blocked_by", "caused_by"].forEach((t) => {
        typeSelect.appendChild(h("option", { value: t }, t));
      });
      content.appendChild(typeSelect);

      const resultsDiv = h("div", { style: { maxHeight: "200px", overflowY: "auto", marginBottom: "12px" } });
      content.appendChild(resultsDiv);

      let selectedTargetId = null;

      searchInput.addEventListener("keydown", async (e) => {
        if (e.key !== "Enter") return;
        const query = searchInput.value.trim();
        if (!query) return;
        try {
          const data = await api("/search", { method: "POST", body: JSON.stringify({ query, k: 10, hybrid: true }) });
          resultsDiv.innerHTML = "";
          (data.results || []).forEach((r) => {
            const row = h("div", {
              style: { padding: "8px", cursor: "pointer", borderRadius: "4px", marginBottom: "4px", background: "var(--color-bg-surface)", fontSize: "0.75rem" },
              onClick: () => {
                selectedTargetId = r.id;
                resultsDiv.querySelectorAll("div").forEach((d) => d.style.outline = "none");
                row.style.outline = "1px solid var(--color-primary)";
              },
            },
              h("span", { style: { color: "var(--color-primary)" } }, r.source || ""),
              " ",
              (r.text || "").slice(0, 80)
            );
            resultsDiv.appendChild(row);
          });
        } catch (err) { showToast(err.message, "error"); }
      });

      const confirmBtn = h("button", {
        className: "btn btn-primary",
        onClick: async () => {
          if (!selectedTargetId) { showToast("Select a memory first", "warning"); return; }
          try {
            await api(`/memory/${memoryId}/link`, {
              method: "POST",
              body: JSON.stringify({ to_id: selectedTargetId, type: typeSelect.value }),
            });
            invalidateCache(`/memory/${memoryId}/links`);
            showToast("Link added", "success");
            hideModal();
            updateDetailPanel();
          } catch (err) { showToast(err.message, "error"); }
        },
      }, "Add Link");
      content.appendChild(confirmBtn);
    });
  }

  // -- Check if memory has conflicts --
  async function loadConflictBadge(memoryId, container) {
    try {
      const data = await api("/memory/conflicts");
      const conflicts = data.conflicts || data || [];
      const hasConflict = Array.isArray(conflicts) && conflicts.some(
        (c) => c.id === memoryId || c.conflicts_with === memoryId
          || c.conflicting_memory?.id === memoryId
      );
      if (hasConflict) {
        container.appendChild(
          h("a", { className: "conflict-badge", href: "#/health", style: { marginTop: "8px", display: "inline-flex" } },
            "\u26A0", " Conflict"
          )
        );
      }
    } catch { /* non-critical */ }
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_detail_panel_enhancements -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/app.js tests/test_web_ui.py
git commit -m "feat(ui): enhance memory detail panel with confidence, links, decay, and conflict badge"
```

---

### Task 5: Enhance search results with feedback buttons, confidence mini-bar, and score tooltip

**Files:**
- Modify: `webui/app.js:686-714` (enhance the list item rendering in `renderListView`)
- Modify: `webui/app.js:773-797` (enhance card rendering in `renderGridView`)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_app_js_has_search_feedback_and_explain(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "feedback-btn" in text
    assert "/search/feedback" in text
    assert "/search/explain" in text
    assert "score-tooltip" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_search_feedback_and_explain -v`
Expected: FAIL

- [ ] **Step 3: Enhance renderListView memory items**

In `renderListView` (around line 686), replace the `memories.forEach` loop that builds list items. Each item now includes:
- Color-coded left border based on similarity
- Confidence mini-bar after the score badge
- Feedback ▲/▼ buttons (only shown when `mem.similarity != null`, i.e., search results)

Replace the existing `memories.forEach((mem) => { ... })` block inside `renderListView` with:

```javascript
    memories.forEach((mem) => {
      const isActive = memState.selected && memState.selected.id === mem.id;
      const item = document.createElement("div");
      item.className = `memory-item${isActive ? " active" : ""}`;
      item.dataset.memId = mem.id;

      // Color-coded left border for search results
      if (mem.similarity != null) {
        const borderColor = confidenceColor(mem.similarity);
        item.classList.add(`border-left-${borderColor}`);
      }

      const truncText = (mem.text || "").length > 120
        ? escHtml((mem.text || "").slice(0, 120)) + "..."
        : escHtml(mem.text || "");

      // Header with source, score badge, confidence mini-bar
      const headerEl = h("div", { className: "memory-item-header" },
        h("span", { className: "memory-item-source" }, mem.source || "")
      );

      const rightSide = h("span", { className: "memory-item-id" }, `#${mem.id}`);

      if (mem.similarity != null) {
        const pct = (mem.similarity * 100).toFixed(1);
        const color = confidenceColor(mem.similarity);
        const scoreBadge = h("span", {
          className: `search-result-score`,
          style: { position: "relative", cursor: "help" },
        }, `${pct}%`);

        // Score tooltip on hover (lazy-load explain data)
        let explainLoaded = false;
        scoreBadge.addEventListener("mouseenter", async () => {
          if (explainLoaded) return;
          // Remove any existing tooltips
          const existing = scoreBadge.querySelector(".score-tooltip");
          if (existing) return;
          try {
            const explainData = await api("/search/explain", {
              method: "POST",
              body: JSON.stringify({ query: memState.searchQuery, k: 20, hybrid: true }),
            });
            explainLoaded = true;
            // Find this result in explain data
            const vectorCandidates = explainData.explain?.vector_candidates || [];
            const bm25Candidates = explainData.explain?.bm25_candidates || [];
            const vectorMatch = vectorCandidates.find((c) => c.id === mem.id);
            const bm25Match = bm25Candidates.find((c) => c.id === mem.id);
            const tooltip = h("div", { className: "score-tooltip" },
              h("div", { className: "score-tooltip-label" }, "Score Breakdown"),
              h("div", { className: "score-tooltip-grid" },
                h("div", { className: "score-tooltip-item" },
                  h("div", { className: "score-tooltip-item-label" }, "Vector"),
                  h("div", { className: "score-tooltip-item-value" }, vectorMatch ? vectorMatch.score.toFixed(2) : "N/A")
                ),
                h("div", { className: "score-tooltip-item" },
                  h("div", { className: "score-tooltip-item-label" }, "BM25"),
                  h("div", { className: "score-tooltip-item-value" }, bm25Match ? bm25Match.score.toFixed(2) : "N/A")
                ),
                h("div", { className: "score-tooltip-item" },
                  h("div", { className: "score-tooltip-item-label" }, "Confidence"),
                  h("div", { className: "score-tooltip-item-value" }, mem.confidence != null ? mem.confidence.toFixed(2) : "N/A")
                ),
                h("div", { className: "score-tooltip-item" },
                  h("div", { className: "score-tooltip-item-label" }, "Final"),
                  h("div", { className: `score-tooltip-item-value ${mem.similarity > 0.7 ? "color-success" : ""}` },
                    mem.similarity.toFixed(2))
                ),
              )
            );
            scoreBadge.appendChild(tooltip);
          } catch {
            // Admin-only or error — show simple fallback
            const tooltip = h("div", { className: "score-tooltip" },
              h("div", { className: "score-tooltip-label" }, "Score details require admin access")
            );
            scoreBadge.appendChild(tooltip);
            explainLoaded = true;
          }
        });
        scoreBadge.addEventListener("mouseleave", () => {
          const tooltip = scoreBadge.querySelector(".score-tooltip");
          if (tooltip) tooltip.remove();
          explainLoaded = false; // allow reload on re-hover (cache is at api level)
        });

        rightSide.appendChild(document.createTextNode(" "));
        rightSide.appendChild(scoreBadge);

        // Confidence mini-bar
        if (mem.confidence != null) {
          rightSide.appendChild(document.createTextNode(" "));
          rightSide.appendChild(confidenceBar(mem.confidence, "sm"));
        }
      }

      headerEl.appendChild(rightSide);

      // Text row with optional feedback buttons
      const textRow = h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start" } },
        h("div", { className: "memory-item-text", style: { flex: "1" } })
      );
      textRow.firstChild.innerHTML = truncText;

      // Feedback buttons (only for search results)
      if (mem.similarity != null) {
        const feedbackDiv = h("div", { style: { display: "flex", gap: "4px", marginLeft: "8px", flexShrink: "0" } });

        const upBtn = h("button", { className: "feedback-btn", title: "Relevant" }, "\u25B2");
        const downBtn = h("button", { className: "feedback-btn", title: "Not relevant" }, "\u25BC");

        upBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          try {
            await api("/search/feedback", {
              method: "POST",
              body: JSON.stringify({ memory_id: mem.id, query: memState.searchQuery, signal: "useful" }),
            });
            upBtn.classList.add("active-useful");
            downBtn.classList.remove("active-not-useful");
            showToast("Feedback recorded", "success");
          } catch (err) { showToast(err.message, "error"); }
        });

        downBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          try {
            await api("/search/feedback", {
              method: "POST",
              body: JSON.stringify({ memory_id: mem.id, query: memState.searchQuery, signal: "not_useful" }),
            });
            downBtn.classList.add("active-not-useful");
            upBtn.classList.remove("active-useful");
            showToast("Feedback recorded", "success");
          } catch (err) { showToast(err.message, "error"); }
        });

        feedbackDiv.appendChild(upBtn);
        feedbackDiv.appendChild(downBtn);
        textRow.appendChild(feedbackDiv);
      }

      item.appendChild(headerEl);
      item.appendChild(textRow);

      item.addEventListener("click", () => {
        memState.selected = mem;
        listPanel.querySelectorAll(".memory-item").forEach((el) => {
          el.classList.toggle("active", el.dataset.memId === String(mem.id));
        });
        updateDetailPanel();
      });
      listPanel.appendChild(item);
    });
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_search_feedback_and_explain -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/app.js tests/test_web_ui.py
git commit -m "feat(ui): add search feedback buttons, score tooltip, and confidence mini-bar to search results"
```

---

### Task 6: Add Health page renderer

**Files:**
- Modify: `webui/app.js` (add `registerPage("health", ...)` before the `initMobileSidebar` function, around line 1845)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_app_js_has_health_page_renderer(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert 'registerPage("health"' in text
    assert "/memory/conflicts" in text
    assert "/metrics/extraction-quality" in text or "extraction-quality" in text
    assert "/metrics/search-quality" in text or "search-quality" in text
    assert "/metrics/failures" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_health_page_renderer -v`
Expected: FAIL

- [ ] **Step 3: Add the Health page renderer**

Insert before the `// -- Mobile Sidebar` comment in `webui/app.js`:

```javascript
// ==========================================================================
//  Health Page
// ==========================================================================

registerPage("health", async (container) => {
  container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';

  // Period state
  let period = "7d";

  async function loadHealthPage() {
    container.innerHTML = "";

    // Header
    const header = h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" } },
      h("div", null,
        h("div", { className: "section-title", style: { marginBottom: "2px" } }, "Health"),
        h("div", { style: { fontSize: "0.75rem", color: "var(--color-text-faint)" } }, "System quality and conflict monitoring")
      ),
      h("div", null, (() => {
        const sel = h("select", { className: "period-select" });
        ["today", "7d", "30d", "all"].forEach((p) => {
          const opt = h("option", { value: p }, p === "today" ? "Today" : p === "all" ? "All Time" : `Last ${p}`);
          if (p === period) opt.selected = true;
          sel.appendChild(opt);
        });
        sel.addEventListener("change", (e) => { period = e.target.value; loadHealthPage(); });
        return sel;
      })())
    );
    container.appendChild(header);

    // Load data in parallel — handle admin-only gracefully
    let conflicts = [];
    let extractionQuality = null;
    let searchQuality = null;
    let failures = [];

    const results = await Promise.allSettled([
      api("/memory/conflicts"),
      api(`/metrics/extraction-quality?period=${period}`),
      api(`/metrics/search-quality?period=${period}`),
      api("/metrics/failures?type=extraction&limit=100"),
    ]);

    if (results[0].status === "fulfilled") {
      const d = results[0].value;
      conflicts = d.conflicts || d || [];
    }
    if (results[1].status === "fulfilled") extractionQuality = results[1].value;
    if (results[2].status === "fulfilled") searchQuality = results[2].value;
    if (results[3].status === "fulfilled") {
      const d = results[3].value;
      failures = d.failures || [];
    }

    // Stat cards
    const statGrid = h("div", { className: "health-stat-grid" });

    // Conflicts count
    const conflictCount = Array.isArray(conflicts) ? conflicts.length : 0;
    statGrid.appendChild(h("div", { className: "health-stat-card" },
      h("div", { className: "health-stat-label" }, "Conflicts"),
      h("div", { className: "health-stat-value color-error" }, String(conflictCount)),
      h("div", { className: "health-stat-sub" }, "unresolved")
    ));

    // Extract quality
    if (extractionQuality && extractionQuality.totals) {
      const t = extractionQuality.totals;
      const rate = t.extracted > 0 ? Math.round(((t.stored + t.updated) / t.extracted) * 100) : 0;
      statGrid.appendChild(h("div", { className: "health-stat-card" },
        h("div", { className: "health-stat-label" }, "Extract Quality"),
        h("div", { className: "health-stat-value color-success" }, `${rate}%`),
        h("div", { className: "health-stat-sub" }, "success rate")
      ));
    } else {
      statGrid.appendChild(h("div", { className: "health-stat-card" },
        h("div", { className: "health-stat-label" }, "Extract Quality"),
        h("div", { className: "health-stat-value", style: { fontSize: "0.875rem", color: "var(--color-text-faint)" } }, "Admin only"),
        h("div", { className: "health-stat-sub" }, "requires admin key")
      ));
    }

    // Search quality
    if (searchQuality && searchQuality.feedback) {
      const ratio = searchQuality.feedback.useful_ratio ?? 0;
      statGrid.appendChild(h("div", { className: "health-stat-card" },
        h("div", { className: "health-stat-label" }, "Search Quality"),
        h("div", { className: "health-stat-value", style: { color: "var(--color-primary)" } }, ratio.toFixed(2)),
        h("div", { className: "health-stat-sub" }, "useful ratio")
      ));
    } else {
      statGrid.appendChild(h("div", { className: "health-stat-card" },
        h("div", { className: "health-stat-label" }, "Search Quality"),
        h("div", { className: "health-stat-value", style: { color: "var(--color-primary)" } }, "\u2014"),
        h("div", { className: "health-stat-sub" }, "no feedback data")
      ));
    }

    // Failures count
    const failCount = failures.length;
    statGrid.appendChild(h("div", { className: "health-stat-card" },
      h("div", { className: "health-stat-label" }, "Failures"),
      h("div", { className: `health-stat-value ${failCount === 0 ? "color-success" : "color-warning"}` }, String(failCount)),
      h("div", { className: "health-stat-sub" }, "recent")
    ));

    container.appendChild(statGrid);

    // Conflicts section
    const conflictsTitle = h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px", marginTop: "4px" } },
      h("div", { className: "section-title" }, "Conflicts")
    );
    container.appendChild(conflictsTitle);

    if (conflictCount === 0) {
      container.appendChild(
        h("div", { className: "empty-state", style: { padding: "40px 20px" } },
          h("div", { className: "empty-state-icon color-success" }, "\u2713"),
          h("div", { className: "empty-state-title" }, "No conflicts detected"),
          h("div", { className: "empty-state-text" }, "All memories are consistent.")
        )
      );
    } else {
      const displayConflicts = conflicts.slice(0, 3);
      displayConflicts.forEach((conflict) => {
        const card = h("div", { className: "conflict-card" });

        const badgeRow = h("div", { style: { display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" } },
          h("span", { className: "conflict-type-badge" }, "CONTRADICTS"),
          h("span", { style: { fontSize: "0.6875rem", color: "var(--color-text-faint)" } }, conflict.source || "")
        );
        card.appendChild(badgeRow);

        const pair = h("div", { className: "conflict-pair" },
          h("div", { className: "conflict-side conflict-side--a" },
            h("div", { className: "conflict-side-label" }, "Memory A"),
            h("div", { style: { color: "var(--color-text)" } }, escHtml((conflict.text || "").slice(0, 120)))
          ),
          h("div", { className: "conflict-side conflict-side--b" },
            h("div", { className: "conflict-side-label" }, "Memory B"),
            h("div", { style: { color: "var(--color-text)" } }, escHtml((conflict.conflicting_memory?.text || "").slice(0, 120)))
          )
        );
        card.appendChild(pair);

        const actions = h("div", { className: "conflict-actions" });

        const keepABtn = h("button", { className: "conflict-action-btn" }, "Keep A");
        keepABtn.addEventListener("click", async () => {
          if (!confirm("Delete Memory B and keep Memory A?")) return;
          try {
            const deleteId = conflict.conflicting_memory?.id || conflict.conflicts_with;
            if (deleteId) {
              await api(`/memory/${deleteId}`, { method: "DELETE" });
              invalidateCache();
              showToast("Conflict resolved — kept Memory A", "success");
              loadHealthPage();
            }
          } catch (err) { showToast(err.message, "error"); }
        });

        const keepBBtn = h("button", { className: "conflict-action-btn" }, "Keep B");
        keepBBtn.addEventListener("click", async () => {
          if (!confirm("Delete Memory A and keep Memory B?")) return;
          try {
            const deleteId = conflict.id;
            if (deleteId) {
              await api(`/memory/${deleteId}`, { method: "DELETE" });
              invalidateCache();
              showToast("Conflict resolved — kept Memory B", "success");
              loadHealthPage();
            }
          } catch (err) { showToast(err.message, "error"); }
        });

        const dismissBtn = h("button", { className: "conflict-dismiss-btn" }, "Dismiss");
        dismissBtn.addEventListener("click", async () => {
          try {
            const memId = conflict.memory_id || conflict.id;
            if (memId) {
              await api(`/memory/${memId}`, {
                method: "PATCH",
                body: JSON.stringify({ metadata_patch: { conflicts_with: null } }),
              });
              invalidateCache();
              showToast("Conflict dismissed", "success");
              loadHealthPage();
            }
          } catch (err) { showToast(err.message, "error"); }
        });

        actions.appendChild(keepABtn);
        actions.appendChild(keepBBtn);
        actions.appendChild(dismissBtn);
        card.appendChild(actions);
        container.appendChild(card);
      });

      if (conflicts.length > 3) {
        container.appendChild(
          h("div", { style: { fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "8px" } },
            `Showing 3 of ${conflicts.length} conflicts`)
        );
      }
    }

    // Quality metrics grid
    const qualityTitle = h("div", { className: "section-title mt-24" }, "Quality Metrics");
    container.appendChild(qualityTitle);

    const qualityGrid = h("div", { className: "quality-grid" });

    // Left: Extraction Quality per-source
    const extractPanel = h("div", { className: "quality-panel" },
      h("div", { className: "quality-panel-title" }, "Extraction Quality")
    );
    if (extractionQuality && extractionQuality.by_source) {
      for (const [source, data] of Object.entries(extractionQuality.by_source)) {
        const rate = data.extracted > 0 ? Math.round(((data.stored + data.updated) / data.extracted) * 100) : 0;
        const color = confidenceColor(rate / 100);
        extractPanel.appendChild(
          h("div", { className: "quality-row" },
            h("span", null, source),
            h("span", { className: `color-${color}` }, `${rate}%`)
          )
        );
      }
    } else {
      extractPanel.appendChild(
        h("div", { style: { fontSize: "0.75rem", color: "var(--color-text-faint)" } }, "Admin access required")
      );
    }
    qualityGrid.appendChild(extractPanel);

    // Right: Search Feedback
    const searchPanel = h("div", { className: "quality-panel" },
      h("div", { className: "quality-panel-title" }, "Search Feedback")
    );
    if (searchQuality && searchQuality.feedback) {
      const fb = searchQuality.feedback;
      searchPanel.appendChild(h("div", { className: "quality-row" },
        h("span", null, "Useful signals"),
        h("span", { className: "color-success" }, String(fb.useful || 0))
      ));
      searchPanel.appendChild(h("div", { className: "quality-row" },
        h("span", null, "Not useful signals"),
        h("span", { className: "color-error" }, String(fb.not_useful || 0))
      ));
      if (searchQuality.rank_distribution) {
        searchPanel.appendChild(h("div", { className: "quality-row" },
          h("span", null, "Top-3 rank ratio"),
          h("span", { style: { color: "var(--color-primary)" } }, String(searchQuality.rank_distribution.top_3 ?? "N/A"))
        ));
      }
    } else {
      searchPanel.appendChild(
        h("div", { style: { fontSize: "0.75rem", color: "var(--color-text-faint)" } }, "No feedback data yet")
      );
    }
    qualityGrid.appendChild(searchPanel);

    container.appendChild(qualityGrid);
  }

  await loadHealthPage();
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_has_health_page_renderer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/app.js tests/test_web_ui.py
git commit -m "feat(ui): add Health page with conflicts, quality metrics, and failures"
```

---

### Task 7: Add extraction quality badge to Extractions page

**Files:**
- Modify: `webui/app.js:1065-1182` (enhance Extractions page renderer to show quality badge)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`:

```python
def test_app_js_extractions_page_has_quality_badge(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "/metrics/extraction-quality" in text
    assert "Quality:" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_extractions_page_has_quality_badge -v`
Expected: FAIL (no `#/health` link exists in the code yet from Extractions page context)

- [ ] **Step 3: Add quality badge to Extractions page**

In the Extractions page renderer (`registerPage("extractions", ...)`), after the stat grid is appended (around line 1110), add a small async block that fetches extraction quality and adds a badge linking to Health:

Find this line in the Extractions page:
```javascript
    container.appendChild(statSection);
    container.appendChild(statGrid);
```

And add after it:

```javascript
    // Quality badge linking to Health page
    try {
      const eq = await api(`/metrics/extraction-quality?period=7d`);
      if (eq && eq.totals && eq.totals.extracted > 0) {
        const rate = Math.round(((eq.totals.stored + eq.totals.updated) / eq.totals.extracted) * 100);
        const color = confidenceColor(rate / 100);
        const badge = h("a", {
          href: "#/health",
          style: { display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.75rem", color: `var(--color-${color})`, textDecoration: "none", marginTop: "8px" },
        },
          `Quality: ${rate}%`,
          h("span", { style: { fontSize: "0.625rem", color: "var(--color-text-faint)" } }, " \u2192 Health")
        );
        container.appendChild(badge);
      }
    } catch { /* admin-only, skip silently */ }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py::test_app_js_extractions_page_has_quality_badge -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add webui/app.js tests/test_web_ui.py
git commit -m "feat(ui): add extraction quality badge linking to Health page"
```

---

### Task 8: Run full test suite and verify no regressions

**Files:**
- No changes — verification only

- [ ] **Step 1: Run all web UI tests**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_web_ui.py -v`
Expected: All new tests PASS. Pre-existing failures (if any) remain unchanged.

- [ ] **Step 2: Run full test suite for regressions**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/ -v --timeout=60 -x`
Expected: No new failures introduced.

- [ ] **Step 3: Manual smoke test**

Start the server and verify in the browser:
```bash
cd /Users/divyekant/Projects/memories && docker compose up -d
```

Then open `http://localhost:8900/ui` and check:
1. Dashboard loads normally
2. Memories page — select a memory, verify confidence bar and links section appear
3. Search for something — verify feedback buttons (▲/▼) appear, hover over score badge for tooltip
4. Click Health in sidebar — verify stat cards, conflicts section, quality metrics render
5. Extractions page — verify quality badge appears (if admin key is set)

- [ ] **Step 4: Commit any fixes from smoke test**

```bash
cd /Users/divyekant/Projects/memories
git add -A
git commit -m "fix(ui): address smoke test findings"
```

(Only if fixes were needed)
