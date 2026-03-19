/* ==========================================================================
   Memories Web UI v2 — App Module
   ========================================================================== */

// -- Config & State --------------------------------------------------------

const API_KEY_STORAGE = "memories-api-key";
const THEME_STORAGE = "memories-theme";

const state = {
  apiKey: localStorage.getItem(API_KEY_STORAGE) || "",
  currentPage: null,
};

// Page-scoped callbacks (set by active page, used by global search)
const pageCallbacks = { search: null, reset: null };

// -- API Helper ------------------------------------------------------------

export function authHeaders(hasBody = false) {
  const headers = {};
  if (hasBody) headers["Content-Type"] = "application/json";
  if (state.apiKey) headers["X-API-Key"] = state.apiKey;
  return headers;
}

// Simple response cache (30s TTL) to avoid re-fetching on page navigation
const _apiCache = new Map();
const CACHE_TTL = 30_000;

function _cacheKey(path, options) {
  if (options?.method && options.method !== "GET") return null;
  if (options?.body) return null;
  return path;
}

export async function api(path, options = {}) {
  const defaults = {
    headers: authHeaders(!!options.body),
  };
  const merged = {
    ...defaults,
    ...options,
    headers: { ...defaults.headers, ...(options.headers || {}) },
  };

  // Check cache for GET requests
  const ck = _cacheKey(path, options);
  if (ck && _apiCache.has(ck)) {
    const cached = _apiCache.get(ck);
    if (Date.now() - cached.ts < CACHE_TTL) return cached.data;
    _apiCache.delete(ck);
  }

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
  let result;
  if (ct.includes("application/json")) {
    result = await resp.json();
  } else {
    result = await resp.text();
  }

  // Cache GET responses
  if (ck) _apiCache.set(ck, { data: result, ts: Date.now() });

  return result;
}

// Bust cache for a path (after mutations)
export function invalidateCache(path) {
  if (path) _apiCache.delete(path);
  else _apiCache.clear();
}

// -- State Accessors -------------------------------------------------------

export function getState() {
  return state;
}

export function setApiKey(key) {
  state.apiKey = key;
  if (key) {
    localStorage.setItem(API_KEY_STORAGE, key);
  } else {
    localStorage.removeItem(API_KEY_STORAGE);
  }
  invalidateCache();
  syncKeyStatus();
}

function syncKeyStatus() {
  const el = document.getElementById("apiKeyStatus");
  if (!el) return;
  if (state.apiKey) {
    const masked = state.apiKey.length > 8
      ? state.apiKey.slice(0, 4) + "..." + state.apiKey.slice(-4)
      : "****";
    el.textContent = masked;
    el.classList.add("key-active");
    el.classList.remove("key-inactive");
  } else {
    el.textContent = "No key";
    el.classList.add("key-inactive");
    el.classList.remove("key-active");
  }
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
    health: "Health",
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

// ==========================================================================
//  Dashboard Page
// ==========================================================================

registerPage("dashboard", async (container) => {
  container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';

  try {
    const [stats, usage, extractStatus] = await Promise.all([
      api("/stats"),
      api("/usage?period=7d"),
      api("/extract/status"),
    ]);

    // Compute operations total from usage.operations
    let opsTotal = 0;
    if (usage.operations) {
      for (const op of Object.values(usage.operations)) {
        opsTotal += op.total || 0;
      }
    }

    const extractionCalls = usage.extraction?.total_calls ?? 0;
    const statusLabel = extractStatus.enabled ? "Active" : "Inactive";
    const statusBadgeClass = extractStatus.enabled ? "badge-success" : "badge-warning";

    container.innerHTML = "";

    // Section: Stat cards
    const statSection = h("div", { className: "section-title" }, "Overview");
    const statGrid = h("div", { className: "stat-grid" });

    statGrid.innerHTML = `
      <div class="stat-card">
        <div class="stat-value">${escHtml(formatNumber(stats.total_memories))}</div>
        <span class="stat-label">Total Memories</span>
      </div>
      <div class="stat-card">
        <div class="stat-value">${escHtml(formatNumber(extractionCalls))}</div>
        <span class="stat-label">Extractions 7d</span>
      </div>
      <div class="stat-card">
        <div class="stat-value">${escHtml(formatNumber(opsTotal))}</div>
        <span class="stat-label">Operations 7d</span>
      </div>
      <div class="stat-card">
        <div class="stat-value"><span class="badge ${statusBadgeClass}">${escHtml(statusLabel)}</span></div>
        <span class="stat-label">Extraction Status</span>
      </div>
    `;

    container.appendChild(statSection);
    container.appendChild(statGrid);

    // Section: Server Info
    const infoSection = h("div", { className: "section-title mt-24" }, "Server Info");
    const infoGrid = h("div", { className: "info-grid" });

    const indexKB = stats.index_size_bytes != null
      ? (stats.index_size_bytes / 1024).toFixed(1) + " KB"
      : "N/A";

    infoGrid.innerHTML = `
      <div class="info-item">
        <div class="info-item-label">Embedder Model</div>
        <div class="info-item-value">${escHtml(stats.model || "N/A")}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Dimensions</div>
        <div class="info-item-value">${escHtml(String(stats.dimension ?? "N/A"))}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Index Size</div>
        <div class="info-item-value">${escHtml(indexKB)}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Backups</div>
        <div class="info-item-value">${escHtml(String(stats.backup_count ?? 0))}</div>
      </div>
    `;

    container.appendChild(infoSection);
    container.appendChild(infoGrid);

    // Section: Usage Analytics
    const usageSection = h("div", { className: "section-title mt-24" }, "Usage Analytics");
    container.appendChild(usageSection);

    // Period selector
    const periodBar = h("div", { className: "filter-bar mb-16" });
    const periodSelect = h("select", { className: "period-select" });
    ["today", "7d", "30d", "all"].forEach((p) => {
      const opt = h("option", { value: p }, p === "today" ? "Today" : p === "all" ? "All Time" : `Last ${p}`);
      if (p === "7d") opt.selected = true;
      periodSelect.appendChild(opt);
    });
    periodBar.appendChild(periodSelect);
    container.appendChild(periodBar);

    const usageContainer = h("div", { id: "dashboardUsage" });
    container.appendChild(usageContainer);

    async function loadUsage(period) {
      usageContainer.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';
      try {
        const usage = await api(`/usage?period=${encodeURIComponent(period)}`);
        usageContainer.innerHTML = "";

        // Usage stat cards
        const uStatGrid = h("div", { className: "stat-grid mb-16" });
        const ops = usage.operations || {};
        const totalOps = Object.values(ops).reduce((sum, o) => sum + (o.total || 0), 0);
        const addOps = ops.add?.total || 0;
        const searchOps = ops.search?.total || 0;
        const deleteOps = ops.delete?.total || 0;
        const extractOps = ops.extract?.total || 0;
        const estCost = usage.extraction?.estimated_cost_usd;

        uStatGrid.innerHTML = `
          <div class="stat-card">
            <div class="stat-value">${escHtml(formatNumber(totalOps))}</div>
            <span class="stat-label">Total Operations</span>
          </div>
          <div class="stat-card">
            <div class="stat-value">${escHtml(formatNumber(addOps))}</div>
            <span class="stat-label">Adds</span>
          </div>
          <div class="stat-card">
            <div class="stat-value">${escHtml(formatNumber(searchOps))}</div>
            <span class="stat-label">Searches</span>
          </div>
          <div class="stat-card">
            <div class="stat-value">${estCost != null ? "$" + escHtml(estCost.toFixed(2)) : "\u2014"}</div>
            <span class="stat-label">Est. Cost</span>
          </div>
        `;
        usageContainer.appendChild(uStatGrid);

        // Operations breakdown table
        const opTypes = Object.keys(ops).filter((k) => ops[k].total > 0);
        if (opTypes.length > 0) {
          const opTitle = h("div", { className: "text-muted mb-8", style: { fontSize: "0.84rem", fontWeight: "600" } }, "Operations Breakdown");
          const opTableWrap = h("div", { className: "table-wrap mb-16" });
          let opHtml = `<table class="data-table"><thead><tr><th>Operation</th><th>Total</th><th>Top Sources</th></tr></thead><tbody>`;
          for (const opType of opTypes) {
            const opData = ops[opType];
            const bySrc = opData.by_source || {};
            const topSources = Object.entries(bySrc)
              .filter(([s]) => s !== "(unknown)")
              .sort((a, b) => b[1] - a[1])
              .slice(0, 3)
              .map(([s, c]) => `<span class="badge badge-info">${escHtml(s)}</span> ${escHtml(formatNumber(c))}`);
            const srcHtml = topSources.length > 0 ? topSources.join(", ") : '<span class="text-muted">—</span>';
            opHtml += `<tr><td class="font-mono">${escHtml(opType)}</td><td>${escHtml(formatNumber(opData.total))}</td><td>${srcHtml}</td></tr>`;
          }
          opHtml += `</tbody></table>`;
          opTableWrap.innerHTML = opHtml;
          usageContainer.appendChild(opTitle);
          usageContainer.appendChild(opTableWrap);
        }

        // Extraction token usage
        const ext = usage.extraction;
        if (ext && ext.total_calls > 0) {
          const extTitle = h("div", { className: "text-muted mb-8", style: { fontSize: "0.84rem", fontWeight: "600" } }, "Extraction Tokens");
          const extTableWrap = h("div", { className: "table-wrap" });
          let extHtml = `<table class="data-table"><thead><tr><th>Model</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th><th>Est. Cost</th></tr></thead><tbody>`;
          for (const [model, data] of Object.entries(ext.by_model || {})) {
            const modelCost = ((data.input_tokens || 0) * 0.001 + (data.output_tokens || 0) * 0.005) / 1000;
            extHtml += `<tr><td class="font-mono">${escHtml(model)}</td><td>${escHtml(formatNumber(data.calls || 0))}</td><td>${escHtml(formatNumber(data.input_tokens || 0))}</td><td>${escHtml(formatNumber(data.output_tokens || 0))}</td><td>${escHtml("$" + modelCost.toFixed(2))}</td></tr>`;
          }
          if (ext.estimated_cost_usd != null) {
            extHtml += `<tr style="font-weight:600;"><td>Total</td><td>${escHtml(formatNumber(ext.total_calls))}</td><td>${escHtml(formatNumber(ext.total_input_tokens || 0))}</td><td>${escHtml(formatNumber(ext.total_output_tokens || 0))}</td><td>${escHtml("$" + ext.estimated_cost_usd.toFixed(2))}</td></tr>`;
          }
          extHtml += `</tbody></table>`;
          extTableWrap.innerHTML = extHtml;
          usageContainer.appendChild(extTitle);
          usageContainer.appendChild(extTableWrap);
        }
      } catch (err) {
        usageContainer.innerHTML = "";
        usageContainer.appendChild(
          h("div", { className: "empty-state", style: { border: "none", padding: "24px" } },
            h("div", { className: "empty-state-icon" }, "\u26A0"),
            h("div", { className: "empty-state-text" }, "Failed to load usage: " + escHtml(err.message))
          )
        );
      }
    }

    periodSelect.addEventListener("change", () => loadUsage(periodSelect.value));
    await loadUsage("7d");
  } catch (err) {
    container.innerHTML = "";
    container.appendChild(
      h("div", { className: "empty-state" },
        h("div", { className: "empty-state-icon" }, "\u26A0"),
        h("div", { className: "empty-state-title" }, "Failed to load dashboard"),
        h("div", { className: "empty-state-text" }, escHtml(err.message))
      )
    );
    showToast(err.message, "error");
  }
});

// ==========================================================================
//  Memories Page
// ==========================================================================

registerPage("memories", async (container) => {
  const memState = { offset: 0, limit: 20, source: "", total: 0, selected: null, view: "list", searchQuery: "", searchResults: null, sourcePrefixes: [] };

  // -- Delete handler --
  async function deleteMemory(id) {
    if (!confirm(`Delete memory #${id}?`)) return;
    try {
      await api(`/memory/${id}`, { method: "DELETE" });
      invalidateCache();
      showToast("Memory deleted", "success");
      memState.selected = null;
      memState.offset = 0;
      await loadMemories();
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  // -- Build page skeleton --
  function buildSkeleton() {
    container.innerHTML = "";

    // Filter bar
    const filterBar = h("div", { className: "filter-bar mb-16" });

    const sourceSelect = h("select", { className: "source-select", id: "sourceSelect" });
    const allOpt = h("option", { value: "" }, "All sources");
    sourceSelect.appendChild(allOpt);
    // Populate dynamically after load
    memState.sourcePrefixes.forEach((prefix) => {
      const opt = h("option", { value: prefix }, prefix);
      if (prefix === memState.source) opt.selected = true;
      sourceSelect.appendChild(opt);
    });
    sourceSelect.addEventListener("change", (e) => {
      memState.source = e.target.value;
      memState.offset = 0;
      memState.searchQuery = "";
      memState.searchResults = null;
      const globalSearch = document.getElementById("globalSearch");
      if (globalSearch) globalSearch.value = "";
      loadMemories();
    });

    const viewToggle = h("div", { className: "view-toggle" });
    const listBtn = h("button", {
      className: `btn ${memState.view === "list" ? "active" : ""}`,
      onClick: () => { memState.view = "list"; renderContent(); },
    }, "List");
    const gridBtn = h("button", {
      className: `btn ${memState.view === "grid" ? "active" : ""}`,
      onClick: () => { memState.view = "grid"; renderContent(); },
    }, "Grid");
    viewToggle.appendChild(listBtn);
    viewToggle.appendChild(gridBtn);

    filterBar.appendChild(sourceSelect);
    filterBar.appendChild(viewToggle);
    container.appendChild(filterBar);

    // Content container
    const contentDiv = h("div", { id: "memoriesContent" });
    container.appendChild(contentDiv);

    // Pagination
    const pag = h("div", { className: "pagination", id: "memoriesPagination" });
    container.appendChild(pag);
  }

  // -- Render content based on current view mode --
  function renderContent() {
    const contentDiv = document.getElementById("memoriesContent");
    if (!contentDiv) return;
    contentDiv.innerHTML = "";

    // Update view toggle button active states
    container.querySelectorAll(".view-toggle .btn").forEach((btn) => {
      btn.classList.toggle("active", btn.textContent.toLowerCase() === memState.view);
    });

    const memories = memState.searchResults || memState.memories || [];

    if (memories.length === 0) {
      contentDiv.appendChild(
        h("div", { className: "empty-state" },
          h("div", { className: "empty-state-icon" }, "\u25C6"),
          h("div", { className: "empty-state-title" }, memState.searchQuery ? "No search results" : "No memories found"),
          h("div", { className: "empty-state-text" }, memState.searchQuery ? "Try a different search query." : "Memories will appear here once added.")
        )
      );
      renderPagination();
      return;
    }

    if (memState.view === "grid") {
      renderGridView(contentDiv, memories);
    } else {
      renderListView(contentDiv, memories);
    }

    renderPagination();
  }

  // -- List view (split layout) --
  function renderListView(contentDiv, memories) {
    const layout = h("div", { className: "memories-layout" });

    // Left: list panel
    const listPanel = h("div", { className: "memories-list-panel" });
    // Global tooltip ref — only one tooltip visible at a time
    let globalTooltip = null;
    function clearGlobalTooltip() {
      if (globalTooltip) { globalTooltip.remove(); globalTooltip = null; }
    }
    memories.forEach((mem) => {
      const isActive = memState.selected && memState.selected.id === mem.id;
      const item = document.createElement("div");
      item.className = `memory-item${isActive ? " active" : ""}`;
      item.dataset.memId = mem.id;

      // Color-coded left border for search results
      if (mem.rrf_score != null) {
        const borderColor = confidenceColor(mem.rrf_score);
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

      if (mem.rrf_score != null) {
        const pct = (mem.rrf_score * 100).toFixed(1);
        const color = confidenceColor(mem.rrf_score);
        const scoreBadge = h("span", {
          className: `search-result-score`,
          style: { cursor: "help" },
        }, `${pct}%`);

        // Score tooltip on hover (lazy-load explain data, fixed-position to body)
        let explainLoaded = false;

        function positionTooltip(tooltip) {
          const rect = scoreBadge.getBoundingClientRect();
          tooltip.style.top = `${rect.bottom + 6}px`;
          tooltip.style.left = `${rect.left + rect.width / 2}px`;
          tooltip.style.transform = "translateX(-50%)";
        }

        scoreBadge.addEventListener("mouseenter", async () => {
          clearGlobalTooltip();
          if (explainLoaded) return;
          try {
            const explainData = await api("/search/explain", {
              method: "POST",
              body: JSON.stringify({ query: memState.searchQuery, k: 20, hybrid: true }),
            });
            explainLoaded = true;
            const vectorCandidates = explainData.explain?.vector_candidates || [];
            const bm25Candidates = explainData.explain?.bm25_candidates || [];
            const vectorMatch = vectorCandidates.find((c) => c.id === mem.id);
            const bm25Match = bm25Candidates.find((c) => c.id === mem.id);
            globalTooltip = h("div", { className: "score-tooltip" },
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
                  h("div", { className: "score-tooltip-item-value" }, mem.rrf_score.toFixed(4))
                ),
              )
            );
            document.body.appendChild(globalTooltip);
            positionTooltip(globalTooltip);
          } catch {
            globalTooltip = h("div", { className: "score-tooltip" },
              h("div", { className: "score-tooltip-label" }, "Score details require admin access")
            );
            document.body.appendChild(globalTooltip);
            positionTooltip(globalTooltip);
            explainLoaded = true;
          }
        });
        scoreBadge.addEventListener("mouseleave", () => {
          clearGlobalTooltip();
          explainLoaded = false;
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
      item.appendChild(headerEl);

      if (mem.rrf_score != null) {
        // Search result: text + feedback buttons in a flex row
        const textRow = h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start" } },
          h("div", { className: "memory-item-text", style: { flex: "1" } })
        );
        textRow.firstChild.innerHTML = truncText;

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
        item.appendChild(textRow);
      } else {
        // Browse mode: preserve original card structure
        const textDiv = h("div", { className: "memory-item-text" });
        textDiv.innerHTML = truncText;
        item.appendChild(textDiv);
      }

      item.addEventListener("click", () => {
        memState.selected = mem;
        listPanel.querySelectorAll(".memory-item").forEach((el) => {
          el.classList.toggle("active", el.dataset.memId === String(mem.id));
        });
        updateDetailPanel();
      });
      listPanel.appendChild(item);
    });

    // Right: detail panel
    const detailPanel = h("div", { className: "memories-detail-panel", id: "memoryDetailPanel" });

    layout.appendChild(listPanel);
    layout.appendChild(detailPanel);
    contentDiv.appendChild(layout);

    updateDetailPanel();
  }

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

  // -- Grid view --
  function renderGridView(contentDiv, memories) {
    const grid = h("div", { className: "memory-grid-view" });
    memories.forEach((mem) => {
      const truncText = (mem.text || "").length > 200
        ? escHtml((mem.text || "").slice(0, 200)) + "..."
        : escHtml(mem.text || "");

      let scoreHtml = "";
      if (mem.rrf_score != null) {
        scoreHtml = ` <span class="search-result-score">${escHtml(String((mem.rrf_score * 100).toFixed(1)))}%</span>`;
      }

      const card = document.createElement("div");
      card.className = "memory-card";
      card.innerHTML = `
        <div class="memory-item-header">
          <span class="memory-item-source">${escHtml(mem.source || "")}</span>
          <span class="memory-item-id">#${escHtml(String(mem.id))}${scoreHtml}</span>
        </div>
        <div class="memory-item-text mt-8">${truncText}</div>
      `;
      grid.appendChild(card);
    });
    contentDiv.appendChild(grid);
  }

  // -- Pagination with jump-to-page --
  function renderPagination() {
    const pag = document.getElementById("memoriesPagination");
    if (!pag) return;
    pag.innerHTML = "";

    // Don't show pagination for search results
    if (memState.searchResults) {
      const info = h("span", { className: "pagination-info" },
        `${memState.searchResults.length} search result${memState.searchResults.length !== 1 ? "s" : ""}`
      );
      pag.appendChild(info);
      return;
    }

    const totalPages = Math.max(1, Math.ceil(memState.total / memState.limit));
    const currentPage = Math.floor(memState.offset / memState.limit) + 1;
    const start = memState.total > 0 ? memState.offset + 1 : 0;
    const end = Math.min(memState.offset + memState.limit, memState.total);

    const info = h("span", { className: "pagination-info" },
      `${start}\u2013${end} of ${memState.total}`
    );

    const controls = h("div", { className: "pagination-controls" });

    const prevBtn = h("button", {
      className: "btn btn-sm",
      onClick: () => {
        memState.offset = Math.max(0, memState.offset - memState.limit);
        loadMemories();
      },
    }, "\u2190");
    if (memState.offset === 0) prevBtn.disabled = true;

    // Jump-to-page input
    const jumpWrap = h("div", { className: "pagination-jump" });
    const jumpLabel = h("span", { className: "text-muted", style: { fontSize: "0.78rem" } }, "Page");
    const jumpInput = h("input", {
      type: "number",
      className: "page-jump-input",
      value: currentPage,
      min: 1,
      max: totalPages,
    });
    const jumpTotal = h("span", { className: "text-muted", style: { fontSize: "0.78rem" } }, `of ${totalPages}`);

    jumpInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const page = parseInt(jumpInput.value, 10);
      if (isNaN(page) || page < 1 || page > totalPages) {
        jumpInput.value = currentPage;
        return;
      }
      memState.offset = (page - 1) * memState.limit;
      loadMemories();
    });
    jumpInput.addEventListener("blur", () => {
      const page = parseInt(jumpInput.value, 10);
      if (isNaN(page) || page < 1 || page > totalPages) {
        jumpInput.value = currentPage;
        return;
      }
      if (page !== currentPage) {
        memState.offset = (page - 1) * memState.limit;
        loadMemories();
      }
    });

    jumpWrap.appendChild(jumpLabel);
    jumpWrap.appendChild(jumpInput);
    jumpWrap.appendChild(jumpTotal);

    const nextBtn = h("button", {
      className: "btn btn-sm",
      onClick: () => {
        memState.offset += memState.limit;
        loadMemories();
      },
    }, "\u2192");
    if (memState.offset + memState.limit >= memState.total) nextBtn.disabled = true;

    // Page size selector
    const sizeWrap = h("div", { className: "pagination-jump" });
    const sizeLabel = h("span", { className: "text-muted", style: { fontSize: "0.78rem" } }, "Per page");
    const sizeSelect = h("select", { className: "page-size-select" });
    [20, 50, 100, 200].forEach((n) => {
      const opt = h("option", { value: n }, String(n));
      if (n === memState.limit) opt.selected = true;
      sizeSelect.appendChild(opt);
    });
    sizeSelect.addEventListener("change", () => {
      memState.limit = parseInt(sizeSelect.value, 10);
      memState.offset = 0;
      loadMemories();
    });
    sizeWrap.appendChild(sizeLabel);
    sizeWrap.appendChild(sizeSelect);

    controls.appendChild(prevBtn);
    controls.appendChild(jumpWrap);
    controls.appendChild(nextBtn);
    controls.appendChild(sizeWrap);

    pag.appendChild(info);
    pag.appendChild(controls);
  }

  // -- Load memories from API --
  async function loadMemories() {
    const contentDiv = document.getElementById("memoriesContent");
    if (contentDiv) {
      contentDiv.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';
    }

    try {
      let url = `/memories?offset=${memState.offset}&limit=${memState.limit}`;
      if (memState.source) {
        url += `&source=${encodeURIComponent(memState.source)}`;
      }
      const data = await api(url);
      memState.memories = data.memories || [];
      memState.total = data.total || 0;
      memState.searchResults = null;
      memState.searchQuery = "";
      renderContent();
    } catch (err) {
      const contentDiv = document.getElementById("memoriesContent");
      if (contentDiv) {
        contentDiv.innerHTML = "";
        contentDiv.appendChild(
          h("div", { className: "empty-state" },
            h("div", { className: "empty-state-icon" }, "\u26A0"),
            h("div", { className: "empty-state-title" }, "Failed to load memories"),
            h("div", { className: "empty-state-text" }, escHtml(err.message))
          )
        );
      }
      showToast(err.message, "error");
    }
  }

  // -- Search memories --
  async function searchMemories(query) {
    const contentDiv = document.getElementById("memoriesContent");
    if (contentDiv) {
      contentDiv.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';
    }

    try {
      const data = await api("/search", {
        method: "POST",
        body: JSON.stringify({ query, k: 20, hybrid: true }),
      });
      memState.searchQuery = query;
      memState.searchResults = data.results || [];
      memState.selected = null;
      renderContent();
    } catch (err) {
      const contentDiv = document.getElementById("memoriesContent");
      if (contentDiv) {
        contentDiv.innerHTML = "";
        contentDiv.appendChild(
          h("div", { className: "empty-state" },
            h("div", { className: "empty-state-icon" }, "\u26A0"),
            h("div", { className: "empty-state-title" }, "Search failed"),
            h("div", { className: "empty-state-text" }, escHtml(err.message))
          )
        );
      }
      showToast(err.message, "error");
    }
  }

  // -- Expose search trigger for global search integration --
  pageCallbacks.search = searchMemories;
  pageCallbacks.reset = () => {
    memState.searchQuery = "";
    memState.searchResults = null;
    memState.offset = 0;
    loadMemories();
  };

  // -- Fetch source prefixes for dropdown --
  async function loadSourcePrefixes() {
    try {
      const usage = await api("/usage?period=all");
      const prefixSet = new Set();
      for (const opData of Object.values(usage.operations || {})) {
        for (const src of Object.keys(opData.by_source || {})) {
          if (src === "(unknown)") continue;
          const prefix = src.includes("/") ? src.split("/")[0] : src;
          prefixSet.add(prefix);
        }
      }
      memState.sourcePrefixes = [...prefixSet].sort();
      // Update dropdown
      const sel = document.getElementById("sourceSelect");
      if (sel) {
        // Keep first "All sources" option, replace rest
        while (sel.options.length > 1) sel.remove(1);
        memState.sourcePrefixes.forEach((p) => {
          const opt = h("option", { value: p }, p);
          if (p === memState.source) opt.selected = true;
          sel.appendChild(opt);
        });
      }
    } catch { /* non-critical */ }
  }

  // -- Initial render --
  buildSkeleton();

  // Load prefixes in background
  loadSourcePrefixes();

  // If navigated here with a pending search query, run it
  const globalSearch = document.getElementById("globalSearch");
  const pendingQuery = globalSearch?.value?.trim();
  if (pendingQuery) {
    await searchMemories(pendingQuery);
  } else {
    await loadMemories();
  }
});

// ==========================================================================
//  Global Search Wiring
// ==========================================================================

(function initGlobalSearch() {
  const globalSearch = document.getElementById("globalSearch");
  if (!globalSearch) return;

  globalSearch.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const query = globalSearch.value.trim();

    if (!query) {
      // Reset to normal list if on memories page
      if (state.currentPage === "memories" && pageCallbacks.reset) {
        pageCallbacks.reset();
      }
      return;
    }

    if (state.currentPage === "memories" && pageCallbacks.search) {
      pageCallbacks.search(query);
    } else {
      // Navigate to memories page; the page will pick up the query from the input
      window.location.hash = "#/memories";
    }
  });

  // Handle clearing via the search clear button (type=search)
  globalSearch.addEventListener("search", () => {
    if (globalSearch.value === "" && state.currentPage === "memories" && pageCallbacks.reset) {
      pageCallbacks.reset();
    }
  });
})();

// ==========================================================================
//  Extractions Page
// ==========================================================================

registerPage("extractions", async (container) => {
  container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';

  try {
    const [extractStatus, usage] = await Promise.all([
      api("/extract/status"),
      api("/usage?period=7d"),
    ]);

    const totalCalls = usage.extraction?.total_calls ?? 0;
    const byModel = usage.extraction?.by_model || {};

    // Success rate: only show if error data is available, otherwise N/A
    const successRate = "\u2014";

    const statusLabel = extractStatus.enabled ? "Active" : "Inactive";
    const statusBadgeClass = extractStatus.enabled ? "badge-success" : "badge-warning";
    const providerLabel = extractStatus.provider || "N/A";

    container.innerHTML = "";

    // Section: Stat cards
    const statSection = h("div", { className: "section-title" }, "Overview");
    const statGrid = h("div", { className: "stat-grid" });

    statGrid.innerHTML = `
      <div class="stat-card">
        <div class="stat-value">${escHtml(formatNumber(totalCalls))}</div>
        <span class="stat-label">Jobs 7d</span>
      </div>
      <div class="stat-card">
        <div class="stat-value">${escHtml(successRate)}</div>
        <span class="stat-label">Success Rate</span>
      </div>
      <div class="stat-card">
        <div class="stat-value"><span class="badge ${statusBadgeClass}">${escHtml(statusLabel)}</span></div>
        <span class="stat-label">Status</span>
      </div>
      <div class="stat-card">
        <div class="stat-value" style="font-size:1.1rem;">${escHtml(providerLabel)}</div>
        <span class="stat-label">Provider</span>
      </div>
    `;

    container.appendChild(statSection);
    container.appendChild(statGrid);

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

    // Section: Configuration
    const configSection = h("div", { className: "section-title mt-24" }, "Configuration");
    const configGrid = h("div", { className: "info-grid" });

    configGrid.innerHTML = `
      <div class="info-item">
        <div class="info-item-label">Provider</div>
        <div class="info-item-value">${escHtml(extractStatus.provider || "N/A")}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Model</div>
        <div class="info-item-value">${escHtml(extractStatus.model || "N/A")}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Enabled</div>
        <div class="info-item-value"><span class="badge ${statusBadgeClass}">${escHtml(statusLabel)}</span></div>
      </div>
    `;

    container.appendChild(configSection);
    container.appendChild(configGrid);

    // Section: Token Usage by Model
    const modelKeys = Object.keys(byModel);
    if (modelKeys.length > 0) {
      const tableSection = h("div", { className: "section-title mt-24" }, "Token Usage by Model");
      const tableWrap = h("div", { className: "table-wrap" });

      let tableHtml = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Model</th>
              <th>Calls</th>
              <th>Input Tokens</th>
              <th>Output Tokens</th>
            </tr>
          </thead>
          <tbody>
      `;
      for (const [model, data] of Object.entries(byModel)) {
        tableHtml += `
            <tr>
              <td class="font-mono">${escHtml(model)}</td>
              <td>${escHtml(formatNumber(data.calls || 0))}</td>
              <td>${escHtml(formatNumber(data.input_tokens || 0))}</td>
              <td>${escHtml(formatNumber(data.output_tokens || 0))}</td>
            </tr>
        `;
      }
      tableHtml += `
          </tbody>
        </table>
      `;

      tableWrap.innerHTML = tableHtml;
      container.appendChild(tableSection);
      container.appendChild(tableWrap);
    }
  } catch (err) {
    container.innerHTML = "";
    container.appendChild(
      h("div", { className: "empty-state" },
        h("div", { className: "empty-state-icon" }, "\u26A0"),
        h("div", { className: "empty-state-title" }, "Failed to load extractions"),
        h("div", { className: "empty-state-text" }, escHtml(err.message))
      )
    );
    showToast(err.message, "error");
  }
});

// ==========================================================================
//  API Keys Page
// ==========================================================================

registerPage("keys", async (container) => {
  container.innerHTML = "";

  // Section: API Key Configuration
  const configSection = h("div", { className: "settings-section" });
  const configTitle = h("div", { className: "section-title" }, "API Key Configuration");

  const keyForm = document.createElement("div");
  keyForm.className = "key-form";

  const description = h("div", { className: "key-description mb-16" },
    "Configure the API key used to authenticate requests to the Memories server. " +
    "The key is stored in your browser session and sent as an X-API-Key header."
  );

  const inputRow = h("div", { className: "key-input-row" });
  const keyInput = h("input", {
    type: "password",
    placeholder: "Enter API key...",
    value: state.apiKey || "",
    style: { flex: "1" },
  });
  keyInput.className = "key-input";

  const saveBtn = h("button", { className: "btn btn-primary" }, "Save Key");
  const clearBtn = h("button", { className: "btn" }, "Clear");

  inputRow.appendChild(keyInput);
  inputRow.appendChild(saveBtn);
  inputRow.appendChild(clearBtn);

  const statusEl = h("div", { className: "key-status", id: "keyStatus" });

  // Show current status on load
  if (state.apiKey) {
    statusEl.innerHTML = `<span class="badge badge-info">Key configured</span>`;
  }

  // Save handler
  saveBtn.addEventListener("click", async () => {
    const val = keyInput.value.trim();
    if (!val) {
      statusEl.innerHTML = `<span class="badge badge-warning">Please enter a key</span>`;
      return;
    }

    setApiKey(val);

    statusEl.innerHTML = `<span class="text-muted" style="font-size:0.78rem;">Testing connection...</span>`;

    try {
      await api("/health");
      statusEl.innerHTML = `<span class="badge badge-success">Connected</span>`;
      showToast("API key saved and verified", "success");
    } catch (err) {
      statusEl.innerHTML = `<span class="badge badge-error">${escHtml(err.message)}</span>`;
      showToast("Key saved but connection test failed", "warning");
    }
  });

  // Clear handler
  clearBtn.addEventListener("click", () => {
    keyInput.value = "";
    setApiKey("");
    statusEl.innerHTML = `<span class="badge badge-warning">Key cleared</span>`;
    showToast("API key cleared", "info");
  });

  keyForm.appendChild(description);
  keyForm.appendChild(inputRow);
  keyForm.appendChild(statusEl);

  configSection.appendChild(configTitle);
  configSection.appendChild(keyForm);
  container.appendChild(configSection);

  // Section: Key Management (admin-gated)
  const mgmtSection = h("div", { className: "settings-section" });

  // Check caller's role
  let callerInfo = null;
  try {
    callerInfo = await api("/api/keys/me");
  } catch {
    // Not authenticated or endpoint unavailable
  }

  if (!callerInfo || callerInfo.role !== "admin") {
    const noAccess = h("div", { className: "empty-state" },
      h("div", { className: "empty-state-icon" }, "\u26B6"),
      h("div", { className: "empty-state-title" }, "Key Management"),
      h("div", { className: "empty-state-text" },
        "Key management requires admin access."
      )
    );
    mgmtSection.appendChild(noAccess);
    container.appendChild(mgmtSection);
    return;
  }

  // --- Admin: full key management UI ---

  const mgmtTitle = h("div", { className: "section-title" }, "Key Management");
  mgmtSection.appendChild(mgmtTitle);

  // Create Key button
  const createBtn = h("button", { className: "btn btn-primary mb-16" }, "+ Create Key");
  mgmtSection.appendChild(createBtn);

  // Key table container
  const tableWrap = h("div", { className: "table-wrap" });
  const keyTable = h("table", { className: "data-table key-table" });
  keyTable.innerHTML = `
    <thead>
      <tr>
        <th>Name</th>
        <th>Prefix</th>
        <th>Role</th>
        <th>Prefixes</th>
        <th>Created</th>
        <th>Last Used</th>
        <th>Usage</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;
  tableWrap.appendChild(keyTable);
  mgmtSection.appendChild(tableWrap);
  container.appendChild(mgmtSection);

  // Role badge helper
  function roleBadge(role) {
    const map = {
      "read-only": "badge-info",
      "read-write": "badge-success",
      "admin": "badge-warning",
    };
    return `<span class="badge ${map[role] || "badge-info"}">${escHtml(role)}</span>`;
  }

  // Render key rows
  async function loadKeys() {
    const tbody = keyTable.querySelector("tbody");
    tbody.innerHTML = '<tr><td colspan="8" class="loading-state"><div class="loading-spinner"></div></td></tr>';

    try {
      invalidateCache("/api/keys");
      const data = await api("/api/keys");
      const keys = data.keys || [];
      tbody.innerHTML = "";

      if (keys.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:24px;" class="text-muted">No managed keys yet. Click "Create Key" to get started.</td></tr>';
        return;
      }

      for (const k of keys) {
        const isRevoked = !!k.revoked;
        const tr = document.createElement("tr");
        if (isRevoked) tr.className = "key-row-revoked";

        const nameCell = isRevoked
          ? `<td><span class="key-name-revoked">${escHtml(k.name || "Unnamed")}</span></td>`
          : `<td>${escHtml(k.name || "Unnamed")}</td>`;

        const prefix = k.key_prefix
          ? `<code class="font-mono text-muted">${escHtml(k.key_prefix)}...</code>`
          : `<span class="text-faint">-</span>`;

        const prefixes = k.prefixes && k.prefixes.length > 0
          ? k.prefixes.map(p => `<span class="badge badge-primary">${escHtml(p)}</span>`).join(" ")
          : `<span class="text-faint">all</span>`;

        const created = k.created_at ? timeAgo(k.created_at) : "-";
        const lastUsed = k.last_used_at ? timeAgo(k.last_used_at) : "Never";
        const usage = k.usage_count != null ? formatNumber(k.usage_count) : "0";

        const statusBadge = isRevoked ? ` <span class="badge badge-error">Revoked</span>` : "";

        tr.innerHTML = `
          ${nameCell}
          <td>${prefix}</td>
          <td>${roleBadge(k.role)}${statusBadge}</td>
          <td>${prefixes}</td>
          <td title="${escHtml(k.created_at || "")}">${created}</td>
          <td title="${escHtml(k.last_used_at || "")}">${lastUsed}</td>
          <td>${usage}</td>
          <td class="key-actions-cell"></td>
        `;

        // Action buttons (only for non-revoked keys)
        const actionsCell = tr.querySelector(".key-actions-cell");
        if (!isRevoked) {
          const editBtn = h("button", {
            className: "btn btn-sm",
            title: "Edit key",
            onClick: () => openEditModal(k),
          }, "\u270E");

          const revokeBtn = h("button", {
            className: "btn btn-sm btn-danger",
            title: "Revoke key",
            onClick: () => openRevokeModal(k),
          }, "\u2715");

          actionsCell.appendChild(editBtn);
          actionsCell.appendChild(revokeBtn);
        }

        tbody.appendChild(tr);
      }
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-error" style="padding:16px;">${escHtml(err.message)}</td></tr>`;
    }
  }

  // Create Key modal
  createBtn.addEventListener("click", () => {
    showModal((content) => {
      content.appendChild(h("div", { className: "modal-title" }, "Create API Key"));

      const form = h("div", { className: "flex flex-col gap-16" });

      const nameLabel = h("label", { className: "text-muted", style: { fontSize: "0.78rem", fontWeight: "600" } }, "Name");
      const nameInput = h("input", {
        type: "text",
        placeholder: "e.g. CI Pipeline",
        className: "key-modal-input",
      });

      const roleLabel = h("label", { className: "text-muted", style: { fontSize: "0.78rem", fontWeight: "600" } }, "Role");
      const roleSelect = document.createElement("select");
      roleSelect.className = "key-modal-input";
      roleSelect.innerHTML = `
        <option value="read-only">read-only</option>
        <option value="read-write" selected>read-write</option>
        <option value="admin">admin</option>
      `;

      const prefixLabel = h("label", { className: "text-muted", style: { fontSize: "0.78rem", fontWeight: "600" } }, "Prefixes (comma-separated, leave empty for all)");
      const prefixInput = h("input", {
        type: "text",
        placeholder: "e.g. myapp/, shared/",
        className: "key-modal-input",
      });

      form.appendChild(nameLabel);
      form.appendChild(nameInput);
      form.appendChild(roleLabel);
      form.appendChild(roleSelect);
      form.appendChild(prefixLabel);
      form.appendChild(prefixInput);

      const actions = h("div", { className: "modal-actions" });
      const cancelBtn = h("button", { className: "btn", onClick: hideModal }, "Cancel");
      const submitBtn = h("button", { className: "btn btn-primary" }, "Create");

      actions.appendChild(cancelBtn);
      actions.appendChild(submitBtn);

      content.appendChild(form);
      content.appendChild(actions);

      submitBtn.addEventListener("click", async () => {
        const name = nameInput.value.trim();
        if (!name) {
          showToast("Name is required", "warning");
          return;
        }

        const role = roleSelect.value;
        const prefixesRaw = prefixInput.value.trim();
        const prefixes = prefixesRaw
          ? prefixesRaw.split(",").map(s => s.trim()).filter(Boolean)
          : [];

        submitBtn.disabled = true;
        submitBtn.textContent = "Creating...";

        try {
          const result = await api("/api/keys", {
            method: "POST",
            body: JSON.stringify({ name, role, prefixes }),
          });

          invalidateCache();

          // Show the created key (one-time display)
          content.innerHTML = "";
          content.appendChild(h("div", { className: "modal-title" }, "Key Created"));

          const warning = h("div", { className: "key-created-warning" },
            "This key will only be shown once. Copy it now."
          );
          content.appendChild(warning);

          const keyDisplay = h("div", { className: "key-created-display" });
          const keyCode = h("code", { className: "key-created-value" }, result.key);
          const copyBtn = h("button", { className: "btn btn-sm", title: "Copy to clipboard" }, "Copy");

          copyBtn.addEventListener("click", () => {
            navigator.clipboard.writeText(result.key).then(() => {
              copyBtn.textContent = "Copied!";
              showToast("Key copied to clipboard", "success");
              setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
            }).catch(() => {
              showToast("Failed to copy", "error");
            });
          });

          keyDisplay.appendChild(keyCode);
          keyDisplay.appendChild(copyBtn);
          content.appendChild(keyDisplay);

          const meta = h("div", { className: "mt-16", style: { fontSize: "0.82rem" } });
          meta.innerHTML = `
            <div class="text-muted mb-8"><strong>Name:</strong> ${escHtml(result.name)}</div>
            <div class="text-muted mb-8"><strong>Role:</strong> ${roleBadge(result.role)}</div>
            <div class="text-muted mb-8"><strong>Prefix:</strong> <code class="font-mono">${escHtml(result.key_prefix)}...</code></div>
          `;
          content.appendChild(meta);

          const doneBtn = h("button", { className: "btn btn-primary mt-16", onClick: () => { hideModal(); loadKeys(); } }, "Done");
          content.appendChild(h("div", { className: "modal-actions" }, doneBtn));

        } catch (err) {
          showToast(err.message, "error");
          submitBtn.disabled = false;
          submitBtn.textContent = "Create";
        }
      });
    });
  });

  // Edit Key modal
  function openEditModal(key) {
    showModal((content) => {
      content.appendChild(h("div", { className: "modal-title" }, `Edit Key: ${key.name || "Unnamed"}`));

      const form = h("div", { className: "flex flex-col gap-16" });

      const nameLabel = h("label", { className: "text-muted", style: { fontSize: "0.78rem", fontWeight: "600" } }, "Name");
      const nameInput = h("input", {
        type: "text",
        value: key.name || "",
        className: "key-modal-input",
      });

      const roleLabel = h("label", { className: "text-muted", style: { fontSize: "0.78rem", fontWeight: "600" } }, "Role");
      const roleSelect = document.createElement("select");
      roleSelect.className = "key-modal-input";
      roleSelect.innerHTML = `
        <option value="read-only"${key.role === "read-only" ? " selected" : ""}>read-only</option>
        <option value="read-write"${key.role === "read-write" ? " selected" : ""}>read-write</option>
        <option value="admin"${key.role === "admin" ? " selected" : ""}>admin</option>
      `;

      const prefixLabel = h("label", { className: "text-muted", style: { fontSize: "0.78rem", fontWeight: "600" } }, "Prefixes (comma-separated, leave empty for all)");
      const prefixInput = h("input", {
        type: "text",
        value: (key.prefixes || []).join(", "),
        className: "key-modal-input",
      });

      form.appendChild(nameLabel);
      form.appendChild(nameInput);
      form.appendChild(roleLabel);
      form.appendChild(roleSelect);
      form.appendChild(prefixLabel);
      form.appendChild(prefixInput);

      const actions = h("div", { className: "modal-actions" });
      const cancelBtn = h("button", { className: "btn", onClick: hideModal }, "Cancel");
      const saveBtn = h("button", { className: "btn btn-primary" }, "Save");

      actions.appendChild(cancelBtn);
      actions.appendChild(saveBtn);

      content.appendChild(form);
      content.appendChild(actions);

      saveBtn.addEventListener("click", async () => {
        const name = nameInput.value.trim();
        const role = roleSelect.value;
        const prefixesRaw = prefixInput.value.trim();
        const prefixes = prefixesRaw
          ? prefixesRaw.split(",").map(s => s.trim()).filter(Boolean)
          : [];

        saveBtn.disabled = true;
        saveBtn.textContent = "Saving...";

        try {
          await api(`/api/keys/${key.id}`, {
            method: "PATCH",
            body: JSON.stringify({ name, role, prefixes }),
          });
          invalidateCache();
          hideModal();
          showToast("Key updated", "success");
          loadKeys();
        } catch (err) {
          showToast(err.message, "error");
          saveBtn.disabled = false;
          saveBtn.textContent = "Save";
        }
      });
    });
  }

  // Revoke Key modal
  function openRevokeModal(key) {
    showModal((content) => {
      content.appendChild(h("div", { className: "modal-title" }, "Revoke Key"));

      const warning = h("div", { style: { fontSize: "0.9rem", lineHeight: "1.6" } },
        `Revoke key "${key.name || "Unnamed"}"? This cannot be undone.`
      );
      content.appendChild(warning);

      const actions = h("div", { className: "modal-actions" });
      const cancelBtn = h("button", { className: "btn", onClick: hideModal }, "Cancel");
      const confirmBtn = h("button", { className: "btn btn-danger" }, "Revoke");

      actions.appendChild(cancelBtn);
      actions.appendChild(confirmBtn);
      content.appendChild(actions);

      confirmBtn.addEventListener("click", async () => {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Revoking...";

        try {
          await api(`/api/keys/${key.id}`, { method: "DELETE" });
          invalidateCache();
          hideModal();
          showToast("Key revoked", "success");
          loadKeys();
        } catch (err) {
          showToast(err.message, "error");
          confirmBtn.disabled = false;
          confirmBtn.textContent = "Revoke";
        }
      });
    });
  }

  // Initial load
  loadKeys();
});

// ==========================================================================
//  Settings Page
// ==========================================================================

registerPage("settings", async (container) => {
  container.innerHTML = '<div class="loading-state"><div class="loading-spinner"></div></div>';

  try {
    const [extractStatus, stats, metrics] = await Promise.all([
      api("/extract/status"),
      api("/stats"),
      api("/metrics").catch(() => null),
    ]);

    container.innerHTML = "";

    // Section: Extraction Provider
    const extractSection = h("div", { className: "settings-section" });
    const extractTitle = h("div", { className: "section-title" }, "Extraction Provider");
    const extractGrid = h("div", { className: "info-grid" });

    const exStatusLabel = extractStatus.enabled ? "Active" : "Inactive";
    const exStatusBadge = extractStatus.enabled ? "badge-success" : "badge-warning";

    extractGrid.innerHTML = `
      <div class="info-item">
        <div class="info-item-label">Provider</div>
        <div class="info-item-value">${escHtml(extractStatus.provider || "N/A")}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Model</div>
        <div class="info-item-value">${escHtml(extractStatus.model || "N/A")}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Status</div>
        <div class="info-item-value"><span class="badge ${exStatusBadge}">${escHtml(exStatusLabel)}</span></div>
      </div>
    `;

    extractSection.appendChild(extractTitle);
    extractSection.appendChild(extractGrid);
    container.appendChild(extractSection);

    // Section: Server Info
    const serverSection = h("div", { className: "settings-section" });
    const serverTitle = h("div", { className: "section-title" }, "Server Info");
    const serverGrid = h("div", { className: "info-grid" });

    const indexKB = stats.index_size_bytes != null
      ? (stats.index_size_bytes / 1024).toFixed(1) + " KB"
      : "N/A";

    const autoReload = metrics?.auto_reload_enabled != null
      ? (metrics.auto_reload_enabled ? "Enabled" : "Disabled")
      : (stats.auto_reload_enabled != null
        ? (stats.auto_reload_enabled ? "Enabled" : "Disabled")
        : "N/A");

    serverGrid.innerHTML = `
      <div class="info-item">
        <div class="info-item-label">Embedder Model</div>
        <div class="info-item-value">${escHtml(stats.model || "N/A")}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Index Size</div>
        <div class="info-item-value">${escHtml(indexKB)}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Backups</div>
        <div class="info-item-value">${escHtml(String(stats.backup_count ?? 0))}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Auto-Reload</div>
        <div class="info-item-value">${escHtml(autoReload)}</div>
      </div>
    `;

    serverSection.appendChild(serverTitle);
    serverSection.appendChild(serverGrid);
    container.appendChild(serverSection);

    // Section: Appearance
    const appearSection = h("div", { className: "settings-section" });
    const appearTitle = h("div", { className: "section-title" }, "Appearance");
    const appearDesc = h("div", { className: "text-muted mb-16", style: { fontSize: "0.84rem" } },
      "Choose your preferred color theme. This syncs with the sidebar theme switcher."
    );

    const themeSwitcher = h("div", { className: "theme-switcher-large" });
    const currentTheme = localStorage.getItem(THEME_STORAGE) || "system";

    const themes = [
      { value: "system", label: "System" },
      { value: "dark", label: "Dark" },
      { value: "light", label: "Light" },
    ];

    themes.forEach(({ value, label }) => {
      const btn = h("button", {
        className: `theme-btn-lg${currentTheme === value ? " active" : ""}`,
        dataset: { themeChoice: value },
      }, label);

      btn.addEventListener("click", () => {
        // Update localStorage and apply
        localStorage.setItem(THEME_STORAGE, value);
        applyTheme(value);

        // Sync sidebar theme buttons
        syncThemeButtons(value);

        // Update large theme button active states
        themeSwitcher.querySelectorAll(".theme-btn-lg").forEach((b) => {
          b.classList.toggle("active", b.dataset.themeChoice === value);
        });
      });

      themeSwitcher.appendChild(btn);
    });

    appearSection.appendChild(appearTitle);
    appearSection.appendChild(appearDesc);
    appearSection.appendChild(themeSwitcher);
    container.appendChild(appearSection);

    // Section: Danger Zone
    const dangerSection = h("div", { className: "settings-section" });
    const dangerTitle = h("div", { className: "section-title" }, "Danger Zone");
    const dangerZone = h("div", { className: "danger-zone" });

    const dangerHeading = h("div", {
      style: { fontSize: "0.9rem", fontWeight: "600", color: "var(--color-error)", marginBottom: "8px" },
    }, "Danger Zone");
    const dangerDesc = h("div", { className: "text-muted mb-16", style: { fontSize: "0.84rem" } },
      "These actions are irreversible. Proceed with caution."
    );

    const dangerActions = h("div", { className: "flex gap-8", style: { flexWrap: "wrap" } });

    // Export All Memories
    const exportBtn = h("button", { className: "btn" }, "Export All Memories");
    exportBtn.addEventListener("click", async () => {
      exportBtn.disabled = true;
      exportBtn.textContent = "Exporting...";
      try {
        const data = await api("/memories?limit=10000");
        const memories = data.memories || [];
        const blob = new Blob([JSON.stringify(memories, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `memories-export-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast(`Exported ${memories.length} memories`, "success");
      } catch (err) {
        showToast(err.message, "error");
      } finally {
        exportBtn.disabled = false;
        exportBtn.textContent = "Export All Memories";
      }
    });

    // Rebuild Index
    const rebuildBtn = h("button", { className: "btn btn-danger" }, "Rebuild Index");
    rebuildBtn.addEventListener("click", async () => {
      if (!confirm("Are you sure you want to rebuild the entire index? This may take a while.")) return;
      rebuildBtn.disabled = true;
      rebuildBtn.textContent = "Rebuilding...";
      try {
        await api("/index/build", { method: "POST" });
        invalidateCache();
        showToast("Index rebuild started", "success");
      } catch (err) {
        showToast(err.message, "error");
      } finally {
        rebuildBtn.disabled = false;
        rebuildBtn.textContent = "Rebuild Index";
      }
    });

    dangerActions.appendChild(exportBtn);
    dangerActions.appendChild(rebuildBtn);

    dangerZone.appendChild(dangerHeading);
    dangerZone.appendChild(dangerDesc);
    dangerZone.appendChild(dangerActions);

    dangerSection.appendChild(dangerTitle);
    dangerSection.appendChild(dangerZone);
    container.appendChild(dangerSection);
  } catch (err) {
    container.innerHTML = "";
    container.appendChild(
      h("div", { className: "empty-state" },
        h("div", { className: "empty-state-icon" }, "\u26A0"),
        h("div", { className: "empty-state-title" }, "Failed to load settings"),
        h("div", { className: "empty-state-text" }, escHtml(err.message))
      )
    );
    showToast(err.message, "error");
  }
});

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

// -- Mobile Sidebar --------------------------------------------------------

function initMobileSidebar() {
  const menuBtn = document.getElementById("menuBtn");
  const sidebar = document.querySelector(".sidebar");
  const overlay = document.getElementById("sidebarOverlay");
  if (!menuBtn || !sidebar || !overlay) return;

  function openSidebar() {
    sidebar.classList.add("open");
    overlay.classList.add("visible");
  }

  function closeSidebar() {
    sidebar.classList.remove("open");
    overlay.classList.remove("visible");
  }

  menuBtn.addEventListener("click", () => {
    if (sidebar.classList.contains("open")) {
      closeSidebar();
    } else {
      openSidebar();
    }
  });

  overlay.addEventListener("click", closeSidebar);

  // Close sidebar when a nav item is clicked (mobile)
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", closeSidebar);
  });
}

// -- Boot ------------------------------------------------------------------

initTheme();
syncKeyStatus();
initMobileSidebar();
initRouter();
