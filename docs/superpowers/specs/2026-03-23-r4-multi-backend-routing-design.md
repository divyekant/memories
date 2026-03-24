# R4: Multi-Backend Routing — Design Spec

## Context

A single agent session currently talks to one Memories instance. The hooks hardcode `MEMORIES_URL` + `MEMORIES_API_KEY`, and the MCP server connects to one endpoint. This limits operators who run multiple instances (dev + prod, personal + shared, project-specific stores).

**Product thesis:** The agent should intelligently route memory operations across multiple backends based on context — which project, what kind of fact, what session intent. The engine doesn't change; routing is a client-layer concern.

**Scale context:** Single operator with two deployments (Mac Mini prod at memory.divyekant.com, localhost dev). Multi-agent access to a single instance is already solved via prefix-scoped API keys.

---

## Design

### Config Format

Location: `~/.config/memories/backends.yaml` (global) or `.memories/backends.yaml` (per-project override).

Three configuration tiers:

#### Tier 1: Scenario-Based (most users)

```yaml
backends:
  local:
    url: http://localhost:8900
    api_key: god-is-an-astronaut
    scenario: dev
  prod:
    url: https://memory.divyekant.com
    api_key: god-is-an-astronaut
    scenario: prod
```

Routing rules auto-derived from scenario combination. No `routing:` section needed.

#### Tier 2: Scenario + Overrides

```yaml
backends:
  local:
    url: http://localhost:8900
    api_key: god-is-an-astronaut
    scenario: dev
  prod:
    url: https://memory.divyekant.com
    api_key: god-is-an-astronaut
    scenario: prod

routing:
  extract: [local]         # override: extractions only to dev
  add: [local, prod]       # explicit: manual adds go to both
```

#### Tier 3: DIY (power users)

```yaml
backends:
  alpha:
    url: http://alpha:8900
    api_key: key-a
  beta:
    url: http://beta:8900
    api_key: key-b
  archive:
    url: http://archive:8900
    api_key: key-c

routing:
  search: [alpha, beta]
  extract: [alpha]
  add: [alpha, beta]
  feedback: [alpha]
  # archive is search-only, never written to by agents
```

No `scenario:` field = DIY mode. All routing must be explicit.

### Scenario Routing Matrix

| Combo | Search | Extract | Add | Feedback |
|-------|--------|---------|-----|----------|
| **single** (any) | that one | that one | that one | that one |
| **dev + prod** | both, merge | dev only | both | dev only |
| **personal + shared** | both, merge | personal | decisions→shared, rest→personal | personal |
| **dev + archive** | both, merge | dev only | dev only | dev only |
| **prod + archive** | both, merge | prod only | prod only | prod only |

For `personal + shared`, the "decisions→shared" routing requires the agent to classify the content type. This is handled via the agent instruction/skill, not the config.

### Fallback Behavior

- If `backends.yaml` doesn't exist: fall back to env vars (`MEMORIES_URL`, `MEMORIES_API_KEY`) — single-backend mode, fully backward compatible
- If a backend is unreachable: skip it, log warning, continue with remaining backends
- If all backends are unreachable: error, same as today

---

## Implementation Layers

### Layer 1: Config Loader (`integrations/claude-code/hooks/_lib.sh`)

New function in the shared hook library:

```bash
_load_backends() {
  # 1. Check per-project: $CWD/.memories/backends.yaml
  # 2. Fall back to global: ~/.config/memories/backends.yaml
  # 3. Fall back to env vars: MEMORIES_URL + MEMORIES_API_KEY (single backend)
  # Returns: JSON array of {name, url, api_key, scenario} + routing rules
}

_get_backends_for_op() {
  # Given operation (search|extract|add|feedback), return list of backends
  # Reads routing rules, resolves scenario defaults if no explicit routing
}
```

All hooks already source `_lib.sh`. Adding these functions makes multi-backend available to every hook automatically.

### Layer 2: Hook Routing (`memory-*.sh`)

**memory-recall.sh (SessionStart):**
- Search: fan out to all `search` backends
- Merge results by deduplicating on text similarity (>0.95 = same memory)
- Sort merged results by best score across backends
- Inject as usual

**memory-query.sh (UserPromptSubmit):**
- Same fan-out + merge pattern as recall

**memory-extract.sh (Stop) / memory-flush.sh (PreCompact) / memory-commit.sh (SessionEnd):**
- Route to `extract` backends per routing rules
- Fire-and-forget (async) — same as today, just to multiple URLs

### Layer 3: MCP Server Routing (`mcp-server/index.js`)

The MCP server needs to support multiple backends. Two approaches:

**Option A: Single MCP entry, proxy routing (Recommended)**
- One MCP server instance reads `backends.yaml`
- Each tool call routes to the appropriate backend(s) per operation type
- Search merges results transparently
- Agent sees one set of tools, routing is invisible

**Option B: Multiple MCP entries with namespaced tools**
- Register separate MCP entries: `memories-local`, `memories-prod`
- Agent sees `mcp__memories-local__memory_search` and `mcp__memories-prod__memory_search`
- Agent must decide which to call (more control, more cognitive load)

Going with **Option A** — the MCP server becomes a thin routing proxy. The agent doesn't need to think about backends; it calls `memory_search` and gets merged results.

**MCP server changes:**
- New dependency: `js-yaml` for YAML parsing (add to `mcp-server/package.json`)
- On startup: load `backends.yaml` (same resolution: project → global → env fallback)
- `memory_search`: fan out to all `search` backends via `Promise.all()`, merge results, deduplicate
- `memory_add`: route to all `add` backends
- `memory_extract`: route to all `extract` backends, poll first to complete
- Other tools: route to primary backend (first in list)

### Layer 4: Search Merge Logic

When searching multiple backends:

1. Fan out: POST /search to each backend in **parallel**
   - Hooks: use background subshells (`curl ... &`) + `wait` to parallelize
   - MCP server: use `Promise.all()` for native parallel fetch
   - This keeps total latency = max(backend latencies), not sum
2. Collect results from all backends
3. Tag each result with `_backend: "name"` so the agent knows provenance
4. Deduplicate: **exact text match** (hash-based) for cross-backend dedup. Avoids similarity threshold issues when backends run different embedding model versions. If texts match exactly, keep the result with higher score.
5. Sort by best RRF/similarity score
6. Return merged list with `_backend` tags

At ~9,500 memories per instance and k=5, this is 2 parallel HTTP calls returning 5 results each — negligible overhead.

**Latency budget:** Search hooks have 3s timeout. With parallel fan-out, 2 backends at ~50ms each stays well within budget. The `wait` pattern handles partial results — if one backend times out, results from the other are still returned.

### Layer 5: Agent Instructions

Update the Memories skill (`skills/memories/`) to include multi-backend awareness:

```markdown
## Multi-Backend Routing

This session may have multiple Memories backends configured. Memory operations
are routed automatically based on the config:

- **Search** fans out to all search backends and merges results
- **Extract** writes to configured extract backends
- **Add** writes to configured add backends

When you see `_backend` tags on search results, that tells you which instance
the memory came from. You don't need to route manually — the system handles it.

For personal+shared scenarios: decisions and architectural choices should use
source prefixes that the shared backend can access (e.g., `decision/{project}`).
Personal notes use `personal/{project}` which stays on the personal backend.
```

---

## Config Resolution Order

1. Per-project: `$CWD/.memories/backends.yaml`
2. Global: `~/.config/memories/backends.yaml`
3. Env vars: `MEMORIES_URL` + `MEMORIES_API_KEY` (single-backend fallback)
4. Default: `http://localhost:8900` with no key (local-only, no auth)

If per-project config exists, it **replaces** global (no merge). This keeps it simple — one file active at a time.

**API key security:** Config files support env var interpolation for credentials:
```yaml
backends:
  prod:
    url: https://memory.divyekant.com
    api_key: ${MEMORIES_PROD_KEY}   # resolved from environment at load time
```

The config loader resolves `${VAR_NAME}` patterns before parsing backend entries. This prevents API keys from being committed to git in per-project configs. The global config at `~/.config/memories/` can use literal keys (not in a repo).

**Per-project `.memories/` directory:** Must be added to `.gitignore`. The installer should handle this automatically. Even with env var interpolation, the directory may contain routing preferences that shouldn't be shared.

---

## Backward Compatibility

- No `backends.yaml` = env var mode = current behavior, zero changes
- Hooks that source `_lib.sh` get multi-backend for free when config exists
- MCP server falls back to `MEMORIES_URL` env var if no config
- All existing `.mcp.json` configs keep working (they pass env vars to the MCP server)
- No engine changes — each instance is independent

---

## Test Strategy

| File | Key Scenarios |
|------|--------------|
| `tests/test_multi_backend_config.py` | Config loading (project → global → env fallback), scenario routing derivation, DIY validation, override merging |
| `tests/test_search_merge.py` | Fan-out mock, deduplication at 0.95 threshold, score sorting, _backend tagging, single-backend passthrough |
| `tests/test_hook_routing.py` | Hook reads config, routes extract to correct backends, search fans out |

MCP server tests: manual integration test with two local instances on different ports.

---

## Files Modified

| File | Changes |
|------|---------|
| `integrations/claude-code/hooks/_lib.sh` | `_load_backends()`, `_get_backends_for_op()`, `_search_memories_multi()`, scenario routing matrix. Also move duplicated `search_memories()` from 3 hooks into `_lib.sh` as part of this refactor. |
| `integrations/claude-code/hooks/memory-recall.sh` | Use `_search_memories_multi()` from _lib.sh |
| `integrations/claude-code/hooks/memory-query.sh` | Use `_search_memories_multi()` from _lib.sh |
| `integrations/claude-code/hooks/memory-rehydrate.sh` | Use `_search_memories_multi()` from _lib.sh (missing from original spec — this hook also searches) |
| `integrations/claude-code/hooks/memory-extract.sh` | Multi-backend extract routing |
| `integrations/claude-code/hooks/memory-flush.sh` | Multi-backend extract routing |
| `integrations/claude-code/hooks/memory-commit.sh` | Multi-backend extract routing |
| `integrations/claude-code/hooks/memory-subagent-capture.sh` | Multi-backend extract routing (missing from original spec — this hook also extracts) |
| `mcp-server/index.js` | Load config (add `js-yaml` dependency), proxy routing, search merge via `Promise.all()` |
| `mcp-server/package.json` | Add `js-yaml` dependency |
| `skills/memories/SKILL.md` | Multi-backend awareness instructions |
| `tests/test_multi_backend_config.py` | Config + routing tests |
| `tests/test_search_merge.py` | Search merge + dedup tests |

## What Is Explicitly Out of Scope

- Engine changes (each instance is independent)
- UI for managing backends (edit YAML directly)
- Cross-instance memory linking (memories on different backends are independent)
- Conflict resolution across backends (if same memory exists on two backends, both are returned — agent sees both)
- Real-time sync between backends (use export/import for bulk sync)
- Scenario: `personal + shared` content-type routing (agent skill handles this, not config)
