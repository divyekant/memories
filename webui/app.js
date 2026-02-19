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
  offsetValue: document.getElementById("offsetValue"),
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
  els.offsetValue.textContent = String(state.offset);

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

bindEvents();
loadFolders();
loadMemories();
