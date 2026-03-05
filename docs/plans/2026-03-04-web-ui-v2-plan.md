# Web UI v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current 2-tab Memory Observatory UI with a 5-page sidebar-nav SPA using Arkos-inspired dark/light theme.

**Architecture:** Complete rewrite of `webui/` (index.html, styles.css, app.js). Vanilla JS SPA with hash-based routing, CSS custom properties for theming, no framework or build step. All API endpoints already exist — this is frontend-only work.

**Tech Stack:** HTML5, CSS3 (custom properties, grid, flexbox), vanilla JavaScript (ES2020+), no dependencies.

**Design reference:** `docs/designs/memories-ui-v2.pen`

---

## Task 1: HTML Shell + CSS Foundation + Router

**Files:**
- Rewrite: `webui/index.html`
- Rewrite: `webui/styles.css`
- Rewrite: `webui/app.js`

**Step 1: Write the HTML shell**

Replace `webui/index.html` with the new sidebar layout structure:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Memories</title>
  <link rel="icon" type="image/svg+xml" href="/ui/static/favicon.svg">
  <link rel="stylesheet" href="/ui/static/styles.css">
  <script>
    // Apply theme immediately to prevent flash
    (function(){
      var s=localStorage.getItem("memories-theme");
      var t=s||(window.matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
      document.documentElement.dataset.theme=t;
      if(s)document.documentElement.dataset.themeOverride=s;
    })();
  </script>
</head>
<body>
  <div class="app">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-logo">
        <span class="logo-text">Memories</span>
      </div>
      <nav class="sidebar-nav" id="sidebarNav">
        <a href="#/dashboard" class="nav-item active" data-page="dashboard">
          <span class="nav-icon">&#9632;</span>
          <span class="nav-label">Dashboard</span>
        </a>
        <a href="#/memories" class="nav-item" data-page="memories">
          <span class="nav-icon">&#9672;</span>
          <span class="nav-label">Memories</span>
        </a>
        <a href="#/extractions" class="nav-item" data-page="extractions">
          <span class="nav-icon">&#9881;</span>
          <span class="nav-label">Extractions</span>
        </a>
        <a href="#/keys" class="nav-item" data-page="keys">
          <span class="nav-icon">&#9919;</span>
          <span class="nav-label">API Keys</span>
        </a>
        <a href="#/settings" class="nav-item" data-page="settings">
          <span class="nav-icon">&#9881;</span>
          <span class="nav-label">Settings</span>
        </a>
      </nav>
      <div class="sidebar-footer">
        <div class="theme-switcher" id="themeSwitcher">
          <button class="theme-btn" data-theme="system" title="System theme">Auto</button>
          <button class="theme-btn active" data-theme="dark" title="Dark theme">Dark</button>
          <button class="theme-btn" data-theme="light" title="Light theme">Light</button>
        </div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar" id="topbar">
        <h1 class="topbar-title" id="pageTitle">Dashboard</h1>
        <div class="topbar-actions">
          <select class="key-filter" id="keyFilter" title="Filter by API key">
            <option value="">All Keys</option>
          </select>
          <div class="search-box">
            <input type="search" id="globalSearch" placeholder="Search memories..." aria-label="Search memories">
          </div>
        </div>
      </header>

      <div class="content" id="content">
        <!-- Pages rendered here by router -->
      </div>
    </div>
  </div>

  <div id="modalOverlay" class="modal-overlay" hidden></div>

  <script src="/ui/static/app.js" type="module"></script>
</body>
</html>
```

**Step 2: Write CSS custom properties with Arkos dark/light tokens**

Replace `webui/styles.css`. Start with the token layer and layout foundation only.

Dark theme tokens (from `.kalos.yaml` brands.palettes.dark):
```css
:root {
  /* Dark theme (default) */
  --color-primary: #d4af37;
  --color-primary-hover: #f5d060;
  --color-bg: #0a0a0a;
  --color-bg-elevated: #111111;
  --color-bg-surface: #1a1a1a;
  --color-text: #e5e5e5;
  --color-text-muted: #a3a3a3;
  --color-text-faint: #666666;
  --color-border: rgba(212,175,55,0.15);
  --color-success: #16A34A;
  --color-warning: #CA8A04;
  --color-error: #DC2626;
  --color-info: #2563EB;

  --font-body: 'Inter', system-ui, sans-serif;
  --font-display: 'Philosopher', serif;
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;

  --sidebar-width: 200px;
  --topbar-height: 56px;
}

[data-theme="light"] {
  --color-primary: #b8960f;
  --color-primary-hover: #d4af37;
  --color-bg: #FAF9F6;
  --color-bg-elevated: #FFFEF9;
  --color-bg-surface: #F0EDE6;
  --color-text: #2C2418;
  --color-text-muted: #7A7060;
  --color-text-faint: #A69E90;
  --color-border: #E8E2D6;
}
```

Layout structure (sidebar + main + topbar):
```css
*, *::before, *::after { box-sizing: border-box; margin: 0; }

body {
  font-family: var(--font-body);
  color: var(--color-text);
  background: var(--color-bg);
  min-height: 100vh;
}

.app {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  min-height: 100vh;
}

.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--color-bg-elevated);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  padding: 16px 0;
  z-index: 10;
}

.main {
  grid-column: 2;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.topbar {
  position: sticky;
  top: 0;
  height: var(--topbar-height);
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  z-index: 5;
}

.content {
  flex: 1;
  padding: 24px;
  overflow-y: auto;
}
```

**Step 3: Write the hash router and page mount system**

Create `webui/app.js` with the router core, theme system, and API helper:

```javascript
// -- Config -------------------------------------------------------------------
const API_KEY_STORAGE = "memories-api-key";
const THEME_STORAGE = "memories-theme";

const state = {
  apiKey: sessionStorage.getItem(API_KEY_STORAGE) || "",
  currentPage: null,
};

// -- API helper ---------------------------------------------------------------
function authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (state.apiKey) h["X-API-Key"] = state.apiKey;
  return h;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: { ...authHeaders(), ...options.headers },
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`API ${resp.status}: ${body}`);
  }
  return resp.json();
}

// -- Theme system -------------------------------------------------------------
function initTheme() {
  const switcher = document.getElementById("themeSwitcher");
  const stored = localStorage.getItem(THEME_STORAGE);

  function applyTheme(choice) {
    if (choice === "system") {
      const os = window.matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light";
      document.documentElement.dataset.theme = os;
      delete document.documentElement.dataset.themeOverride;
      localStorage.removeItem(THEME_STORAGE);
    } else {
      document.documentElement.dataset.theme = choice;
      document.documentElement.dataset.themeOverride = choice;
      localStorage.setItem(THEME_STORAGE, choice);
    }
    switcher.querySelectorAll(".theme-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.theme === choice);
    });
  }

  switcher.addEventListener("click", (e) => {
    const btn = e.target.closest(".theme-btn");
    if (btn) applyTheme(btn.dataset.theme);
  });

  applyTheme(stored || "system");

  window.matchMedia("(prefers-color-scheme:dark)").addEventListener("change", () => {
    if (!localStorage.getItem(THEME_STORAGE)) applyTheme("system");
  });
}

// -- Router -------------------------------------------------------------------
const pages = {};  // populated by page modules

function registerPage(name, renderFn) {
  pages[name] = renderFn;
}

function navigate(page) {
  if (state.currentPage === page) return;
  state.currentPage = page;

  // Update nav active state
  document.querySelectorAll(".nav-item").forEach(a => {
    a.classList.toggle("active", a.dataset.page === page);
  });

  // Update page title
  const titles = {
    dashboard: "Dashboard",
    memories: "Memories",
    extractions: "Extractions",
    keys: "API Keys",
    settings: "Settings",
  };
  document.getElementById("pageTitle").textContent = titles[page] || page;

  // Render page
  const content = document.getElementById("content");
  content.innerHTML = "";
  if (pages[page]) {
    pages[page](content);
  } else {
    content.innerHTML = `<p>Page not found: ${page}</p>`;
  }
}

function initRouter() {
  function handleHash() {
    const hash = location.hash.replace("#/", "") || "dashboard";
    navigate(hash);
  }
  window.addEventListener("hashchange", handleHash);
  handleHash();
}

// -- Boot ---------------------------------------------------------------------
initTheme();
initRouter();
```

**Step 4: Verify foundation loads**

Run: Open `http://localhost:8900/ui` in browser.
Expected: Sidebar with 5 nav items on left, top bar with title, empty content area. Dark theme with gold sidebar text. Clicking nav items changes URL hash and page title.

**Step 5: Commit**

```bash
git add webui/index.html webui/styles.css webui/app.js
git commit -m "feat(ui): scaffold v2 SPA shell with sidebar nav, router, and Arkos theme tokens"
```

---

## Task 2: Sidebar + Top Bar Polish

**Files:**
- Modify: `webui/styles.css`
- Modify: `webui/index.html`

**Step 1: Style the sidebar**

Add to `styles.css`:
- `.sidebar-logo`: padding 16px, font-size 1.1rem, color primary, font-weight 700, border-bottom
- `.sidebar-nav`: flex: 1, display flex column, gap 2px, padding 12px 8px
- `.nav-item`: display flex, align-items center, gap 10px, padding 8px 12px, border-radius var(--radius-md), color var(--color-text-muted), text-decoration none, transition background 120ms
- `.nav-item:hover`: background rgba(255,255,255,0.05) (dark) / rgba(0,0,0,0.04) (light)
- `.nav-item.active`: background rgba(212,175,55,0.12), color var(--color-primary), border-left 3px solid var(--color-primary)
- `.nav-icon`: width 20px, text-align center, font-size 0.9rem
- `.sidebar-footer`: padding 12px 8px, border-top 1px solid var(--color-border)
- `.theme-switcher`: display flex, gap 2px, background var(--color-bg-surface), border-radius var(--radius-md), padding 2px
- `.theme-btn`: flex 1, padding 6px 0, border none, background transparent, color var(--color-text-muted), font-size 0.75rem, cursor pointer, border-radius var(--radius-sm)
- `.theme-btn.active`: background var(--color-bg-elevated), color var(--color-text), box-shadow 0 1px 2px rgba(0,0,0,0.2)

**Step 2: Style the top bar**

- `.topbar-title`: font-size 1.1rem, font-weight 600
- `.topbar-actions`: display flex, gap 12px, align-items center
- `.key-filter`: background var(--color-bg-elevated), border 1px solid var(--color-border), color var(--color-text), padding 6px 12px, border-radius var(--radius-md), font-size 0.85rem
- `.search-box input`: background var(--color-bg-elevated), border 1px solid var(--color-border), color var(--color-text), padding 8px 12px, border-radius var(--radius-md), width 240px, font-size 0.85rem

**Step 3: Verify visuals**

Run: Refresh `http://localhost:8900/ui`
Expected: Gold "Memories" logo top-left, 5 nav items with hover/active states, theme switcher at bottom with System/Dark/Light buttons, clean top bar with title + search.

**Step 4: Commit**

```bash
git add webui/styles.css webui/index.html
git commit -m "feat(ui): style sidebar nav and top bar components"
```

---

## Task 3: Shared UI Components (stat cards, tables, badges)

**Files:**
- Modify: `webui/styles.css`
- Modify: `webui/app.js`

**Step 1: Add reusable CSS classes**

Add to `styles.css`:

```css
/* -- Stat cards -------------------------------------------------------------- */
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
.stat-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}
.stat-value { font-size: 1.8rem; font-weight: 700; color: var(--color-text); }
.stat-label { font-size: 0.8rem; color: var(--color-text-muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.06em; }

/* -- Data table -------------------------------------------------------------- */
.data-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.data-table th {
  text-align: left; color: var(--color-text-muted); font-weight: 600;
  font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em;
  padding: 8px 12px; border-bottom: 1px solid var(--color-border);
}
.data-table td { padding: 10px 12px; border-bottom: 1px solid var(--color-border); }
.data-table tbody tr:hover { background: rgba(212,175,55,0.04); }

/* -- Badges ------------------------------------------------------------------ */
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 9999px;
  font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em;
}
.badge-success { background: rgba(22,163,74,0.15); color: #16A34A; }
.badge-warning { background: rgba(202,138,4,0.15); color: #CA8A04; }
.badge-error   { background: rgba(220,38,38,0.15); color: #DC2626; }
.badge-info    { background: rgba(37,99,235,0.15); color: #2563EB; }
.badge-primary { background: rgba(212,175,55,0.15); color: var(--color-primary); }

/* -- Buttons ----------------------------------------------------------------- */
.btn {
  padding: 8px 16px; border-radius: var(--radius-md); border: 1px solid var(--color-border);
  background: var(--color-bg-elevated); color: var(--color-text);
  font-size: 0.85rem; cursor: pointer; transition: all 120ms ease;
}
.btn:hover { border-color: var(--color-primary); }
.btn-primary {
  background: var(--color-primary); color: #0a0a0a; border-color: transparent; font-weight: 600;
}
.btn-primary:hover { background: var(--color-primary-hover); }
.btn-danger { background: var(--color-error); color: #fff; border-color: transparent; }
.btn-sm { padding: 4px 10px; font-size: 0.78rem; }

/* -- Section headings -------------------------------------------------------- */
.section-title {
  font-size: 0.88rem; font-weight: 600; color: var(--color-text);
  margin-bottom: 12px;
}

/* -- Empty state ------------------------------------------------------------- */
.empty-state {
  text-align: center; padding: 40px; color: var(--color-text-muted);
  border: 1px dashed var(--color-border); border-radius: var(--radius-lg);
}
```

**Step 2: Add JS helper functions to app.js**

```javascript
// -- UI helpers ---------------------------------------------------------------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const c of children) {
    if (typeof c === "string") el.appendChild(document.createTextNode(c));
    else if (c) el.appendChild(c);
  }
  return el;
}

function formatNumber(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function timeAgo(dateStr) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function escHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}
```

**Step 3: Verify helpers work**

Run: Open browser console, confirm `h("div", {class: "test"}, "hello")` creates element.
Expected: DOM element created correctly.

**Step 4: Commit**

```bash
git add webui/styles.css webui/app.js
git commit -m "feat(ui): add shared UI components — stat cards, tables, badges, buttons"
```

---

## Task 4: Dashboard Page

**Files:**
- Modify: `webui/app.js`

**Step 1: Implement dashboard page renderer**

Add to `app.js` after helpers:

```javascript
// -- Dashboard page -----------------------------------------------------------
registerPage("dashboard", async (container) => {
  container.innerHTML = '<p class="loading">Loading dashboard...</p>';

  try {
    // Fetch stats in parallel
    const [stats, usage, extractStatus] = await Promise.all([
      api("/stats"),
      api("/usage?period=7d").catch(() => null),
      api("/extract/status").catch(() => null),
    ]);

    container.innerHTML = "";

    // Stat cards
    const grid = h("div", { class: "stat-grid" });

    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, formatNumber(stats.total_memories || 0)),
      h("div", { class: "stat-label" }, "Total Memories"),
    ));

    const extractJobs = usage?.extraction?.total_calls || 0;
    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, formatNumber(extractJobs)),
      h("div", { class: "stat-label" }, "Extractions (7d)"),
    ));

    const totalOps = usage ? Object.values(usage.operations || {}).reduce((s, v) => s + (v.total || 0), 0) : 0;
    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, formatNumber(totalOps)),
      h("div", { class: "stat-label" }, "Operations (7d)"),
    ));

    const activeKeys = extractStatus?.enabled ? "Active" : "Inactive";
    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, activeKeys),
      h("div", { class: "stat-label" }, "Extraction Status"),
    ));

    container.appendChild(grid);

    // Server info summary
    const info = h("div", { class: "info-section", style: "margin-top: 24px;" });
    info.appendChild(h("h2", { class: "section-title" }, "Server Info"));
    const infoGrid = h("div", { class: "info-grid" });
    infoGrid.innerHTML = `
      <div class="info-item"><span class="info-label">Model</span><span class="info-value">${escHtml(stats.model || "—")}</span></div>
      <div class="info-item"><span class="info-label">Dimensions</span><span class="info-value">${stats.dimension || "—"}</span></div>
      <div class="info-item"><span class="info-label">Index Size</span><span class="info-value">${((stats.index_size_bytes || 0) / 1024).toFixed(0)} KB</span></div>
      <div class="info-item"><span class="info-label">Backups</span><span class="info-value">${stats.backup_count || 0}</span></div>
    `;
    info.appendChild(infoGrid);
    container.appendChild(info);

  } catch (err) {
    container.innerHTML = `<div class="empty-state">Failed to load dashboard: ${escHtml(err.message)}</div>`;
  }
});
```

**Step 2: Add dashboard-specific CSS**

```css
.loading { color: var(--color-text-muted); padding: 20px; }
.info-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;
}
.info-item {
  background: var(--color-bg-elevated); border: 1px solid var(--color-border);
  border-radius: var(--radius-md); padding: 12px;
}
.info-label { display: block; font-size: 0.75rem; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.06em; }
.info-value { display: block; margin-top: 4px; font-size: 0.95rem; font-weight: 500; }
```

**Step 3: Verify dashboard renders**

Run: Navigate to `http://localhost:8900/ui#/dashboard`
Expected: 4 stat cards (Total Memories, Extractions 7d, Operations 7d, Extraction Status) + Server Info section with model, dimensions, index size, backups.

**Step 4: Commit**

```bash
git add webui/app.js webui/styles.css
git commit -m "feat(ui): add dashboard page with stat cards and server info"
```

---

## Task 5: Memories Page — List View + Pagination

**Files:**
- Modify: `webui/app.js`
- Modify: `webui/styles.css`

**Step 1: Implement memories list page**

Add to `app.js`:

```javascript
// -- Memories page ------------------------------------------------------------
const memState = {
  offset: 0,
  limit: 20,
  source: "",
  total: 0,
  selected: null, // memory id for detail panel
  view: "list",   // "list" or "grid"
};

registerPage("memories", (container) => {
  container.innerHTML = "";

  // Filter bar
  const filterBar = h("div", { class: "filter-bar" });

  const sourceInput = h("input", {
    type: "text",
    class: "filter-input",
    placeholder: "Filter by source prefix...",
    value: memState.source,
  });
  sourceInput.addEventListener("change", () => {
    memState.source = sourceInput.value;
    memState.offset = 0;
    loadMemoriesList();
  });

  const viewToggle = h("div", { class: "view-toggle" });
  viewToggle.innerHTML = `
    <button class="view-btn ${memState.view === 'list' ? 'active' : ''}" data-view="list" title="List view">List</button>
    <button class="view-btn ${memState.view === 'grid' ? 'active' : ''}" data-view="grid" title="Grid view">Grid</button>
  `;
  viewToggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".view-btn");
    if (!btn) return;
    memState.view = btn.dataset.view;
    viewToggle.querySelectorAll(".view-btn").forEach(b => b.classList.toggle("active", b.dataset.view === memState.view));
    renderMemoriesContent();
  });

  filterBar.appendChild(sourceInput);
  filterBar.appendChild(viewToggle);
  container.appendChild(filterBar);

  // Layout: list panel + detail panel
  const layout = h("div", { class: "memories-layout", id: "memoriesLayout" });
  layout.innerHTML = '<div class="memories-list-panel" id="memoriesListPanel"></div><div class="memories-detail-panel" id="memoriesDetailPanel"><div class="empty-state">Select a memory to view details</div></div>';
  container.appendChild(layout);

  // Pagination
  const pagination = h("div", { class: "pagination", id: "memoriesPagination" });
  container.appendChild(pagination);

  loadMemoriesList();
});

async function loadMemoriesList() {
  const panel = document.getElementById("memoriesListPanel");
  if (!panel) return;
  panel.innerHTML = '<p class="loading">Loading...</p>';

  const params = new URLSearchParams({
    offset: String(memState.offset),
    limit: String(memState.limit),
  });
  if (memState.source) params.set("source", memState.source);

  try {
    const data = await api(`/memories?${params}`);
    memState.total = data.total || 0;
    renderMemoriesList(data.memories || []);
    renderMemoriesPagination();
  } catch (err) {
    panel.innerHTML = `<div class="empty-state">${escHtml(err.message)}</div>`;
  }
}

function renderMemoriesList(memories) {
  const panel = document.getElementById("memoriesListPanel");
  panel.innerHTML = "";

  if (!memories.length) {
    panel.innerHTML = '<div class="empty-state">No memories found.</div>';
    return;
  }

  memories.forEach(mem => {
    const item = h("div", {
      class: `memory-item ${memState.selected === mem.id ? 'active' : ''}`,
      onclick: () => selectMemory(mem),
    });
    item.innerHTML = `
      <div class="memory-item-header">
        <span class="memory-item-source">${escHtml(mem.source || "(no source)")}</span>
        <span class="memory-item-id">#${mem.id}</span>
      </div>
      <p class="memory-item-text">${escHtml(mem.text.substring(0, 120))}${mem.text.length > 120 ? "..." : ""}</p>
    `;
    panel.appendChild(item);
  });
}

function selectMemory(mem) {
  memState.selected = mem.id;
  // Highlight active item
  document.querySelectorAll(".memory-item").forEach(el => {
    el.classList.toggle("active", el.querySelector(".memory-item-id")?.textContent === `#${mem.id}`);
  });

  const detail = document.getElementById("memoriesDetailPanel");
  detail.innerHTML = `
    <div class="detail-header">
      <h3 class="detail-source">${escHtml(mem.source || "(no source)")}</h3>
      <span class="detail-id">#${mem.id}</span>
    </div>
    <div class="detail-text">${escHtml(mem.text)}</div>
    <div class="detail-meta">
      <div class="meta-item"><span class="meta-label">ID</span><span class="meta-value">${mem.id}</span></div>
      <div class="meta-item"><span class="meta-label">Source</span><span class="meta-value">${escHtml(mem.source || "—")}</span></div>
      <div class="meta-item"><span class="meta-label">Created</span><span class="meta-value">${mem.created_at || "—"}</span></div>
    </div>
    <div class="detail-actions">
      <button class="btn btn-danger btn-sm" onclick="deleteMemory(${mem.id})">Delete</button>
    </div>
  `;
}

async function deleteMemory(id) {
  if (!confirm(`Delete memory #${id}?`)) return;
  try {
    await api(`/memory/${id}`, { method: "DELETE" });
    memState.selected = null;
    loadMemoriesList();
    const detail = document.getElementById("memoriesDetailPanel");
    if (detail) detail.innerHTML = '<div class="empty-state">Memory deleted.</div>';
  } catch (err) {
    alert("Delete failed: " + err.message);
  }
}

// Make deleteMemory accessible from inline onclick
window.deleteMemory = deleteMemory;

function renderMemoriesPagination() {
  const pag = document.getElementById("memoriesPagination");
  if (!pag) return;
  const hasPrev = memState.offset > 0;
  const hasNext = memState.offset + memState.limit < memState.total;

  pag.innerHTML = `
    <span class="pag-info">${memState.offset + 1}–${Math.min(memState.offset + memState.limit, memState.total)} of ${memState.total}</span>
    <button class="btn btn-sm" ${hasPrev ? "" : "disabled"} id="memPrev">Previous</button>
    <button class="btn btn-sm" ${hasNext ? "" : "disabled"} id="memNext">Next</button>
  `;
  document.getElementById("memPrev")?.addEventListener("click", () => {
    memState.offset = Math.max(0, memState.offset - memState.limit);
    loadMemoriesList();
  });
  document.getElementById("memNext")?.addEventListener("click", () => {
    memState.offset += memState.limit;
    loadMemoriesList();
  });
}
```

**Step 2: Add memories page CSS**

```css
/* -- Filter bar -------------------------------------------------------------- */
.filter-bar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
.filter-input {
  flex: 1; padding: 8px 12px; background: var(--color-bg-elevated);
  border: 1px solid var(--color-border); border-radius: var(--radius-md);
  color: var(--color-text); font-size: 0.85rem;
}
.view-toggle { display: flex; gap: 2px; background: var(--color-bg-surface); border-radius: var(--radius-md); padding: 2px; }
.view-btn {
  padding: 6px 12px; border: none; background: transparent;
  color: var(--color-text-muted); font-size: 0.78rem; cursor: pointer;
  border-radius: var(--radius-sm);
}
.view-btn.active { background: var(--color-bg-elevated); color: var(--color-text); }

/* -- Memories layout --------------------------------------------------------- */
.memories-layout { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; min-height: 500px; }
.memories-list-panel {
  background: var(--color-bg-elevated); border: 1px solid var(--color-border);
  border-radius: var(--radius-lg); padding: 8px; overflow-y: auto; max-height: 70vh;
}
.memories-detail-panel {
  background: var(--color-bg-elevated); border: 1px solid var(--color-border);
  border-radius: var(--radius-lg); padding: 20px; overflow-y: auto; max-height: 70vh;
}

/* -- Memory list item -------------------------------------------------------- */
.memory-item {
  padding: 10px 12px; border-radius: var(--radius-md); cursor: pointer;
  transition: background 120ms ease; border-left: 3px solid transparent;
}
.memory-item:hover { background: rgba(212,175,55,0.04); }
.memory-item.active { background: rgba(212,175,55,0.08); border-left-color: var(--color-primary); }
.memory-item-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
.memory-item-source { font-size: 0.8rem; font-weight: 600; color: var(--color-primary); }
.memory-item-id { font-size: 0.75rem; color: var(--color-text-faint); font-family: monospace; }
.memory-item-text { font-size: 0.85rem; color: var(--color-text-muted); line-height: 1.4; margin: 0; }

/* -- Detail panel ------------------------------------------------------------ */
.detail-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 16px; }
.detail-source { font-size: 1rem; font-weight: 600; margin: 0; }
.detail-id { font-size: 0.8rem; color: var(--color-text-faint); font-family: monospace; }
.detail-text { font-size: 0.92rem; line-height: 1.6; white-space: pre-wrap; margin-bottom: 20px; }
.detail-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }
.meta-item { background: var(--color-bg-surface); border-radius: var(--radius-md); padding: 10px; }
.meta-label { display: block; font-size: 0.72rem; color: var(--color-text-muted); text-transform: uppercase; letter-spacing: 0.06em; }
.meta-value { display: block; margin-top: 2px; font-size: 0.88rem; }
.detail-actions { display: flex; gap: 8px; }

/* -- Pagination -------------------------------------------------------------- */
.pagination { display: flex; align-items: center; gap: 12px; margin-top: 16px; justify-content: flex-end; }
.pag-info { font-size: 0.82rem; color: var(--color-text-muted); }
```

**Step 3: Verify memories page**

Run: Navigate to `http://localhost:8900/ui#/memories`
Expected: Source filter + List/Grid toggle at top. Split panel — left shows paginated memory list with source, id, truncated text. Clicking a memory shows full detail on right with metadata and delete button. Pagination at bottom.

**Step 4: Commit**

```bash
git add webui/app.js webui/styles.css
git commit -m "feat(ui): add memories page with list+detail view and pagination"
```

---

## Task 6: Memories Page — Grid View + Search

**Files:**
- Modify: `webui/app.js`

**Step 1: Add grid view rendering**

Add `renderMemoriesContent()` function that switches between list and grid based on `memState.view`. In grid mode, show memory cards in a responsive grid (no detail panel — cards show full text).

**Step 2: Wire global search to memories page**

When user types in the global search input and presses Enter:
- If not on memories page, navigate to `#/memories`
- Call `POST /search` with the query
- Render results with similarity scores in the list

**Step 3: Verify grid view and search**

Run: Toggle to Grid view, verify cards render. Type a search query, verify results show with scores.

**Step 4: Commit**

```bash
git add webui/app.js
git commit -m "feat(ui): add grid view toggle and global search for memories"
```

---

## Task 7: Extractions Page

**Files:**
- Modify: `webui/app.js`
- Modify: `webui/styles.css`

**Step 1: Implement extractions page**

Add to `app.js`:

```javascript
registerPage("extractions", async (container) => {
  container.innerHTML = '<p class="loading">Loading extractions...</p>';

  try {
    const [status, usage] = await Promise.all([
      api("/extract/status").catch(() => null),
      api("/usage?period=7d").catch(() => null),
    ]);

    container.innerHTML = "";

    // Stat cards
    const grid = h("div", { class: "stat-grid" });
    const ext = usage?.extraction || {};

    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, formatNumber(ext.total_calls || 0)),
      h("div", { class: "stat-label" }, "Jobs (7d)"),
    ));

    const successRate = ext.total_calls ? ((ext.successful_calls || ext.total_calls) / ext.total_calls * 100).toFixed(0) : "—";
    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, typeof successRate === "string" ? successRate : successRate + "%"),
      h("div", { class: "stat-label" }, "Success Rate"),
    ));

    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, status?.enabled ? "Active" : "Inactive"),
      h("div", { class: "stat-label" }, "Status"),
    ));

    grid.appendChild(h("div", { class: "stat-card" },
      h("div", { class: "stat-value" }, escHtml(status?.provider || "—")),
      h("div", { class: "stat-label" }, "Provider"),
    ));

    container.appendChild(grid);

    // Extraction config details
    if (status) {
      const config = h("div", { style: "margin-top: 24px;" });
      config.appendChild(h("h2", { class: "section-title" }, "Configuration"));
      const infoGrid = h("div", { class: "info-grid" });
      infoGrid.innerHTML = `
        <div class="info-item"><span class="info-label">Provider</span><span class="info-value">${escHtml(status.provider || "—")}</span></div>
        <div class="info-item"><span class="info-label">Model</span><span class="info-value">${escHtml(status.model || "—")}</span></div>
        <div class="info-item"><span class="info-label">Enabled</span><span class="info-value">${status.enabled ? '<span class="badge badge-success">Yes</span>' : '<span class="badge badge-error">No</span>'}</span></div>
      `;
      config.appendChild(infoGrid);
      container.appendChild(config);
    }

    // Usage breakdown (tokens by model)
    if (ext.by_model && Object.keys(ext.by_model).length > 0) {
      const tokenSection = h("div", { style: "margin-top: 24px;" });
      tokenSection.appendChild(h("h2", { class: "section-title" }, "Token Usage by Model"));

      let tableHtml = '<table class="data-table"><thead><tr><th>Model</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th></tr></thead><tbody>';
      for (const [model, data] of Object.entries(ext.by_model)) {
        tableHtml += `<tr><td>${escHtml(model)}</td><td>${data.calls || 0}</td><td>${formatNumber(data.input_tokens || 0)}</td><td>${formatNumber(data.output_tokens || 0)}</td></tr>`;
      }
      tableHtml += '</tbody></table>';

      const wrap = h("div", { class: "table-wrap", html: tableHtml });
      tokenSection.appendChild(wrap);
      container.appendChild(tokenSection);
    }

  } catch (err) {
    container.innerHTML = `<div class="empty-state">Failed to load extractions: ${escHtml(err.message)}</div>`;
  }
});
```

**Step 2: Add table-wrap CSS**

```css
.table-wrap { overflow-x: auto; background: var(--color-bg-elevated); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 4px; }
```

**Step 3: Verify extractions page**

Run: Navigate to `http://localhost:8900/ui#/extractions`
Expected: 4 stat cards, config details, token usage table (if extraction is configured).

**Step 4: Commit**

```bash
git add webui/app.js webui/styles.css
git commit -m "feat(ui): add extractions page with stats, config, and token usage"
```

---

## Task 8: API Keys Page

**Files:**
- Modify: `webui/app.js`
- Modify: `webui/styles.css`

**Step 1: Implement API keys management page**

Note: The backend currently has a single API key via env var (`API_KEY`). The keys page will show the current auth status and provide the API key input (moving it from the old control panel). Full multi-key CRUD will be built when the backend adds key management endpoints.

```javascript
registerPage("keys", (container) => {
  container.innerHTML = "";

  const section = h("div");
  section.appendChild(h("h2", { class: "section-title" }, "API Key Configuration"));

  // Current key input
  const keyForm = h("div", { class: "key-form" });
  keyForm.innerHTML = `
    <p class="key-description">Enter your API key to authenticate requests. The key is stored in session storage and sent as the X-API-Key header.</p>
    <div class="key-input-row">
      <input type="password" id="apiKeyInput" class="filter-input" placeholder="Enter API key..." value="${escHtml(state.apiKey)}" style="flex:1;">
      <button class="btn btn-primary" id="saveKeyBtn">Save Key</button>
      <button class="btn" id="clearKeyBtn">Clear</button>
    </div>
    <p class="key-status" id="keyStatus"></p>
  `;
  section.appendChild(keyForm);
  container.appendChild(section);

  // Bind events
  document.getElementById("saveKeyBtn").addEventListener("click", async () => {
    const input = document.getElementById("apiKeyInput");
    state.apiKey = input.value.trim();
    sessionStorage.setItem(API_KEY_STORAGE, state.apiKey);
    // Test the key
    try {
      await api("/health");
      document.getElementById("keyStatus").innerHTML = '<span class="badge badge-success">Connected</span> Key is valid.';
    } catch (err) {
      document.getElementById("keyStatus").innerHTML = '<span class="badge badge-error">Error</span> ' + escHtml(err.message);
    }
  });

  document.getElementById("clearKeyBtn").addEventListener("click", () => {
    state.apiKey = "";
    sessionStorage.removeItem(API_KEY_STORAGE);
    document.getElementById("apiKeyInput").value = "";
    document.getElementById("keyStatus").textContent = "Key cleared.";
  });

  // Placeholder for future multi-key management
  const future = h("div", { style: "margin-top: 32px;" });
  future.innerHTML = `
    <h2 class="section-title">Key Management</h2>
    <div class="empty-state">
      Multi-key management (create, revoke, permissions) will be available when the backend adds key management endpoints.<br>
      Currently, the server uses a single API key configured via the <code>API_KEY</code> environment variable.
    </div>
  `;
  container.appendChild(future);
});
```

**Step 2: Add key form CSS**

```css
.key-form { background: var(--color-bg-elevated); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 20px; }
.key-description { font-size: 0.85rem; color: var(--color-text-muted); margin-bottom: 12px; }
.key-input-row { display: flex; gap: 8px; align-items: center; }
.key-status { font-size: 0.85rem; margin-top: 8px; }
```

**Step 3: Verify keys page**

Run: Navigate to `http://localhost:8900/ui#/keys`
Expected: API key input with Save/Clear buttons, status indicator, future multi-key placeholder.

**Step 4: Commit**

```bash
git add webui/app.js webui/styles.css
git commit -m "feat(ui): add API keys page with key input and auth testing"
```

---

## Task 9: Settings Page

**Files:**
- Modify: `webui/app.js`
- Modify: `webui/styles.css`

**Step 1: Implement settings page**

```javascript
registerPage("settings", async (container) => {
  container.innerHTML = '<p class="loading">Loading settings...</p>';

  try {
    const [stats, extractStatus, metrics] = await Promise.all([
      api("/stats"),
      api("/extract/status").catch(() => null),
      api("/metrics").catch(() => null),
    ]);

    container.innerHTML = "";

    // Extraction Provider
    if (extractStatus) {
      const section = h("div", { class: "settings-section" });
      section.appendChild(h("h2", { class: "section-title" }, "Extraction Provider"));
      const grid = h("div", { class: "info-grid" });
      grid.innerHTML = `
        <div class="info-item"><span class="info-label">Provider</span><span class="info-value">${escHtml(extractStatus.provider || "—")}</span></div>
        <div class="info-item"><span class="info-label">Model</span><span class="info-value">${escHtml(extractStatus.model || "—")}</span></div>
        <div class="info-item"><span class="info-label">Status</span><span class="info-value">${extractStatus.enabled ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-error">Disabled</span>'}</span></div>
      `;
      section.appendChild(grid);
      container.appendChild(section);
    }

    // Server Info
    const serverSection = h("div", { class: "settings-section" });
    serverSection.appendChild(h("h2", { class: "section-title" }, "Server Info"));
    const serverGrid = h("div", { class: "info-grid" });
    serverGrid.innerHTML = `
      <div class="info-item"><span class="info-label">Embedder</span><span class="info-value">${escHtml(stats.model || "—")}</span></div>
      <div class="info-item"><span class="info-label">Index Size</span><span class="info-value">${((stats.index_size_bytes || 0) / 1024).toFixed(0)} KB (${stats.total_memories} memories)</span></div>
      <div class="info-item"><span class="info-label">Backups</span><span class="info-value">${stats.backup_count || 0}</span></div>
      <div class="info-item"><span class="info-label">Auto-Reload</span><span class="info-value">${metrics?.embedder_reload?.enabled ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-info">Disabled</span>'}</span></div>
    `;
    serverSection.appendChild(serverGrid);
    container.appendChild(serverSection);

    // Appearance
    const themeSection = h("div", { class: "settings-section" });
    themeSection.appendChild(h("h2", { class: "section-title" }, "Appearance"));
    themeSection.innerHTML += `
      <p style="font-size: 0.85rem; color: var(--color-text-muted); margin-bottom: 12px;">
        Theme preference is also available in the sidebar footer. Changes apply immediately.
      </p>
      <div class="theme-switcher-large" id="themeSettingsSwitcher">
        <button class="theme-btn-lg" data-theme="system">System</button>
        <button class="theme-btn-lg" data-theme="dark">Dark</button>
        <button class="theme-btn-lg" data-theme="light">Light</button>
      </div>
    `;
    container.appendChild(themeSection);

    // Sync theme buttons with current state
    const currentTheme = localStorage.getItem(THEME_STORAGE) || "system";
    themeSection.querySelectorAll(".theme-btn-lg").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.theme === currentTheme);
    });
    document.getElementById("themeSettingsSwitcher").addEventListener("click", (e) => {
      const btn = e.target.closest(".theme-btn-lg");
      if (!btn) return;
      // Reuse the sidebar theme switcher logic
      document.querySelector(`.theme-btn[data-theme="${btn.dataset.theme}"]`)?.click();
      themeSection.querySelectorAll(".theme-btn-lg").forEach(b => {
        b.classList.toggle("active", b.dataset.theme === btn.dataset.theme);
      });
    });

    // Danger Zone
    const dangerSection = h("div", { class: "settings-section danger-zone" });
    dangerSection.appendChild(h("h2", { class: "section-title", style: "color: var(--color-error);" }, "Danger Zone"));
    dangerSection.innerHTML += `
      <p style="font-size: 0.85rem; color: var(--color-text-muted); margin-bottom: 12px;">
        These actions are irreversible. Proceed with caution.
      </p>
      <div style="display: flex; gap: 12px;">
        <button class="btn" id="exportBtn">Export All Memories</button>
        <button class="btn btn-danger" id="rebuildBtn">Rebuild Index</button>
      </div>
    `;
    container.appendChild(dangerSection);

    // Bind danger zone buttons
    document.getElementById("exportBtn").addEventListener("click", async () => {
      try {
        const data = await api("/memories?limit=10000");
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `memories-export-${new Date().toISOString().split("T")[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        alert("Export failed: " + err.message);
      }
    });

    document.getElementById("rebuildBtn").addEventListener("click", async () => {
      if (!confirm("Rebuild the entire index? This may take a while.")) return;
      try {
        await api("/index/build", { method: "POST" });
        alert("Index rebuild started.");
      } catch (err) {
        alert("Rebuild failed: " + err.message);
      }
    });

  } catch (err) {
    container.innerHTML = `<div class="empty-state">Failed to load settings: ${escHtml(err.message)}</div>`;
  }
});
```

**Step 2: Add settings CSS**

```css
.settings-section { margin-bottom: 32px; }
.danger-zone { border: 1px solid var(--color-error); border-radius: var(--radius-lg); padding: 20px; }
.theme-switcher-large { display: flex; gap: 8px; }
.theme-btn-lg {
  padding: 10px 24px; border: 1px solid var(--color-border); border-radius: var(--radius-md);
  background: var(--color-bg-elevated); color: var(--color-text-muted); cursor: pointer;
  font-size: 0.88rem; transition: all 120ms ease;
}
.theme-btn-lg.active { border-color: var(--color-primary); color: var(--color-primary); background: rgba(212,175,55,0.08); }
.theme-btn-lg:hover { border-color: var(--color-primary); }
```

**Step 3: Verify settings page**

Run: Navigate to `http://localhost:8900/ui#/settings`
Expected: Extraction provider info, server info, theme toggle with System/Dark/Light, danger zone with Export and Rebuild Index.

**Step 4: Commit**

```bash
git add webui/app.js webui/styles.css
git commit -m "feat(ui): add settings page with provider info, theme, and danger zone"
```

---

## Task 10: Responsive Layout + Mobile Sidebar

**Files:**
- Modify: `webui/styles.css`
- Modify: `webui/index.html`
- Modify: `webui/app.js`

**Step 1: Add responsive breakpoints**

Add to `styles.css`:

```css
@media (max-width: 768px) {
  .app { grid-template-columns: 1fr; }
  .sidebar {
    position: fixed; transform: translateX(-100%);
    transition: transform 200ms ease; width: 240px;
  }
  .sidebar.open { transform: translateX(0); }
  .main { grid-column: 1; }
  .topbar { padding: 0 16px; }
  .content { padding: 16px; }
  .memories-layout { grid-template-columns: 1fr; }
  .memories-detail-panel { display: none; }
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .info-grid { grid-template-columns: 1fr; }
}
```

**Step 2: Add hamburger menu button to topbar**

Add a hamburger button to `index.html` that shows on mobile and toggles `sidebar.open`.

**Step 3: Add JS for mobile sidebar toggle**

```javascript
const menuBtn = document.getElementById("menuBtn");
const sidebar = document.getElementById("sidebar");
if (menuBtn) {
  menuBtn.addEventListener("click", () => sidebar.classList.toggle("open"));
  // Close sidebar when nav item clicked (mobile)
  document.querySelectorAll(".nav-item").forEach(a => {
    a.addEventListener("click", () => sidebar.classList.remove("open"));
  });
}
```

**Step 4: Verify responsive**

Run: Resize browser to < 768px. Verify sidebar collapses, hamburger shows, memories detail panel hides on mobile.

**Step 5: Commit**

```bash
git add webui/styles.css webui/index.html webui/app.js
git commit -m "feat(ui): add responsive layout with collapsible sidebar for mobile"
```

---

## Task 11: Polish — Animations, Loading States, Error Handling

**Files:**
- Modify: `webui/styles.css`
- Modify: `webui/app.js`

**Step 1: Add subtle animations**

- Card rise-in animation (reuse from current styles.css)
- Page transition fade
- Stat card number count-up effect (optional — skip if complex)

**Step 2: Improve loading states**

Add skeleton loading indicators for stat cards and memory list while API calls are in progress.

**Step 3: Global error toast**

Add a simple toast notification system for API errors instead of `alert()`:

```javascript
function showToast(message, type = "error") {
  const toast = h("div", { class: `toast toast-${type}` }, message);
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add("visible"), 10);
  setTimeout(() => { toast.classList.remove("visible"); setTimeout(() => toast.remove(), 300); }, 4000);
}
```

**Step 4: Verify polish**

Run: Trigger various states — loading, error (wrong API key), empty (no memories). Verify all look clean.

**Step 5: Commit**

```bash
git add webui/styles.css webui/app.js
git commit -m "feat(ui): add animations, loading states, and toast notifications"
```

---

## Task 12: Update Backend Static File Serving

**Files:**
- Modify: `app.py` (lines 835-836, 1002-1008)

**Step 1: Verify existing serving still works**

The backend already serves `webui/` at `/ui/static` and `/ui` returns `index.html`. No backend changes needed unless the file structure changes.

Check: Static mount at `/ui/static` serves CSS/JS/favicon. `GET /ui` returns index.html.

**Step 2: Test the full flow**

Run:
```bash
# Rebuild Docker container with new webui files
docker compose down && docker compose up -d --build memories
# Wait for health
curl -s http://localhost:8900/health | jq .
# Open UI
open http://localhost:8900/ui
```

Expected: Full v2 UI loads with sidebar, all 5 pages functional, dark/light theme works.

**Step 3: Commit (if any backend changes needed)**

```bash
git add app.py
git commit -m "chore(ui): verify backend static file serving for v2 UI"
```

---

## Task 13: Update Documentation

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`

**Step 1: Update CHANGELOG**

Add under `[Unreleased]`:
```markdown
### Changed
- **Web UI v2**: Complete redesign with sidebar navigation, 5 pages (Dashboard, Memories, Extractions, API Keys, Settings), Arkos-inspired dark/light theme, list+detail memory view with grid toggle, and responsive mobile layout
```

**Step 2: Update README**

Update any references to the old UI. Add a brief section or update existing UI section to describe the new layout and features.

**Step 3: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "docs: update changelog and readme for web UI v2"
```

---

## Summary

| Task | Description | Est. |
|------|-------------|------|
| 1 | HTML shell + CSS tokens + Router | Core |
| 2 | Sidebar + Top bar polish | Core |
| 3 | Shared UI components | Core |
| 4 | Dashboard page | Feature |
| 5 | Memories page — list + detail | Feature |
| 6 | Memories — grid view + search | Feature |
| 7 | Extractions page | Feature |
| 8 | API Keys page | Feature |
| 9 | Settings page | Feature |
| 10 | Responsive + mobile sidebar | Polish |
| 11 | Animations + loading + toasts | Polish |
| 12 | Backend verification + Docker | Infra |
| 13 | Documentation | Docs |

13 tasks, each independently committable. Core tasks (1-3) must be done first. Feature tasks (4-9) can be done in any order. Polish (10-11) after features. Infra + docs last.
