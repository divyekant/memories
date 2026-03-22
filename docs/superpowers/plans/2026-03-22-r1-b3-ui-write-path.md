# R1 B3: UI Write-Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add write capabilities to the Memories operator workbench UI — create, edit, link, merge, bulk actions, extraction trigger, lifecycle panel, and conflict resolution.

**Architecture:** 7 reusable components in new `webui/components.js` (ES module), imported by `app.js`. All backend APIs already exist from B1/B2 except one pre-task (audit log filter). Feature code stays in `app.js` page render functions. TDD with tests in `tests/test_web_ui.py`.

**Tech Stack:** Vanilla JS (ES modules), CSS custom properties, FastAPI backend, pytest

**Design spec:** `docs/superpowers/specs/2026-03-19-r1-controllable-memory-design.md`

**Test baseline:** 874 tests passing

---

## File Map

| File | Role | Action |
|------|------|--------|
| `webui/components.js` | 7 reusable UI components | **Create** |
| `webui/app.js` | Page renders, feature integration, showToast extension | **Modify** |
| `webui/styles.css` | New component + feature styles | **Modify** |
| `audit_log.py` | Add resource_id filter to query() | **Modify** |
| `app.py` | Add resource_id param to GET /audit | **Modify** |
| `tests/test_web_ui.py` | All B3 UI tests | **Modify** |
| `tests/test_audit_log.py` | Audit resource_id filter test | **Modify** |

---

### Task 0: Backend Pre-Task — Audit Log resource_id Filter

**Files:**
- Modify: `audit_log.py` (query method ~line 86)
- Modify: `app.py` (GET /audit endpoint)
- Test: `tests/test_audit_log.py`

- [ ] **Step 1: Write failing test for resource_id filter**

```python
# In tests/test_audit_log.py
def test_query_filter_by_resource_id(audit_log):
    """query() should filter by resource_id when provided."""
    audit_log.log(action="memory.created", resource_id="42")
    audit_log.log(action="memory.updated", resource_id="42")
    audit_log.log(action="memory.created", resource_id="99")

    results = audit_log.query(resource_id="42")
    assert len(results) == 2
    assert all(r["resource_id"] == "42" for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audit_log.py::test_query_filter_by_resource_id -v`
Expected: FAIL — `query()` does not accept `resource_id` param

- [ ] **Step 3: Add resource_id filter to audit_log.query()**

In `audit_log.py`, modify the `query()` method:

```python
def query(self, action=None, key_id=None, resource_id=None, limit=50, offset=0):
    sql = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if action:
        sql += " AND action = ?"
        params.append(action)
    if key_id:
        sql += " AND key_id = ?"
        params.append(key_id)
    if resource_id:
        sql += " AND resource_id = ?"
        params.append(resource_id)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    # ... rest unchanged
```

- [ ] **Step 4: Add SQLite index on resource_id**

In `audit_log.py`, in the `_init_db()` or `__init__` method, add after table creation:

```python
cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource_id ON audit_log(resource_id)")
```

- [ ] **Step 5: Add resource_id query param to GET /audit endpoint**

In `app.py`, find the `GET /audit` endpoint and add `resource_id: Optional[str] = None` parameter, pass it to `audit_log.query(resource_id=resource_id)`.

- [ ] **Step 6: Run all audit tests**

Run: `pytest tests/test_audit_log.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add audit_log.py app.py tests/test_audit_log.py
git commit -m "feat: add resource_id filter to audit log query"
```

---

### Task 1: Reusable Components — `webui/components.js`

**Files:**
- Create: `webui/components.js`
- Modify: `webui/styles.css` (component styles)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests for all 7 component functions**

Add to `tests/test_web_ui.py`:

```python
def test_components_js_exports(client):
    """components.js should export all 7 reusable component functions."""
    resp = client.get("/ui/static/components.js")
    assert resp.status_code == 200
    js = resp.text
    for fn in [
        "editableField", "actionBadge", "approvalToggle",
        "bulkSelectMode", "memoryCard", "timelineEvent", "comparisonPanel",
    ]:
        assert f"export function {fn}" in js, f"Missing export: {fn}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_ui.py::test_components_js_exports -v`
Expected: FAIL — 404 (file doesn't exist)

- [ ] **Step 3: Create components.js with all 7 components**

Create `webui/components.js`:

```javascript
/* ==========================================================================
   Memories Web UI — Reusable Components
   ========================================================================== */

import {
  h, escHtml, timeAgo, confidenceColor, confidenceBar, linkTypeColor,
} from "./app.js";

// -- editableField ---------------------------------------------------------
// Click-to-edit field. type: "text" → textarea, "input" → input, "select" → dropdown
export function editableField(container, { value, type = "input", options, onSave, onCancel }) {
  const display = h("span", {
    className: "editable-field",
    onclick: () => switchToEdit(),
  }, value || "(empty)");

  function switchToEdit() {
    container.innerHTML = "";
    let input;
    if (type === "text") {
      input = h("textarea", { className: "editable-textarea", value: value || "" });
      input.textContent = value || "";
    } else if (type === "select") {
      input = h("select", { className: "editable-select" },
        ...(options || []).map(opt =>
          h("option", { value: opt, selected: opt === value }, opt)
        )
      );
    } else {
      input = h("input", { className: "editable-input", type: "text", value: value || "" });
    }
    const saveBtn = h("button", {
      className: "btn btn-sm btn-primary",
      onclick: () => {
        const newVal = type === "text" ? input.value : input.value;
        if (onSave) onSave(newVal);
      },
    }, "Save");
    const cancelBtn = h("button", {
      className: "btn btn-sm",
      onclick: () => {
        container.innerHTML = "";
        container.appendChild(display);
        if (onCancel) onCancel();
      },
    }, "Cancel");
    const actions = h("div", { className: "editable-actions" }, saveBtn, cancelBtn);
    container.appendChild(input);
    container.appendChild(actions);
    input.focus();
  }

  container.appendChild(display);
}

// -- actionBadge -----------------------------------------------------------
// Styled badge for AUDN actions: ADD, UPDATE, DELETE, NOOP, CONFLICT
export function actionBadge(action) {
  const colors = {
    ADD: "success", UPDATE: "info", DELETE: "error",
    NOOP: "muted", CONFLICT: "warning",
  };
  const variant = colors[action] || "muted";
  return h("span", { className: `action-badge action-badge--${variant}` }, action);
}

// -- approvalToggle --------------------------------------------------------
// Tri-state toggle: approved ✓ / rejected ✕ / skipped —
export function approvalToggle({ state = "skipped", onChange }) {
  const btn = h("button", {
    className: `approval-toggle approval-toggle--${state}`,
    onclick: () => {
      const next = state === "approved" ? "rejected"
        : state === "rejected" ? "skipped" : "approved";
      if (onChange) onChange(next);
    },
  }, state === "approved" ? "\u2713" : state === "rejected" ? "\u2715" : "\u2014");
  return btn;
}

// -- bulkSelectMode --------------------------------------------------------
// Manages checkbox selection state and action bar
export function bulkSelectMode({ items, onSelectionChange, actions }) {
  const selected = new Set();

  function toggle(id) {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    if (onSelectionChange) onSelectionChange([...selected]);
    updateBar();
  }

  function selectAll() {
    items.forEach(i => selected.add(i.id));
    if (onSelectionChange) onSelectionChange([...selected]);
    updateBar();
  }

  function deselectAll() {
    selected.clear();
    if (onSelectionChange) onSelectionChange([]);
    updateBar();
  }

  let barEl = null;

  function renderBar(container) {
    barEl = h("div", { className: "bulk-action-bar" },
      h("label", { className: "bulk-select-all" },
        h("input", {
          type: "checkbox",
          onchange: (e) => e.target.checked ? selectAll() : deselectAll(),
        }),
        "Select all"
      ),
      h("span", { className: "bulk-count" }, `${selected.size} selected`),
      h("div", { className: "bulk-actions" },
        ...(actions || []).map(a =>
          h("button", {
            className: `btn btn-sm ${a.style || ""}`,
            onclick: () => a.onClick([...selected]),
            disabled: selected.size === 0,
          }, a.label)
        )
      )
    );
    container.prepend(barEl);
  }

  function updateBar() {
    if (barEl) {
      const count = barEl.querySelector(".bulk-count");
      if (count) count.textContent = `${selected.size} selected`;
      barEl.querySelectorAll(".bulk-actions .btn").forEach(b => {
        b.disabled = selected.size === 0;
      });
    }
  }

  return { toggle, selectAll, deselectAll, isSelected: (id) => selected.has(id), selected, renderBar };
}

// -- memoryCard ------------------------------------------------------------
// Memory card for list view. mode: "browse" | "search" | "select"
export function memoryCard(mem, { mode = "browse", isSelected = false, onSelect, onActivate }) {
  const card = h("div", {
    className: `memory-item${isSelected ? " selected" : ""}`,
    onclick: () => {
      if (mode === "select" && onSelect) onSelect(mem.id);
      else if (onActivate) onActivate(mem.id);
    },
  });

  // Checkbox for select mode
  if (mode === "select") {
    card.appendChild(h("input", {
      type: "checkbox",
      checked: isSelected,
      className: "bulk-checkbox",
      onclick: (e) => { e.stopPropagation(); if (onSelect) onSelect(mem.id); },
    }));
  }

  // Header: source + ID + pin badge
  const headerChildren = [
    h("span", { className: "memory-item-source" }, mem.source || "unknown"),
  ];
  if (mem.pinned) {
    headerChildren.push(h("span", { className: "badge badge-primary", title: "Pinned" }, "\uD83D\uDCCC"));
  }
  if (mem.archived) {
    headerChildren.push(h("span", { className: "badge badge-warning", title: "Archived" }, "archived"));
  }
  headerChildren.push(h("span", { className: "memory-item-id" }, `#${mem.id}`));
  card.appendChild(h("div", { className: "memory-item-header" }, ...headerChildren));

  // Text preview
  const text = mem.text || "";
  card.appendChild(h("div", { className: "memory-item-text" },
    text.length > 120 ? text.slice(0, 120) + "\u2026" : text
  ));

  // Search mode extras: score dot + score badge
  if (mode === "search" && mem.similarity != null) {
    const score = mem.similarity ?? mem.rrf_score ?? 0;
    const color = confidenceColor(score);
    card.appendChild(h("div", { className: "memory-item-score" },
      h("span", { className: `score-dot score-dot--${color}` }),
      h("span", { className: `badge badge-${color}` }, `${Math.round(score * 100)}%`),
    ));
  }

  return card;
}

// -- timelineEvent ---------------------------------------------------------
// Vertical timeline row: colored dot + title + detail + timestamp
export function timelineEvent({ color = "info", title, detail, timestamp }) {
  return h("div", { className: "timeline-event" },
    h("span", { className: `timeline-dot timeline-dot--${color}` }),
    h("div", { className: "timeline-content" },
      h("span", { className: "timeline-title" }, title),
      detail ? h("span", { className: "timeline-detail" }, detail) : null,
      timestamp ? h("span", { className: "timeline-ts" }, timeAgo(timestamp)) : null,
    ),
  );
}

// -- comparisonPanel -------------------------------------------------------
// Side-by-side memory comparison with colored borders
export function comparisonPanel({ memA, memB, labelA = "Memory A", labelB = "Memory B", colorA = "error", colorB = "success" }) {
  return h("div", { className: "comparison-panel" },
    h("div", { className: `comparison-side comparison-side--${colorA}` },
      h("div", { className: "comparison-label" }, labelA),
      h("div", { className: "comparison-source" }, memA.source || "unknown"),
      h("div", { className: "comparison-text" }, memA.text || ""),
      memA.confidence != null ? confidenceBar(memA.confidence, "sm") : null,
    ),
    h("div", { className: `comparison-side comparison-side--${colorB}` },
      h("div", { className: "comparison-label" }, labelB),
      h("div", { className: "comparison-source" }, memB.source || "unknown"),
      h("div", { className: "comparison-text" }, memB.text || ""),
      memB.confidence != null ? confidenceBar(memB.confidence, "sm") : null,
    ),
  );
}
```

- [ ] **Step 4: Add component CSS to styles.css**

Append to `webui/styles.css`:

```css
/* -- Editable Field ---------------------------------------------------- */
.editable-field { cursor: pointer; border-bottom: 1px dashed var(--color-text-faint); }
.editable-field:hover { border-bottom-color: var(--color-primary); }
.editable-textarea { width: 100%; min-height: 80px; resize: vertical; background: var(--color-bg-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: 4px; padding: 8px; font: inherit; }
.editable-input { width: 100%; background: var(--color-bg-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: 4px; padding: 6px 8px; font: inherit; }
.editable-select { background: var(--color-bg-surface); color: var(--color-text); border: 1px solid var(--color-border); border-radius: 4px; padding: 6px 8px; font: inherit; }
.editable-actions { display: flex; gap: 6px; margin-top: 6px; }

/* -- Action Badge ------------------------------------------------------ */
.action-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.action-badge--success { background: rgba(22,163,74,0.15); color: var(--color-success); }
.action-badge--info { background: rgba(37,99,235,0.15); color: var(--color-info); }
.action-badge--error { background: rgba(220,38,38,0.15); color: var(--color-error); }
.action-badge--warning { background: rgba(202,138,4,0.15); color: var(--color-warning); }
.action-badge--muted { background: rgba(102,102,102,0.15); color: var(--color-text-faint); }

/* -- Approval Toggle --------------------------------------------------- */
.approval-toggle { width: 28px; height: 28px; border-radius: 4px; border: 1px solid var(--color-border); background: transparent; color: var(--color-text); cursor: pointer; font-size: 14px; display: inline-flex; align-items: center; justify-content: center; }
.approval-toggle--approved { background: rgba(22,163,74,0.15); color: var(--color-success); border-color: var(--color-success); }
.approval-toggle--rejected { background: rgba(220,38,38,0.15); color: var(--color-error); border-color: var(--color-error); }
.approval-toggle--skipped { color: var(--color-text-faint); }

/* -- Bulk Select ------------------------------------------------------- */
.bulk-action-bar { display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: var(--color-bg-elevated); border: 1px solid var(--color-border); border-radius: 8px; margin-bottom: 12px; }
.bulk-select-all { display: flex; align-items: center; gap: 6px; font-size: 0.8125rem; cursor: pointer; color: var(--color-text-muted); }
.bulk-count { font-size: 0.8125rem; color: var(--color-primary); font-weight: 600; }
.bulk-actions { display: flex; gap: 6px; margin-left: auto; }
.bulk-checkbox { margin-right: 8px; accent-color: var(--color-primary); }
.memory-item.selected { background: var(--color-primary-dim); }

/* -- Timeline ---------------------------------------------------------- */
.timeline-event { display: flex; gap: 12px; padding: 8px 0; position: relative; }
.timeline-event:not(:last-child)::after { content: ""; position: absolute; left: 5px; top: 24px; bottom: -8px; width: 1px; background: var(--color-border); }
.timeline-dot { width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; margin-top: 3px; }
.timeline-dot--success { background: var(--color-success); }
.timeline-dot--info { background: var(--color-info); }
.timeline-dot--error { background: var(--color-error); }
.timeline-dot--warning { background: var(--color-warning); }
.timeline-dot--primary { background: var(--color-primary); }
.timeline-content { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.timeline-title { font-size: 0.8125rem; color: var(--color-text); }
.timeline-detail { font-size: 0.75rem; color: var(--color-text-muted); }
.timeline-ts { font-size: 0.6875rem; color: var(--color-text-faint); }

/* -- Comparison Panel -------------------------------------------------- */
.comparison-panel { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.comparison-side { padding: 14px; border-radius: 8px; border-left: 3px solid transparent; background: var(--color-bg-surface); }
.comparison-side--error { border-left-color: var(--color-error); }
.comparison-side--success { border-left-color: var(--color-success); }
.comparison-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; color: var(--color-text-muted); margin-bottom: 8px; }
.comparison-source { font-size: 0.75rem; color: var(--color-primary); margin-bottom: 6px; }
.comparison-text { font-size: 0.8125rem; color: var(--color-text); line-height: 1.5; white-space: pre-wrap; }

/* -- Score Dot (search mode) ------------------------------------------- */
.memory-item-score { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.score-dot { width: 6px; height: 6px; border-radius: 50%; }
.score-dot--success { background: var(--color-success); }
.score-dot--warning { background: var(--color-warning); }
.score-dot--error { background: var(--color-error); }

@media (max-width: 768px) {
  .comparison-panel { grid-template-columns: 1fr; }
}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_web_ui.py::test_components_js_exports -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webui/components.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: add 7 reusable UI components in components.js"
```

---

### Task 2: Extend showToast for Action Buttons

**Files:**
- Modify: `webui/app.js` (showToast function ~line 368)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing test**

```python
def test_show_toast_accepts_action_param(client):
    """showToast should accept optional action object for undo buttons."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    # Verify showToast signature accepts options/action parameter
    assert "action" in js[js.index("function showToast"):js.index("function showToast") + 300]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_ui.py::test_show_toast_accepts_action_param -v`
Expected: FAIL — current showToast has no action param

- [ ] **Step 3: Extend showToast**

In `webui/app.js`, replace the `showToast` function:

```javascript
export function showToast(message, type = "info", action = null) {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const children = [message];
  if (action && action.label && action.onClick) {
    children.push(h("button", {
      className: "toast-action-btn",
      onclick: (e) => { e.stopPropagation(); action.onClick(); toast.remove(); },
    }, action.label));
  }

  const toast = h("div", { className: `toast toast-${type}` }, ...children);
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("toast-exit");
    toast.addEventListener("animationend", () => toast.remove());
  }, action ? 8000 : 4000); // Longer dismiss time when action present
}
```

- [ ] **Step 4: Add toast action button CSS**

Append to `webui/styles.css`:

```css
/* -- Toast Action Button ----------------------------------------------- */
.toast-action-btn { background: transparent; border: 1px solid currentColor; color: inherit; padding: 2px 10px; border-radius: 4px; cursor: pointer; font-size: 0.75rem; margin-left: 12px; font-weight: 600; }
.toast-action-btn:hover { background: rgba(255,255,255,0.1); }
```

- [ ] **Step 5: Run test**

Run: `pytest tests/test_web_ui.py::test_show_toast_accepts_action_param -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webui/app.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: extend showToast with optional action button for undo"
```

---

### Task 3: Item 6 — Create Memory from UI

**Files:**
- Modify: `webui/app.js` (memories page ~line 617)
- Modify: `webui/styles.css`
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_create_memory_button_exists(client):
    """Memories page JS should contain create memory button and modal."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "showCreateMemoryModal" in js or "createMemoryModal" in js
    assert "POST" in js  # POST /memory/add call
    assert "+ Create" in js or "Create" in js


def test_create_memory_styles(client):
    """CSS should include create button and empty state styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".create-memory-btn" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_ui.py::test_create_memory_button_exists tests/test_web_ui.py::test_create_memory_styles -v`
Expected: FAIL

- [ ] **Step 3: Add create memory modal to memories page**

In `webui/app.js`, inside the `registerPage("memories", ...)` function, after the `buildSkeleton()` function, add:

```javascript
function showCreateMemoryModal() {
  showModal((content) => {
    const lastSource = localStorage.getItem("memories-last-source") || "";
    content.appendChild(h("h3", { className: "modal-title" }, "Create Memory"));

    const textArea = h("textarea", {
      className: "editable-textarea",
      placeholder: "Memory text...",
      style: { minHeight: "120px" },
    });
    const sourceInput = h("input", {
      className: "editable-input",
      type: "text",
      placeholder: "Source (e.g. claude-code/project)",
      value: lastSource,
    });
    const categorySelect = h("select", { className: "editable-select" },
      h("option", { value: "decision" }, "decision"),
      h("option", { value: "learning" }, "learning"),
      h("option", { value: "detail", selected: true }, "detail"),
    );

    const form = h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
      h("label", { className: "modal-label" }, "Text"),
      textArea,
      h("label", { className: "modal-label" }, "Source"),
      sourceInput,
      h("label", { className: "modal-label" }, "Category"),
      categorySelect,
    );
    content.appendChild(form);

    const actions = h("div", { className: "modal-actions" },
      h("button", { className: "btn", onclick: hideModal }, "Cancel"),
      h("button", {
        className: "btn btn-primary",
        onclick: async () => {
          const text = textArea.value.trim();
          const source = sourceInput.value.trim();
          if (!text) { showToast("Text is required", "error"); return; }
          if (!source) { showToast("Source is required", "error"); return; }
          try {
            await api("/memory/add", {
              method: "POST",
              body: JSON.stringify({
                text,
                source,
                metadata: { category: categorySelect.value },
              }),
            });
            localStorage.setItem("memories-last-source", source);
            invalidateCache();
            hideModal();
            showToast("Memory created", "success");
            loadMemories();
          } catch (e) {
            showToast(e.message, "error");
          }
        },
      }, "Create"),
    );
    content.appendChild(actions);
  });
}
```

- [ ] **Step 4: Add "+ Create" button to skeleton topbar**

In the `buildSkeleton()` function, after the search input, add:

```javascript
const createBtn = h("button", {
  className: "btn btn-primary create-memory-btn",
  onclick: showCreateMemoryModal,
}, "+ Create");
```

Insert it into the topbar actions area of the memories page skeleton.

- [ ] **Step 5: Add empty state CTA**

In `renderContent()`, when `memories.length === 0` and no search is active, render:

```javascript
contentDiv.appendChild(h("div", { className: "empty-state" },
  h("p", {}, "No memories yet"),
  h("button", {
    className: "btn btn-primary",
    onclick: showCreateMemoryModal,
  }, "Create your first memory"),
));
```

- [ ] **Step 6: Add CSS**

```css
.create-memory-btn { font-size: 0.8125rem; }
.modal-label { font-size: 0.75rem; font-weight: 600; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 48px; color: var(--color-text-muted); }
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_web_ui.py -v -k "create_memory"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add webui/app.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: add create memory modal to memories page"
```

---

### Task 4: Item 7 — Inline Edit

**Files:**
- Modify: `webui/app.js` (updateDetailPanel ~line 903)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_inline_edit_in_detail_panel(client):
    """Detail panel should support inline editing via editableField."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "editableField" in js
    assert "PATCH" in js  # PATCH /memory/{id}


def test_pin_archive_controls(client):
    """Detail panel should have pin toggle and archive button."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "pinned" in js
    assert "archived" in js
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_ui.py -v -k "inline_edit or pin_archive"`
Expected: FAIL

- [ ] **Step 3: Import editableField and add inline editing to detail panel**

At top of `app.js`, add import:
```javascript
import { editableField, actionBadge, approvalToggle, bulkSelectMode, memoryCard, timelineEvent, comparisonPanel } from "./components.js";
```

In `updateDetailPanel()`, replace the static text/source/category displays with editable versions using `editableField()`. Add pin toggle badge and archive button with undo toast:

```javascript
// Pin toggle
const pinBtn = h("button", {
  className: `btn btn-sm ${mem.pinned ? "btn-primary" : ""}`,
  title: mem.pinned ? "Unpin" : "Pin",
  onclick: async () => {
    await api(`/memory/${mem.id}`, {
      method: "PATCH",
      body: JSON.stringify({ pinned: !mem.pinned }),
    });
    invalidateCache();
    updateDetailPanel();
    showToast(mem.pinned ? "Unpinned" : "Pinned", "success");
  },
}, "\uD83D\uDCCC");

// Archive with undo
const archiveBtn = h("button", {
  className: "btn btn-sm",
  onclick: async () => {
    await api(`/memory/${mem.id}`, {
      method: "PATCH",
      body: JSON.stringify({ archived: true }),
    });
    invalidateCache();
    loadMemories();
    showToast("Memory archived", "info", {
      label: "Undo",
      onClick: async () => {
        await api(`/memory/${mem.id}`, {
          method: "PATCH",
          body: JSON.stringify({ archived: false }),
        });
        invalidateCache();
        loadMemories();
      },
    });
  },
}, "Archive");
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_web_ui.py -v -k "inline_edit or pin_archive"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webui/app.js tests/test_web_ui.py
git commit -m "feat: add inline edit, pin toggle, and archive with undo to detail panel"
```

---

### Task 5: Item 8 — Link Memories Enhancement

**Files:**
- Modify: `webui/app.js` (showAddLinkModal ~line 1060, loadLinks ~line 988)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing test**

```python
def test_enhanced_link_modal(client):
    """Link modal should show source, text preview, and confidence bar."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    # Check for bidirectional display
    assert "incoming" in js or "outgoing" in js or "direction" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_ui.py::test_enhanced_link_modal -v`
Expected: FAIL

- [ ] **Step 3: Enhance showAddLinkModal**

In the existing `showAddLinkModal()`, enhance search results to show source + text preview + confidence bar. After adding a link, auto-expand the linked section.

- [ ] **Step 4: Enhance loadLinks for bidirectional display**

In `loadLinks()`, add direction indicators (→ outgoing, ← incoming) using the `include_incoming=true` response data.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_web_ui.py::test_enhanced_link_modal -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webui/app.js tests/test_web_ui.py
git commit -m "feat: enhance link modal with source, preview, and bidirectional display"
```

---

### Task 6: Item 10 — Bulk Actions

**Files:**
- Modify: `webui/app.js` (memories page)
- Modify: `webui/styles.css`
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_bulk_select_mode(client):
    """Memories page should support bulk select with action bar."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "bulkSelectMode" in js
    assert "archive-batch" in js
    assert "delete-batch" in js


def test_bulk_action_styles(client):
    """CSS should include bulk action bar styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".bulk-action-bar" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_ui.py -v -k "bulk_select or bulk_action"`
Expected: FAIL

- [ ] **Step 3: Add "Select" toggle and bulk action integration**

In the memories page skeleton, add a "Select" toggle button. When toggled, switch card rendering to `mode: "select"` and show the bulk action bar with Archive/Delete/Retag/Re-source/Merge buttons.

- Archive: `POST /memory/archive-batch` with `{ids}`
- Delete: confirmation modal → `POST /memory/delete-batch` with `{ids}`
- Retag: modal with category dropdown → `PATCH /memory/{id}` for each
- Re-source: modal with source input → `PATCH /memory/{id}` for each
- Merge: requires 2+ selected → triggers merge modal (Task 7)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_web_ui.py -v -k "bulk"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webui/app.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: add bulk select mode with archive, delete, retag, re-source actions"
```

---

### Task 7: Item 9 — Merge Memories

**Files:**
- Modify: `webui/app.js` (memories page)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_merge_modal(client):
    """Merge modal should use comparisonPanel and call POST /memory/merge."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "comparisonPanel" in js
    assert "/memory/merge" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_ui.py::test_merge_modal -v`
Expected: FAIL

- [ ] **Step 3: Add merge modal**

Add `showMergeModal(selectedIds)` function. It:
1. Fetches memory details for selected IDs via `POST /memory/get-batch`
2. Shows `comparisonPanel` with source memories (red border, "will be archived")
3. Shows editable textarea for merged result (green border)
4. Source input (prefilled from first selected memory)
5. "Merge & Archive Originals" button → `POST /memory/merge` with `{ids, merged_text, source}`
6. On success: toast, invalidate, reload list

Wire the merge action from bulk select bar to this modal (enabled when 2+ selected).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_web_ui.py::test_merge_modal -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webui/app.js tests/test_web_ui.py
git commit -m "feat: add merge memories modal with comparison panel"
```

---

### Task 8: Item 11 — Extraction Trigger from UI

**Files:**
- Modify: `webui/app.js` (memories + extractions pages)
- Modify: `webui/styles.css`
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_extraction_trigger_modal(client):
    """Extract button should open modal with dry-run flow."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "showExtractionModal" in js or "extractionModal" in js
    assert "actionBadge" in js
    assert "approvalToggle" in js
    assert "/memory/extract/commit" in js


def test_extraction_result_styles(client):
    """CSS should include extraction result list styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".action-badge" in css
    assert ".approval-toggle" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_ui.py -v -k "extraction_trigger or extraction_result"`
Expected: FAIL

- [ ] **Step 3: Add Extract button to topbar**

Add an "Extract" button (secondary style) next to "+ Create" in the memories page topbar. Also add to extractions page topbar.

- [ ] **Step 4: Add extraction modal**

```javascript
function showExtractionModal() {
  showModal((content) => {
    content.appendChild(h("h3", { className: "modal-title" }, "Extract Memories"));
    // Textarea for pasting conversation text
    // Source input
    // Mode toggle: standard / aggressive / conservative
    // Dry-run checkbox (checked by default)
    // Submit button → POST /memory/extract with dry_run: true
    // Poll GET /memory/extract/{job_id} until completed
    // Render results list with actionBadge + approvalToggle per fact
    // NOOP facts at 50% opacity
    // Summary bar: "N approved · N rejected · N skipped"
    // "Approve All" / "Reject All" shortcuts
    // "Commit N Memories" → POST /memory/extract/commit with approved actions
  });
}
```

- [ ] **Step 5: Add extraction result CSS**

```css
.extract-results { display: flex; flex-direction: column; gap: 8px; max-height: 400px; overflow-y: auto; }
.extract-fact { display: flex; align-items: flex-start; gap: 8px; padding: 8px; border-radius: 4px; background: var(--color-bg-surface); }
.extract-fact--noop { opacity: 0.5; }
.extract-summary { display: flex; gap: 12px; padding: 8px 0; font-size: 0.8125rem; color: var(--color-text-muted); border-top: 1px solid var(--color-border); margin-top: 8px; }
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_web_ui.py -v -k "extraction"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add webui/app.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: add extraction trigger modal with dry-run and approval flow"
```

---

### Task 9: Item 14 — Lifecycle Panel

**Files:**
- Modify: `webui/app.js` (updateDetailPanel)
- Modify: `webui/styles.css`
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_lifecycle_tabbed_panel(client):
    """Detail panel should have tabbed layout: Overview | Lifecycle | Links."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "Overview" in js
    assert "Lifecycle" in js
    assert "timelineEvent" in js


def test_lifecycle_styles(client):
    """CSS should include tab and timeline styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".detail-tabs" in css or ".tab-" in css
    assert ".timeline-event" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_ui.py -v -k "lifecycle"`
Expected: FAIL

- [ ] **Step 3: Restructure detail panel with tabs**

In `updateDetailPanel()`, replace the flat layout with a tabbed layout:
- **Overview**: current detail content (text, meta, confidence)
- **Lifecycle**: origin block + confidence sparkline + audit timeline
- **Links**: existing linked memories section (moved from Overview)

```javascript
// Tab bar
const tabs = ["Overview", "Lifecycle", "Links"];
let activeTab = "Overview";

const tabBar = h("div", { className: "detail-tabs" },
  ...tabs.map(t => h("button", {
    className: `detail-tab${t === activeTab ? " active" : ""}`,
    onclick: () => { activeTab = t; renderTab(); },
  }, t))
);
```

- [ ] **Step 4: Implement Lifecycle tab**

```javascript
async function renderLifecycleTab(container) {
  // Origin block
  const origin = mem.metadata?.origin || "manual";
  // ... icon + method + created_at timestamp

  // Confidence section — static bar (not sparkline)
  // Spec mentions "mini sparkline showing decay curve" but historical
  // confidence data isn't available via API. Use confidenceBar for now;
  // sparkline deferred to when decay history endpoint exists.
  // ... confidenceBar(mem.confidence, "md")

  // History timeline from audit log
  const auditData = await api(`/audit?resource_id=${mem.id}&limit=20`);
  const events = auditData.entries || auditData || [];
  const timeline = h("div", { className: "lifecycle-timeline" });
  events.forEach(evt => {
    const color = {
      "memory.created": "success", "memory.updated": "info",
      "memory.linked": "info", "memory.pinned": "primary",
      "memory.archived": "warning", "conflict.detected": "error",
    }[evt.action] || "info";
    timeline.appendChild(timelineEvent({
      color, title: evt.action, detail: evt.key_name, timestamp: evt.ts,
    }));
  });
  container.appendChild(timeline);
}
```

- [ ] **Step 5: Add tab CSS**

```css
.detail-tabs { display: flex; border-bottom: 1px solid var(--color-border); margin-bottom: 16px; }
.detail-tab { background: transparent; border: none; padding: 8px 16px; color: var(--color-text-muted); cursor: pointer; font-size: 0.8125rem; border-bottom: 2px solid transparent; }
.detail-tab.active { color: var(--color-primary); border-bottom-color: var(--color-primary); }
.detail-tab:hover { color: var(--color-text); }
.lifecycle-timeline { padding: 8px 0; }
.lifecycle-origin { display: flex; align-items: center; gap: 8px; padding: 12px; background: var(--color-bg-surface); border-radius: 8px; margin-bottom: 16px; }
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_web_ui.py -v -k "lifecycle"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add webui/app.js webui/styles.css tests/test_web_ui.py
git commit -m "feat: add lifecycle panel with tabbed detail view and audit timeline"
```

---

### Task 10: Item 15 — Conflict Resolution

**Files:**
- Modify: `webui/app.js` (health page ~line 2244)
- Test: `tests/test_web_ui.py`

- [ ] **Step 1: Write failing tests**

```python
def test_conflict_resolution_modal(client):
    """Health page should have conflict resolution modal with soft archive."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "comparisonPanel" in js
    # Verify soft archive (PATCH archived) not hard delete
    assert "archived" in js


def test_conflict_resolution_options(client):
    """Conflict modal should offer Keep A, Keep B, Merge, Defer."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "Keep A" in js
    assert "Keep B" in js
    assert "Merge" in js or "merge" in js
    assert "Defer" in js or "defer" in js
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_ui.py -v -k "conflict_resolution"`
Expected: FAIL

- [ ] **Step 3: Migrate existing handlers from DELETE to soft archive**

In the health page's conflict rendering, replace the `DELETE` calls in Keep A / Keep B handlers with `PATCH {archived: true}`.

- [ ] **Step 4: Add conflict resolution modal**

Replace inline Keep A / Keep B / Dismiss buttons with a single "Resolve" button that opens a modal:

```javascript
function showConflictModal(conflict) {
  showModal((content) => {
    content.appendChild(h("h3", { className: "modal-title" }, "Resolve Conflict"));
    // Full text side-by-side via comparisonPanel
    content.appendChild(comparisonPanel({
      memA: conflict, memB: conflict.conflicting_memory,
      labelA: `Memory #${conflict.id}`, labelB: `Memory #${conflict.conflicting_memory.id}`,
    }));
    // Radio options: Keep A, Keep B, Merge, Defer
    // Keep A → PATCH archive B, clear conflicts_with on A
    // Keep B → PATCH archive A, clear conflicts_with on B
    // Merge → hideModal() then showMergeModal([A.id, B.id])
    // Defer → PATCH metadata_patch: {deferred: true} on both, hide from queue
  });
}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_web_ui.py -v -k "conflict"`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS (874 baseline + new B3 tests)

- [ ] **Step 7: Commit**

```bash
git add webui/app.js tests/test_web_ui.py
git commit -m "feat: add conflict resolution modal with soft archive migration"
```

---

## Post-Implementation Checklist

- [ ] All 874 baseline tests still pass
- [ ] All new B3 tests pass
- [ ] `components.js` exports all 7 functions
- [ ] `app.js` imports from `components.js`
- [ ] No hard DELETE calls remain in conflict resolution
- [ ] `GET /audit?resource_id=X` works
- [ ] showToast supports action buttons
- [ ] Visual validation with ui-val skill on memories, health pages
