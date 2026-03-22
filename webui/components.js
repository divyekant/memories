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
        const newVal = input.value;
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
