const state = {
  offset: 0,
  limit: 20,
  source: "",
  total: 0,
  apiKey: localStorage.getItem("faiss_ui_api_key") || "",
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
};

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.classList.toggle("error", isError);
}

function authHeaders() {
  return state.apiKey ? { "X-API-Key": state.apiKey } : {};
}

function renderMemories(memories) {
  els.memoryList.innerHTML = "";

  if (!memories.length) {
    const notice = document.createElement("div");
    notice.className = "notice";
    notice.textContent = "No memories matched this filter.";
    els.memoryList.appendChild(notice);
    return;
  }

  memories.forEach((memory, idx) => {
    const frag = els.cardTemplate.content.cloneNode(true);
    frag.querySelector(".memory-id").textContent = `#${memory.id}`;
    frag.querySelector(".memory-source").textContent = memory.source || "(no source)";
    frag.querySelector(".memory-text").textContent = memory.text;

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
      headers: {
        ...authHeaders(),
      },
    });

    if (!resp.ok) {
      const detail = await resp.text();
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

function bindEvents() {
  els.apiKey.value = state.apiKey;

  els.apiKey.addEventListener("change", () => {
    state.apiKey = els.apiKey.value.trim();
    localStorage.setItem("faiss_ui_api_key", state.apiKey);
    state.offset = 0;
    loadMemories();
  });

  els.source.addEventListener("change", () => {
    state.source = els.source.value;
    state.offset = 0;
    loadMemories();
  });

  els.limit.addEventListener("change", () => {
    state.limit = Number(els.limit.value);
    state.offset = 0;
    loadMemories();
  });

  els.refresh.addEventListener("click", () => {
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
}

bindEvents();
loadMemories();
