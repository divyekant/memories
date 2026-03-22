/* ==========================================================================
   Memories Web UI — Shared Utilities
   Imported by both app.js and components.js (avoids circular imports)
   ========================================================================== */

/**
 * Minimal DOM element factory.
 */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, val] of Object.entries(attrs)) {
      if (key === "className") {
        el.className = val;
      } else if (key === "style" && typeof val === "object") {
        Object.assign(el.style, val);
      } else if (key.startsWith("on") && typeof val === "function") {
        el.addEventListener(key.slice(2).toLowerCase(), val);
      } else if (key === "htmlFor") {
        el.setAttribute("for", val);
      } else if (key === "dataset" && typeof val === "object") {
        for (const [dk, dv] of Object.entries(val)) {
          el.dataset[dk] = dv;
        }
      } else {
        el.setAttribute(key, val);
      }
    }
  }
  for (const child of children) {
    if (child == null) continue;
    if (typeof child === "string" || typeof child === "number") {
      el.appendChild(document.createTextNode(String(child)));
    } else if (child instanceof Node) {
      el.appendChild(child);
    }
  }
  return el;
}

/**
 * Format large numbers compactly: 1234 -> "1.2K", 1500000 -> "1.5M"
 */
export function formatNumber(n) {
  if (n == null) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}

/**
 * Human-friendly relative time: "5m ago", "2h ago", "3d ago"
 */
export function timeAgo(dateStr) {
  if (!dateStr) return "";
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = Math.max(0, now - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

/**
 * Escape a string for safe insertion as innerHTML.
 */
export function escHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

/**
 * Return the semantic color class suffix for a confidence/similarity value (0-1).
 */
export function confidenceColor(value) {
  if (value > 0.7) return "success";
  if (value >= 0.4) return "warning";
  return "error";
}

/**
 * Build a confidence bar DOM element.
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
