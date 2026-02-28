// -- Theme toggle (runs first to avoid flash) --------------------------------

(function initTheme() {
  const stored = localStorage.getItem("theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = stored || (prefersDark ? "dark" : "light");
  document.documentElement.dataset.theme = theme;

  const btn = document.getElementById("themeToggle");
  if (btn) {
    btn.textContent = theme === "dark" ? "🌙" : "☀️";
    btn.addEventListener("click", () => {
      const current = document.documentElement.dataset.theme;
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      btn.textContent = next === "dark" ? "🌙" : "☀️";
      localStorage.setItem("theme", next);
    });
  }

  // Listen for OS theme changes (only when no manual override)
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    if (localStorage.getItem("theme")) return; // manual override active
    const auto = e.matches ? "dark" : "light";
    document.documentElement.dataset.theme = auto;
    if (btn) btn.textContent = auto === "dark" ? "🌙" : "☀️";
  });
})();

const state = {
  offset: 0,
  limit: 20,
  source: "",
  total: 0,
  apiKey: sessionStorage.getItem("faiss_ui_api_key") || "",
  activeFolder: null, // null = all, string = folder name
  folders: [],        // [{name, count}, ...]
  folderTotal: 0,
};

const els = {
  apiKey: document.getElementById("apiKey"),
  source: document.getElementById("source"),
  limit: document.getElementById("limit"),
  refresh: document.getElementById("refresh"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  status: document.getElementById("status"),
  memoryList: document.getElementById("memoryList"),
  visibleCount: document.getElementById("visibleCount"),
  totalCount: document.getElementById("totalCount"),
  offsetInput: document.getElementById("offsetInput"),
  cardTemplate: document.getElementById("memoryCardTemplate"),
  folderList: document.getElementById("folderList"),
  folderAllCount: document.getElementById("folderAllCount"),
  renameFolderModal: document.getElementById("renameFolderModal"),
  renameFolderInput: document.getElementById("renameFolderInput"),
  renameFolderConfirm: document.getElementById("renameFolderConfirm"),
  renameFolderCancel: document.getElementById("renameFolderCancel"),
};

let renamingFolder = null; // folder name being renamed

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.classList.toggle("error", isError);
}

function authHeaders() {
  return state.apiKey ? { "X-API-Key": state.apiKey } : {};
}

function syncApiKeyFromInput() {
  state.apiKey = (els.apiKey.value || "").trim();
  sessionStorage.setItem("faiss_ui_api_key", state.apiKey);
}

async function parseErrorDetail(resp) {
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") {
        return data.detail;
      }
      if (data.detail && typeof data.detail.message === "string") {
        return data.detail.message;
      }
      return JSON.stringify(data);
    } catch (_) {
      return `HTTP ${resp.status}`;
    }
  }
  return resp.text();
}

// -- Folder sidebar -----------------------------------------------------------

async function loadFolders() {
  try {
    const resp = await fetch("/folders", { headers: authHeaders() });
    if (!resp.ok) return;
    const data = await resp.json();
    state.folders = data.folders || [];
    state.folderTotal = data.total || 0;
    renderFolders();
  } catch (_) {
    // Silently fail — folders are supplementary
  }
}

function renderFolders() {
  els.folderList.innerHTML = "";

  // "All Memories" item
  const allLi = document.createElement("li");
  allLi.className = "folder-item" + (state.activeFolder === null ? " active" : "");
  allLi.dataset.folder = "";
  allLi.innerHTML = `
    <span class="folder-name">All Memories</span>
    <span class="folder-count">${state.folderTotal}</span>
  `;
  allLi.addEventListener("click", () => {
    state.activeFolder = null;
    state.source = "";
    els.source.value = "";
    state.offset = 0;
    renderFolders();
    loadMemories();
  });
  els.folderList.appendChild(allLi);

  // Individual folders
  state.folders.forEach((folder) => {
    const li = document.createElement("li");
    li.className = "folder-item" + (state.activeFolder === folder.name ? " active" : "");
    li.dataset.folder = folder.name;

    const nameSpan = document.createElement("span");
    nameSpan.className = "folder-name";
    nameSpan.textContent = folder.name;

    const countSpan = document.createElement("span");
    countSpan.className = "folder-count";
    countSpan.textContent = String(folder.count);

    const actionsSpan = document.createElement("span");
    actionsSpan.className = "folder-actions";

    const renameBtn = document.createElement("button");
    renameBtn.className = "folder-action-btn";
    renameBtn.textContent = "Rename";
    renameBtn.title = "Rename folder";
    renameBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openRenameModal(folder.name);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "folder-action-btn delete";
    deleteBtn.textContent = "Delete";
    deleteBtn.title = "Delete all memories in this folder";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteFolder(folder.name);
    });

    actionsSpan.appendChild(renameBtn);
    actionsSpan.appendChild(deleteBtn);

    li.appendChild(nameSpan);
    li.appendChild(countSpan);
    li.appendChild(actionsSpan);

    li.addEventListener("click", () => {
      state.activeFolder = folder.name;
      state.source = folder.name;
      els.source.value = folder.name;
      state.offset = 0;
      renderFolders();
      loadMemories();
    });

    els.folderList.appendChild(li);
  });
}

// -- Rename modal -------------------------------------------------------------

function openRenameModal(folderName) {
  renamingFolder = folderName;
  els.renameFolderInput.value = folderName;
  els.renameFolderModal.hidden = false;
  els.renameFolderInput.focus();
  els.renameFolderInput.select();
}

function closeRenameModal() {
  renamingFolder = null;
  els.renameFolderModal.hidden = true;
  els.renameFolderInput.value = "";
}

async function confirmRename() {
  const newName = els.renameFolderInput.value.trim();
  if (!newName || !renamingFolder || newName === renamingFolder) {
    closeRenameModal();
    return;
  }

  setStatus(`Renaming folder "${renamingFolder}" to "${newName}"...`);
  try {
    const resp = await fetch("/folders/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ old_name: renamingFolder, new_name: newName }),
    });
    if (!resp.ok) {
      const detail = await parseErrorDetail(resp);
      throw new Error(detail);
    }
    const data = await resp.json();
    setStatus(`Renamed: ${data.updated} memories updated.`);
    if (state.activeFolder === renamingFolder) {
      state.activeFolder = newName;
      state.source = newName;
      els.source.value = newName;
    }
    closeRenameModal();
    await loadFolders();
    await loadMemories();
  } catch (err) {
    setStatus(`Rename failed: ${err.message}`, true);
  }
}

// -- Delete folder ------------------------------------------------------------

async function deleteFolder(folderName) {
  if (!confirm(`Delete ALL memories in folder "${folderName}"? This cannot be undone.`)) {
    return;
  }

  setStatus(`Deleting folder "${folderName}"...`);
  try {
    const resp = await fetch("/memory/delete-by-prefix", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ source_prefix: folderName }),
    });
    if (!resp.ok) {
      const detail = await parseErrorDetail(resp);
      throw new Error(detail);
    }
    const data = await resp.json();
    setStatus(`Deleted folder "${folderName}": ${data.deleted || 0} memories removed.`);
    if (state.activeFolder === folderName) {
      state.activeFolder = null;
      state.source = "";
      els.source.value = "";
    }
    await loadFolders();
    state.offset = 0;
    await loadMemories();
  } catch (err) {
    setStatus(`Delete failed: ${err.message}`, true);
  }
}

// -- Move to folder -----------------------------------------------------------

async function moveToFolder(memoryId, newFolder) {
  if (!newFolder) return;

  setStatus(`Moving memory #${memoryId} to "${newFolder}"...`);
  try {
    const resp = await fetch(`/memory/${memoryId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ source: newFolder }),
    });
    if (!resp.ok) {
      const detail = await parseErrorDetail(resp);
      throw new Error(detail);
    }
    setStatus(`Moved memory #${memoryId} to "${newFolder}".`);
    await loadFolders();
    await loadMemories();
  } catch (err) {
    setStatus(`Move failed: ${err.message}`, true);
  }
}

// -- Render memories ----------------------------------------------------------

function renderMemories(memories) {
  els.memoryList.innerHTML = "";

  if (!memories.length) {
    const notice = document.createElement("div");
    notice.className = "notice";
    notice.textContent = "No memories matched this filter.";
    els.memoryList.appendChild(notice);
    return;
  }

  memories.forEach((mem, idx) => {
    const frag = els.cardTemplate.content.cloneNode(true);
    frag.querySelector(".memory-id").textContent = `#${mem.id}`;
    frag.querySelector(".memory-source").textContent = mem.source || "(no source)";
    frag.querySelector(".memory-text").textContent = mem.text;

    // Populate the move-to-folder dropdown
    const moveSelect = frag.querySelector(".move-folder-select");
    state.folders.forEach((folder) => {
      const opt = document.createElement("option");
      opt.value = folder.name;
      opt.textContent = folder.name;
      moveSelect.appendChild(opt);
    });
    moveSelect.addEventListener("change", () => {
      if (moveSelect.value) {
        moveToFolder(mem.id, moveSelect.value);
      }
    });

    const card = frag.querySelector(".memory-card");
    card.style.animationDelay = `${idx * 14}ms`;
    els.memoryList.appendChild(frag);
  });
}

function updatePagingState(visibleCount) {
  els.visibleCount.textContent = String(visibleCount);
  els.totalCount.textContent = String(state.total);
  els.offsetInput.value = state.offset;
  els.offsetInput.max = Math.max(0, state.total - 1);

  els.prev.disabled = state.offset <= 0;
  els.next.disabled = state.offset + visibleCount >= state.total;
}

async function loadMemories() {
  syncApiKeyFromInput();

  const params = new URLSearchParams({
    offset: String(state.offset),
    limit: String(state.limit),
  });

  if (state.source.trim()) {
    params.set("source", state.source.trim());
  }

  setStatus("Loading memories...");

  try {
    const resp = await fetch(`/memories?${params.toString()}`, {
      headers: authHeaders(),
    });

    if (!resp.ok) {
      const detail = await parseErrorDetail(resp);
      throw new Error(`Request failed (${resp.status}): ${detail}`);
    }

    const data = await resp.json();
    const memories = data.memories || [];
    state.total = data.total || 0;

    renderMemories(memories);
    updatePagingState(memories.length);
    setStatus(`Loaded ${memories.length} memories.`);
  } catch (err) {
    renderMemories([]);
    updatePagingState(0);
    setStatus(err.message || "Failed to load memories.", true);
  }
}

// -- Event bindings -----------------------------------------------------------

function bindEvents() {
  els.apiKey.value = state.apiKey;

  els.apiKey.addEventListener("input", () => {
    syncApiKeyFromInput();
  });

  els.apiKey.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    state.offset = 0;
    loadFolders();
    loadMemories();
  });

  els.source.addEventListener("change", () => {
    state.source = els.source.value;
    state.activeFolder = null; // manual source filter clears folder selection
    state.offset = 0;
    renderFolders();
    loadMemories();
  });

  els.limit.addEventListener("change", () => {
    state.limit = Number(els.limit.value);
    state.offset = 0;
    loadMemories();
  });

  els.offsetInput.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const val = Math.max(0, Math.min(parseInt(els.offsetInput.value, 10) || 0, state.total - 1));
    state.offset = val;
    loadMemories();
  });

  els.refresh.addEventListener("click", () => {
    loadFolders();
    loadMemories();
  });

  els.prev.addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadMemories();
  });

  els.next.addEventListener("click", () => {
    state.offset += state.limit;
    loadMemories();
  });

  // Rename modal
  els.renameFolderConfirm.addEventListener("click", confirmRename);
  els.renameFolderCancel.addEventListener("click", closeRenameModal);
  els.renameFolderInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") confirmRename();
    if (e.key === "Escape") closeRenameModal();
  });
  els.renameFolderModal.addEventListener("click", (e) => {
    if (e.target === els.renameFolderModal) closeRenameModal();
  });
}

// -- Tab navigation -----------------------------------------------------------

const tabBtns = document.querySelectorAll(".tab-btn");
const tabContents = document.querySelectorAll(".tab-content");

tabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabBtns.forEach((b) => b.classList.remove("active"));
    tabContents.forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    const target = document.getElementById(
      btn.dataset.tab === "memories" ? "tabMemories" : "tabUsage"
    );
    if (target) target.classList.add("active");

    if (btn.dataset.tab === "usage" && !usageLoaded) {
      loadUsage();
    }
  });
});

// -- Usage dashboard ----------------------------------------------------------

let usageLoaded = false;

const usageEls = {
  period: document.getElementById("usagePeriod"),
  refresh: document.getElementById("usageRefresh"),
  status: document.getElementById("usageStatus"),
  totalOps: document.getElementById("usageTotalOps"),
  extractCalls: document.getElementById("usageExtractCalls"),
  estCost: document.getElementById("usageEstCost"),
  opsTable: document.getElementById("usageOpsTable"),
  tokensTable: document.getElementById("usageTokensTable"),
};

function setUsageStatus(msg, isError = false) {
  usageEls.status.textContent = msg;
  usageEls.status.classList.toggle("error", isError);
}

function formatNumber(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function renderOpsTable(operations) {
  if (!operations || Object.keys(operations).length === 0) {
    usageEls.opsTable.innerHTML = '<p class="notice">No operations recorded.</p>';
    return;
  }

  let html = '<table class="usage-table"><thead><tr><th>Operation</th><th>Count</th><th>Sources</th></tr></thead><tbody>';
  for (const [op, data] of Object.entries(operations)) {
    const sources = data.by_source || {};
    const sourceList = Object.entries(sources)
      .sort((a, b) => b[1] - a[1])
      .map(([s, c]) => `<span class="source-tag">${escHtml(s)} <b>${c}</b></span>`)
      .join(" ");
    html += `<tr><td class="op-name">${escHtml(op)}</td><td class="op-count">${formatNumber(data.total || 0)}</td><td class="op-sources">${sourceList || '<span class="text-soft">—</span>'}</td></tr>`;
  }
  html += "</tbody></table>";
  usageEls.opsTable.innerHTML = html;
}

function renderTokensTable(extraction) {
  if (!extraction || extraction.total_calls === 0) {
    usageEls.tokensTable.innerHTML = '<p class="notice">No extraction calls recorded.</p>';
    return;
  }

  const byModel = extraction.by_model || {};
  let html = '<table class="usage-table"><thead><tr><th>Model</th><th>Calls</th><th>Input Tokens</th><th>Output Tokens</th></tr></thead><tbody>';
  for (const [model, data] of Object.entries(byModel)) {
    html += `<tr><td class="model-name">${escHtml(model)}</td><td>${data.calls || 0}</td><td>${formatNumber(data.input_tokens || 0)}</td><td>${formatNumber(data.output_tokens || 0)}</td></tr>`;
  }
  html += "</tbody></table>";
  usageEls.tokensTable.innerHTML = html;
}

function escHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

async function loadUsage() {
  syncApiKeyFromInput();
  const period = usageEls.period.value;
  setUsageStatus("Loading usage data...");

  try {
    const resp = await fetch(`/usage?period=${period}`, {
      headers: authHeaders(),
    });

    if (!resp.ok) {
      const detail = await parseErrorDetail(resp);
      throw new Error(`Request failed (${resp.status}): ${detail}`);
    }

    const data = await resp.json();

    if (data.enabled === false) {
      setUsageStatus("Usage tracking is disabled. Set USAGE_TRACKING=true to enable.", true);
      usageEls.totalOps.textContent = "—";
      usageEls.extractCalls.textContent = "—";
      usageEls.estCost.textContent = "—";
      usageEls.opsTable.innerHTML = '<p class="notice">Usage tracking is disabled on this server.</p>';
      usageEls.tokensTable.innerHTML = '<p class="notice">Usage tracking is disabled on this server.</p>';
      return;
    }

    // Summary metrics
    const ops = data.operations || {};
    let totalOps = 0;
    for (const v of Object.values(ops)) totalOps += v.total || 0;
    usageEls.totalOps.textContent = formatNumber(totalOps);

    const extraction = data.extraction || {};
    usageEls.extractCalls.textContent = formatNumber(extraction.total_calls || 0);
    const cost = extraction.estimated_cost_usd || 0;
    usageEls.estCost.textContent = cost < 0.01 && cost > 0
      ? `$${cost.toFixed(4)}`
      : `$${cost.toFixed(2)}`;

    renderOpsTable(ops);
    renderTokensTable(extraction);

    usageLoaded = true;
    setUsageStatus(`Loaded usage data for period: ${period}`);
  } catch (err) {
    setUsageStatus(err.message || "Failed to load usage data.", true);
  }
}

usageEls.refresh.addEventListener("click", loadUsage);
usageEls.period.addEventListener("change", loadUsage);

bindEvents();
loadFolders();
loadMemories();
