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
        <div class="stat-value">${formatNumber(stats.total_memories)}</div>
        <span class="stat-label">Total Memories</span>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatNumber(extractionCalls)}</div>
        <span class="stat-label">Extractions 7d</span>
      </div>
      <div class="stat-card">
        <div class="stat-value">${formatNumber(opsTotal)}</div>
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
        <div class="info-item-value">${stats.dimension ?? "N/A"}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Index Size</div>
        <div class="info-item-value">${escHtml(indexKB)}</div>
      </div>
      <div class="info-item">
        <div class="info-item-label">Backups</div>
        <div class="info-item-value">${stats.backup_count ?? 0}</div>
      </div>
    `;

    container.appendChild(infoSection);
    container.appendChild(infoGrid);
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
  const memState = { offset: 0, limit: 20, source: "", total: 0, selected: null, view: "list", searchQuery: "", searchResults: null };

  // -- Delete handler (exposed globally for innerHTML onclick) --
  window.deleteMemory = async (id) => {
    if (!confirm(`Delete memory #${id}?`)) return;
    try {
      await api(`/memory/${id}`, { method: "DELETE" });
      showToast("Memory deleted", "success");
      memState.selected = null;
      memState.offset = 0;
      await loadMemories();
    } catch (err) {
      showToast(err.message, "error");
    }
  };

  // -- Build page skeleton --
  function buildSkeleton() {
    container.innerHTML = "";

    // Filter bar
    const filterBar = h("div", { className: "filter-bar mb-16" });

    const sourceInput = h("input", {
      type: "text",
      placeholder: "Filter by source prefix...",
      value: memState.source,
      style: { width: "220px" },
    });
    sourceInput.addEventListener("input", (e) => {
      memState.source = e.target.value;
      memState.offset = 0;
      memState.searchQuery = "";
      memState.searchResults = null;
      // Clear global search input when using source filter
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

    filterBar.appendChild(sourceInput);
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
    memories.forEach((mem) => {
      const isActive = memState.selected && memState.selected.id === mem.id;
      const item = document.createElement("div");
      item.className = `memory-item${isActive ? " active" : ""}`;
      const truncText = (mem.text || "").length > 120
        ? escHtml((mem.text || "").slice(0, 120)) + "..."
        : escHtml(mem.text || "");

      let scoreHtml = "";
      if (mem.similarity != null) {
        scoreHtml = ` <span class="search-result-score">${(mem.similarity * 100).toFixed(1)}%</span>`;
      }

      item.innerHTML = `
        <div class="memory-item-header">
          <span class="memory-item-source">${escHtml(mem.source || "")}</span>
          <span class="memory-item-id">#${mem.id}${scoreHtml}</span>
        </div>
        <div class="memory-item-text">${truncText}</div>
      `;
      item.addEventListener("click", () => {
        memState.selected = mem;
        renderContent();
      });
      listPanel.appendChild(item);
    });

    // Right: detail panel
    const detailPanel = h("div", { className: "memories-detail-panel" });
    if (memState.selected) {
      const mem = memState.selected;
      detailPanel.innerHTML = `
        <div class="detail-header">
          <div>
            <span class="memory-item-source" style="font-size:0.85rem;">${escHtml(mem.source || "")}</span>
          </div>
          <span class="memory-item-id" style="font-size:0.78rem;">#${mem.id}</span>
        </div>
        <div class="detail-text">${escHtml(mem.text || "")}</div>
        <div class="detail-meta">
          <div class="meta-item">
            <div class="meta-label">ID</div>
            <div class="meta-value font-mono">${mem.id}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Source</div>
            <div class="meta-value">${escHtml(mem.source || "N/A")}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Created</div>
            <div class="meta-value">${mem.created_at ? timeAgo(mem.created_at) : "N/A"}</div>
          </div>
        </div>
        <div class="detail-actions">
          <button class="btn btn-danger btn-sm" onclick="deleteMemory(${mem.id})">Delete</button>
        </div>
      `;
    } else {
      detailPanel.innerHTML = `
        <div class="empty-state" style="border:none;padding:60px 20px;">
          <div class="empty-state-icon">\u25C6</div>
          <div class="empty-state-text">Select a memory to view details</div>
        </div>
      `;
    }

    layout.appendChild(listPanel);
    layout.appendChild(detailPanel);
    contentDiv.appendChild(layout);
  }

  // -- Grid view --
  function renderGridView(contentDiv, memories) {
    const grid = h("div", { className: "memory-grid-view" });
    memories.forEach((mem) => {
      const truncText = (mem.text || "").length > 200
        ? escHtml((mem.text || "").slice(0, 200)) + "..."
        : escHtml(mem.text || "");

      let scoreHtml = "";
      if (mem.similarity != null) {
        scoreHtml = ` <span class="search-result-score">${(mem.similarity * 100).toFixed(1)}%</span>`;
      }

      const card = document.createElement("div");
      card.className = "memory-card";
      card.innerHTML = `
        <div class="memory-item-header">
          <span class="memory-item-source">${escHtml(mem.source || "")}</span>
          <span class="memory-item-id">#${mem.id}${scoreHtml}</span>
        </div>
        <div class="memory-item-text mt-8">${truncText}</div>
      `;
      grid.appendChild(card);
    });
    contentDiv.appendChild(grid);
  }

  // -- Pagination --
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

    const start = memState.total > 0 ? memState.offset + 1 : 0;
    const end = Math.min(memState.offset + memState.limit, memState.total);
    const info = h("span", { className: "pagination-info" },
      `${start}\u2013${end} of ${memState.total}`
    );

    const btns = h("div", { className: "pagination-buttons" });
    const prevBtn = h("button", {
      className: "btn btn-sm",
      onClick: () => {
        memState.offset = Math.max(0, memState.offset - memState.limit);
        loadMemories();
      },
    }, "Previous");
    if (memState.offset === 0) prevBtn.disabled = true;

    const nextBtn = h("button", {
      className: "btn btn-sm",
      onClick: () => {
        memState.offset += memState.limit;
        loadMemories();
      },
    }, "Next");
    if (memState.offset + memState.limit >= memState.total) nextBtn.disabled = true;

    btns.appendChild(prevBtn);
    btns.appendChild(nextBtn);

    pag.appendChild(info);
    pag.appendChild(btns);
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
  window._memoriesPageSearch = searchMemories;
  window._memoriesPageReset = () => {
    memState.searchQuery = "";
    memState.searchResults = null;
    memState.offset = 0;
    loadMemories();
  };

  // -- Initial render --
  buildSkeleton();

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
      if (state.currentPage === "memories" && window._memoriesPageReset) {
        window._memoriesPageReset();
      }
      return;
    }

    if (state.currentPage === "memories" && window._memoriesPageSearch) {
      window._memoriesPageSearch(query);
    } else {
      // Navigate to memories page; the page will pick up the query from the input
      window.location.hash = "#/memories";
    }
  });

  // Handle clearing via the search clear button (type=search)
  globalSearch.addEventListener("search", () => {
    if (globalSearch.value === "" && state.currentPage === "memories" && window._memoriesPageReset) {
      window._memoriesPageReset();
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

    // Compute success rate from by_model data if available
    let successRate = "\u2014";
    if (totalCalls > 0) {
      let totalSuccess = 0;
      let totalAttempts = 0;
      for (const m of Object.values(byModel)) {
        totalAttempts += m.calls || 0;
        // If there's no error breakdown, assume all calls succeeded
        totalSuccess += m.calls || 0;
      }
      if (totalAttempts > 0) {
        successRate = ((totalSuccess / totalAttempts) * 100).toFixed(1) + "%";
      }
    }

    const statusLabel = extractStatus.enabled ? "Active" : "Inactive";
    const statusBadgeClass = extractStatus.enabled ? "badge-success" : "badge-warning";
    const providerLabel = extractStatus.provider || "N/A";

    container.innerHTML = "";

    // Section: Stat cards
    const statSection = h("div", { className: "section-title" }, "Overview");
    const statGrid = h("div", { className: "stat-grid" });

    statGrid.innerHTML = `
      <div class="stat-card">
        <div class="stat-value">${formatNumber(totalCalls)}</div>
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
              <td>${formatNumber(data.calls || 0)}</td>
              <td>${formatNumber(data.input_tokens || 0)}</td>
              <td>${formatNumber(data.output_tokens || 0)}</td>
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

    state.apiKey = val;
    sessionStorage.setItem(API_KEY_STORAGE, state.apiKey);

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
    state.apiKey = "";
    sessionStorage.removeItem(API_KEY_STORAGE);
    statusEl.innerHTML = `<span class="badge badge-warning">Key cleared</span>`;
    showToast("API key cleared", "info");
  });

  keyForm.appendChild(description);
  keyForm.appendChild(inputRow);
  keyForm.appendChild(statusEl);

  configSection.appendChild(configTitle);
  configSection.appendChild(keyForm);
  container.appendChild(configSection);

  // Section: Key Management Placeholder
  const mgmtSection = h("div", { className: "settings-section" });
  const mgmtTitle = h("div", { className: "section-title" }, "Key Management");

  const placeholder = h("div", { className: "empty-state" },
    h("div", { className: "empty-state-icon" }, "\u26B6"),
    h("div", { className: "empty-state-title" }, "Multi-key management coming soon"),
    h("div", { className: "empty-state-text" },
      "The server currently uses a single API_KEY environment variable. " +
      "Once the backend adds multi-key endpoints, you will be able to create, rotate, and revoke keys here."
    )
  );

  mgmtSection.appendChild(mgmtTitle);
  mgmtSection.appendChild(placeholder);
  container.appendChild(mgmtSection);
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
        <div class="info-item-value">${stats.backup_count ?? 0}</div>
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

// -- Boot ------------------------------------------------------------------

initTheme();
initRouter();
