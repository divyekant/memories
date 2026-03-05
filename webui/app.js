/* ==========================================================================
   Memories Web UI v2 — App Module
   ========================================================================== */

// -- Config & State --------------------------------------------------------

const API_KEY_STORAGE = "memories-api-key";
const THEME_STORAGE = "memories-theme";

const state = {
  apiKey: sessionStorage.getItem(API_KEY_STORAGE) || "",
  currentPage: null,
};

// -- API Helper ------------------------------------------------------------

export function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (state.apiKey) {
    headers["X-API-Key"] = state.apiKey;
  }
  return headers;
}

export async function api(path, options = {}) {
  const defaults = {
    headers: authHeaders(),
  };
  const merged = {
    ...defaults,
    ...options,
    headers: { ...defaults.headers, ...(options.headers || {}) },
  };
  const resp = await fetch(path, merged);
  if (!resp.ok) {
    let detail;
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      try {
        const data = await resp.json();
        detail = typeof data.detail === "string"
          ? data.detail
          : data.detail?.message || JSON.stringify(data);
      } catch {
        detail = `HTTP ${resp.status}`;
      }
    } else {
      detail = await resp.text();
    }
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return resp.json();
  }
  return resp.text();
}

// -- State Accessors -------------------------------------------------------

export function getState() {
  return state;
}

export function setApiKey(key) {
  state.apiKey = key;
  sessionStorage.setItem(API_KEY_STORAGE, key);

  // Sync the topbar dropdown if it exists
  const sel = document.getElementById("apiKeyFilter");
  if (sel) sel.value = key;
}

// -- Theme System ----------------------------------------------------------

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme) {
  if (theme === "system") {
    document.documentElement.setAttribute("data-theme", getSystemTheme());
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
}

function syncThemeButtons(choice) {
  document.querySelectorAll(".theme-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeChoice === choice);
  });
}

function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE) || "system";
  applyTheme(saved);
  syncThemeButtons(saved);

  // Bind theme buttons
  document.querySelectorAll(".theme-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const choice = btn.dataset.themeChoice;
      localStorage.setItem(THEME_STORAGE, choice);
      applyTheme(choice);
      syncThemeButtons(choice);
    });
  });

  // Listen for OS theme changes (applies when preference is "system")
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    const current = localStorage.getItem(THEME_STORAGE) || "system";
    if (current === "system") {
      applyTheme("system");
    }
  });
}

// -- DOM Helpers -----------------------------------------------------------

/**
 * Create a DOM element with attributes and children.
 * @param {string} tag - HTML tag name
 * @param {Object|null} attrs - Attributes, className, event handlers (on...)
 * @param {...(string|Node)} children - Text or child nodes
 * @returns {HTMLElement}
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

// -- Router ----------------------------------------------------------------

const pages = {};

export function registerPage(name, renderFn) {
  pages[name] = renderFn;
}

function navigate(page) {
  const contentEl = document.getElementById("content");
  const titleEl = document.getElementById("pageTitle");
  if (!contentEl) return;

  // Clean up old content
  contentEl.innerHTML = "";

  // Update nav active state
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === page);
  });

  // Update page title
  const titles = {
    dashboard: "Dashboard",
    memories: "Memories",
    extractions: "Extractions",
    keys: "API Keys",
    settings: "Settings",
  };
  if (titleEl) {
    titleEl.textContent = titles[page] || page;
  }

  state.currentPage = page;

  // Call the registered renderer, or show placeholder
  if (pages[page]) {
    pages[page](contentEl);
  } else {
    contentEl.appendChild(
      h("div", { className: "empty-state" },
        h("div", { className: "empty-state-icon" }, "\u25C6"),
        h("div", { className: "empty-state-title" }, titles[page] || page),
        h("div", { className: "empty-state-text" }, "Coming soon.")
      )
    );
  }
}

function parseHash() {
  const hash = window.location.hash || "#/dashboard";
  const page = hash.replace("#/", "").split("?")[0].split("/")[0];
  return page || "dashboard";
}

function initRouter() {
  window.addEventListener("hashchange", () => {
    navigate(parseHash());
  });

  // Initial route
  navigate(parseHash());
}

// -- Toast Notifications ---------------------------------------------------

/**
 * Show a toast notification.
 * @param {string} message - Toast text
 * @param {"success"|"error"|"warning"|"info"} [type="info"] - Toast type
 */
export function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toast = h("div", { className: `toast toast-${type}` }, message);
  container.appendChild(toast);

  // Auto-dismiss after 4 seconds
  setTimeout(() => {
    toast.classList.add("toast-exit");
    toast.addEventListener("animationend", () => {
      toast.remove();
    });
  }, 4000);
}

// -- Modal Helper ----------------------------------------------------------

export function showModal(renderFn) {
  const overlay = document.getElementById("modalOverlay");
  const content = document.getElementById("modalContent");
  if (!overlay || !content) return;

  content.innerHTML = "";
  renderFn(content);
  overlay.hidden = false;

  // Close on overlay click
  const closeHandler = (e) => {
    if (e.target === overlay) {
      hideModal();
      overlay.removeEventListener("click", closeHandler);
    }
  };
  overlay.addEventListener("click", closeHandler);
}

export function hideModal() {
  const overlay = document.getElementById("modalOverlay");
  if (overlay) overlay.hidden = true;
}

// -- Boot ------------------------------------------------------------------

initTheme();
initRouter();
