# R4: Multi-Backend Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable one agent session to talk to multiple Memories backends with intelligent routing — search fans out and merges, writes route per-config.

**Architecture:** Config at `~/.config/memories/backends.yaml` defines named backends with scenario-based or DIY routing. `_lib.sh` handles config loading + routing for hooks. MCP server becomes a thin routing proxy. Engine instances unchanged.

**Tech Stack:** Bash (hooks), JavaScript/Node (MCP server), YAML config, `js-yaml` npm package

**Design spec:** `docs/superpowers/specs/2026-03-23-r4-multi-backend-routing-design.md`

**Test baseline:** 1012 tests passing

---

## File Map

| File | Role | Action |
|------|------|--------|
| `integrations/claude-code/hooks/_lib.sh` | Config loader, `_search_memories_multi()`, routing helpers | **Modify** |
| `integrations/claude-code/hooks/memory-recall.sh` | Use shared search from _lib.sh | **Modify** |
| `integrations/claude-code/hooks/memory-query.sh` | Use shared search from _lib.sh | **Modify** |
| `integrations/claude-code/hooks/memory-rehydrate.sh` | Use shared search from _lib.sh | **Modify** |
| `integrations/claude-code/hooks/memory-extract.sh` | Multi-backend extract routing | **Modify** |
| `integrations/claude-code/hooks/memory-flush.sh` | Multi-backend extract routing | **Modify** |
| `integrations/claude-code/hooks/memory-commit.sh` | Multi-backend extract routing | **Modify** |
| `integrations/claude-code/hooks/memory-subagent-capture.sh` | Multi-backend extract routing | **Modify** |
| `mcp-server/index.js` | Multi-backend proxy routing | **Modify** |
| `mcp-server/package.json` | Add js-yaml dependency | **Modify** |
| `skills/memories/SKILL.md` | Multi-backend awareness | **Modify** |
| `tests/test_multi_backend_config.py` | Config loading + routing tests | **Create** |
| `tests/test_search_merge.py` | Search merge + dedup tests | **Create** |

---

### Task 1: Config loader in _lib.sh

**Files:**
- Modify: `integrations/claude-code/hooks/_lib.sh`
- Create: `tests/test_multi_backend_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_multi_backend_config.py
"""Test multi-backend config loading and routing resolution."""
import subprocess
import json
import os
import pytest
import tempfile
import yaml

def _run_lib_function(func_call, env=None, config_content=None):
    """Source _lib.sh and call a function, return stdout."""
    lib_path = os.path.join(os.path.dirname(__file__), "..",
                            "integrations", "claude-code", "hooks", "_lib.sh")
    script = f'source "{lib_path}"\n{func_call}'
    full_env = {**os.environ, **(env or {})}
    if config_content:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            full_env["MEMORIES_BACKENDS_FILE"] = f.name
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=full_env,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode

class TestConfigLoader:
    def test_load_backends_from_yaml(self):
        """_load_backends should parse YAML config into JSON."""
        config = yaml.dump({
            "backends": {
                "local": {"url": "http://localhost:8900", "api_key": "key1", "scenario": "dev"},
                "prod": {"url": "https://prod.example.com", "api_key": "key2", "scenario": "prod"},
            }
        })
        stdout, _, rc = _run_lib_function("_load_backends", config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_fallback_to_env_vars(self):
        """Without config file, should create single backend from env vars."""
        env = {"MEMORIES_URL": "http://localhost:8900", "MEMORIES_API_KEY": "testkey"}
        stdout, _, rc = _run_lib_function("_load_backends", env=env)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 1
        assert data[0]["url"] == "http://localhost:8900"

    def test_env_var_interpolation(self):
        """API key with ${VAR} should be resolved from environment."""
        config = yaml.dump({
            "backends": {
                "prod": {"url": "https://prod.example.com", "api_key": "${MY_SECRET_KEY}"},
            }
        })
        env = {"MY_SECRET_KEY": "resolved-key"}
        stdout, _, rc = _run_lib_function("_load_backends", env=env, config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert data[0]["api_key"] == "resolved-key"

class TestRoutingResolution:
    def test_dev_prod_search_routing(self):
        """dev+prod scenario should search both backends."""
        config = yaml.dump({
            "backends": {
                "local": {"url": "http://localhost:8900", "api_key": "k1", "scenario": "dev"},
                "prod": {"url": "https://prod.example.com", "api_key": "k2", "scenario": "prod"},
            }
        })
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "search"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_dev_prod_extract_routing(self):
        """dev+prod scenario should extract to dev only."""
        config = yaml.dump({
            "backends": {
                "local": {"url": "http://localhost:8900", "api_key": "k1", "scenario": "dev"},
                "prod": {"url": "https://prod.example.com", "api_key": "k2", "scenario": "prod"},
            }
        })
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "extract"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 1
        assert "localhost" in data[0]["url"]

    def test_diy_routing(self):
        """DIY config with explicit routing should use those rules."""
        config = yaml.dump({
            "backends": {
                "alpha": {"url": "http://alpha:8900", "api_key": "a"},
                "beta": {"url": "http://beta:8900", "api_key": "b"},
            },
            "routing": {
                "search": ["alpha", "beta"],
                "extract": ["alpha"],
            }
        })
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "search"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_single_backend_passthrough(self):
        """Single backend should route everything to it."""
        config = yaml.dump({
            "backends": {
                "only": {"url": "http://localhost:8900", "api_key": "k", "scenario": "dev"},
            }
        })
        for op in ["search", "extract", "add", "feedback"]:
            stdout, _, rc = _run_lib_function(
                f'_get_backends_for_op "{op}"', config_content=config)
            assert rc == 0
            data = json.loads(stdout)
            assert len(data) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_multi_backend_config.py -v`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement config loader in _lib.sh**

Add these functions after the existing helpers (after line ~55):

```bash
# -- Multi-Backend Config --------------------------------------------------

_BACKENDS_CACHE=""

_load_backends() {
  # Return cached if already loaded
  if [ -n "$_BACKENDS_CACHE" ]; then
    echo "$_BACKENDS_CACHE"
    return 0
  fi

  local config_file="${MEMORIES_BACKENDS_FILE:-}"

  # Resolution: explicit env → project → global → env var fallback
  if [ -z "$config_file" ]; then
    if [ -f "$CWD/.memories/backends.yaml" ] 2>/dev/null; then
      config_file="$CWD/.memories/backends.yaml"
    elif [ -f "$HOME/.config/memories/backends.yaml" ]; then
      config_file="$HOME/.config/memories/backends.yaml"
    fi
  fi

  if [ -n "$config_file" ] && [ -f "$config_file" ]; then
    # Parse YAML → JSON using Node.js + js-yaml (guaranteed available — Claude Code requires Node,
    # and js-yaml is installed in mcp-server/node_modules by Task 4).
    # Resolve mcp-server path relative to hooks directory.
    local hooks_dir
    hooks_dir="$(cd "$(dirname "$0")" && pwd)"
    local mcp_modules="${hooks_dir}/../../mcp-server/node_modules"
    # Fallback: check common install locations
    [ ! -d "$mcp_modules" ] && mcp_modules="$HOME/projects/memories/mcp-server/node_modules"

    local raw
    raw=$(node -e "
const yaml = require('${mcp_modules}/js-yaml');
const fs = require('fs');
const data = yaml.load(fs.readFileSync('${config_file}', 'utf8'));
const backends = Object.entries(data.backends || {}).map(([name, cfg]) => {
  let apiKey = cfg.api_key || '';
  const m = apiKey.match(/\\\$\{(\w+)\}/);
  if (m) apiKey = process.env[m[1]] || apiKey;
  return { name, url: cfg.url || '', api_key: apiKey, scenario: cfg.scenario || '' };
});
console.log(JSON.stringify({ backends, routing: data.routing || {} }));
" 2>/dev/null)
    _BACKENDS_CACHE="$raw"
    # Output just the backends array for simple callers
    echo "$raw" | jq -c '.backends'
  else
    # Fallback to env vars — single backend
    local url="${MEMORIES_URL:-http://localhost:8900}"
    local key="${MEMORIES_API_KEY:-}"
    _BACKENDS_CACHE=$(jq -nc --arg url "$url" --arg key "$key" \
      '{backends: [{name: "default", url: $url, api_key: $key, scenario: ""}], routing: {}}')
    echo "$_BACKENDS_CACHE" | jq -c '.backends'
  fi
}

_get_backends_for_op() {
  local op="$1"  # search | extract | add | feedback

  # Load full config (with routing)
  _load_backends > /dev/null  # populate cache
  local config="$_BACKENDS_CACHE"
  local backends
  backends=$(echo "$config" | jq -c '.backends')
  local routing
  routing=$(echo "$config" | jq -c '.routing // {}')
  local count
  count=$(echo "$backends" | jq 'length')

  # Single backend — always that one
  if [ "$count" -eq 1 ]; then
    echo "$backends"
    return 0
  fi

  # Check explicit routing first
  local explicit
  explicit=$(echo "$routing" | jq -c --arg op "$op" '.[$op] // empty')
  if [ -n "$explicit" ] && [ "$explicit" != "null" ]; then
    # Filter backends by name
    echo "$backends" | jq -c --argjson names "$explicit" \
      '[.[] | select(.name as $n | $names | index($n))]'
    return 0
  fi

  # Scenario-based routing
  local scenarios
  scenarios=$(echo "$backends" | jq -r '[.[].scenario] | join(",")')

  case "$op" in
    search)
      # All backends for search
      echo "$backends"
      ;;
    extract)
      # dev or personal backends only
      echo "$backends" | jq -c '[.[] | select(.scenario == "dev" or .scenario == "personal")]'
      ;;
    add)
      # All writable backends (dev + prod, personal + shared)
      echo "$backends"
      ;;
    feedback)
      # dev or personal only
      echo "$backends" | jq -c '[.[] | select(.scenario == "dev" or .scenario == "personal")]'
      ;;
    *)
      # Default: primary (first)
      echo "$backends" | jq -c '[.[0]]'
      ;;
  esac
}
```

Note: Uses Node.js with `js-yaml` for YAML parsing. Node is a hard dependency of Claude Code, and `js-yaml` is installed as an MCP server dependency (Task 4). The hooks resolve the `node_modules` path relative to the hooks directory.

- [ ] **Step 4: Run tests**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_multi_backend_config.py -v`

- [ ] **Step 5: Commit**

```bash
git add integrations/claude-code/hooks/_lib.sh tests/test_multi_backend_config.py
git commit -m "feat: add multi-backend config loader and routing resolver to _lib.sh"
```

---

### Task 2: Refactor search_memories() into _lib.sh + parallel fan-out

**Files:**
- Modify: `integrations/claude-code/hooks/_lib.sh`
- Modify: `integrations/claude-code/hooks/memory-recall.sh`
- Modify: `integrations/claude-code/hooks/memory-query.sh`
- Modify: `integrations/claude-code/hooks/memory-rehydrate.sh`

- [ ] **Step 1: Add _search_memories_multi() to _lib.sh**

After the routing functions, add:

```bash
_search_memories_multi() {
  local query="$1"
  local prefix="${2:-}"
  local limit="${3:-5}"
  local threshold="${4:-0.4}"

  local backends
  backends=$(_get_backends_for_op "search")
  local count
  count=$(echo "$backends" | jq 'length')

  local body
  if [ -n "$prefix" ]; then
    body=$(jq -nc --arg q "$query" --arg p "$prefix" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, source_prefix: $p, k: $k, hybrid: true, threshold: $t}')
  else
    body=$(jq -nc --arg q "$query" --argjson k "$limit" --argjson t "$threshold" \
      '{query: $q, k: $k, hybrid: true, threshold: $t}')
  fi

  if [ "$count" -le 1 ]; then
    # Single backend — direct call (backward compat, no overhead)
    local url key
    url=$(echo "$backends" | jq -r '.[0].url')
    key=$(echo "$backends" | jq -r '.[0].api_key')
    curl -sf --max-time 4 -X POST "$url/search" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
      -d "$body" 2>/dev/null || echo '{"results":[],"count":0}'
    return
  fi

  # Multi-backend: parallel fan-out with background subshells
  local tmpdir
  tmpdir=$(mktemp -d)
  local i=0
  echo "$backends" | jq -c '.[]' | while read -r backend; do
    local url key name
    url=$(echo "$backend" | jq -r '.url')
    key=$(echo "$backend" | jq -r '.api_key')
    name=$(echo "$backend" | jq -r '.name')
    (
      result=$(curl -sf --max-time 4 -X POST "$url/search" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $key" \
        -d "$body" 2>/dev/null)
      if [ -n "$result" ]; then
        # Tag results with _backend
        echo "$result" | jq -c --arg b "$name" '.results[] | . + {_backend: $b}' > "$tmpdir/result_${name}.jsonl"
      fi
    ) &
    i=$((i + 1))
  done
  wait

  # Merge results: collect, dedup by exact text hash, sort by score
  cat "$tmpdir"/result_*.jsonl 2>/dev/null | jq -s '
    unique_by(.text)
    | sort_by(-(.similarity // .rrf_score // 0))
  ' | jq -c '{results: ., count: length}'

  rm -rf "$tmpdir"
}
```

- [ ] **Step 2: Replace search_memories() in memory-recall.sh**

Replace the `search_memories()` function definition (lines 63-101) with:

```bash
search_memories() {
  _search_memories_multi "$@"
}
```

- [ ] **Step 3: Replace search_memories() in memory-query.sh**

Same replacement (lines 100-138).

- [ ] **Step 4: Replace inline search in memory-rehydrate.sh**

Replace the inline curl call (lines 50-55) with:

```bash
BATCH=$(_search_memories_multi "$QUERY" "$prefix" 3 0.35)
```

- [ ] **Step 5: Commit**

```bash
git add integrations/claude-code/hooks/_lib.sh integrations/claude-code/hooks/memory-recall.sh \
  integrations/claude-code/hooks/memory-query.sh integrations/claude-code/hooks/memory-rehydrate.sh
git commit -m "feat: refactor search into _lib.sh with multi-backend parallel fan-out"
```

---

### Task 3: Multi-backend extract routing in hooks

**Files:**
- Modify: `integrations/claude-code/hooks/_lib.sh`
- Modify: `integrations/claude-code/hooks/memory-extract.sh`
- Modify: `integrations/claude-code/hooks/memory-flush.sh`
- Modify: `integrations/claude-code/hooks/memory-commit.sh`
- Modify: `integrations/claude-code/hooks/memory-subagent-capture.sh`

- [ ] **Step 1: Add _extract_multi() to _lib.sh**

```bash
_extract_multi() {
  local messages="$1"
  local source="$2"
  local context="${3:-stop}"

  local backends
  backends=$(_get_backends_for_op "extract")
  local body
  body=$(jq -nc --arg m "$messages" --arg s "$source" --arg c "$context" \
    '{messages: $m, source: $s, context: $c}')

  echo "$backends" | jq -c '.[]' | while read -r backend; do
    local url key name
    url=$(echo "$backend" | jq -r '.url')
    key=$(echo "$backend" | jq -r '.api_key')
    name=$(echo "$backend" | jq -r '.name')
    curl -sf --max-time 30 -X POST "$url/memory/extract" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $key" \
      -d "$body" > /dev/null 2>&1 || _log_error "Extract failed for backend $name"
  done
}
```

- [ ] **Step 2: Replace curl calls in memory-extract.sh**

Replace the curl block (~lines 90-95) with:

```bash
_extract_multi "$MESSAGES" "$SOURCE" "stop"
```

- [ ] **Step 3: Replace curl calls in memory-flush.sh, memory-commit.sh, memory-subagent-capture.sh**

Same pattern — replace the inline curl with `_extract_multi "$MESSAGES" "$SOURCE" "$CONTEXT"` where CONTEXT is the appropriate value per hook.

- [ ] **Step 4: Commit**

```bash
git add integrations/claude-code/hooks/_lib.sh integrations/claude-code/hooks/memory-extract.sh \
  integrations/claude-code/hooks/memory-flush.sh integrations/claude-code/hooks/memory-commit.sh \
  integrations/claude-code/hooks/memory-subagent-capture.sh
git commit -m "feat: multi-backend extract routing in all extraction hooks"
```

---

### Task 4: MCP server multi-backend proxy

**Files:**
- Modify: `mcp-server/index.js`
- Modify: `mcp-server/package.json`

- [ ] **Step 1: Add js-yaml dependency**

```bash
cd mcp-server && npm install js-yaml && cd ..
```

- [ ] **Step 2: Add config loader to index.js**

Replace the single `MEMORIES_URL`/`API_KEY` loading (lines 14-15) with a multi-backend loader:

```javascript
import fs from "fs";
import path from "path";
import yaml from "js-yaml";

// -- Config Loading --------------------------------------------------------

function loadBackends() {
  // Resolution: MEMORIES_BACKENDS_FILE → project → global → env fallback
  const configPaths = [
    process.env.MEMORIES_BACKENDS_FILE,
    path.join(process.cwd(), ".memories", "backends.yaml"),
    path.join(process.env.HOME || "", ".config", "memories", "backends.yaml"),
  ].filter(Boolean);

  for (const p of configPaths) {
    if (fs.existsSync(p)) {
      const raw = yaml.load(fs.readFileSync(p, "utf8"));
      const backends = Object.entries(raw.backends || {}).map(([name, cfg]) => {
        let apiKey = cfg.api_key || "";
        // Env var interpolation
        const match = apiKey.match(/\$\{(\w+)\}/);
        if (match) apiKey = process.env[match[1]] || apiKey;
        return { name, url: cfg.url, apiKey, scenario: cfg.scenario || "" };
      });
      const routing = raw.routing || {};
      return { backends, routing };
    }
  }

  // Fallback to env vars
  return {
    backends: [{
      name: "default",
      url: process.env.MEMORIES_URL || "http://localhost:8900",
      apiKey: process.env.MEMORIES_API_KEY || "",
      scenario: "",
    }],
    routing: {},
  };
}

const config = loadBackends();

function getBackendsForOp(op) {
  // Explicit routing first
  if (config.routing[op]) {
    const names = config.routing[op];
    return config.backends.filter(b => names.includes(b.name));
  }
  // Single backend
  if (config.backends.length === 1) return config.backends;
  // Scenario-based
  switch (op) {
    case "search": return config.backends;
    case "extract": return config.backends.filter(b => b.scenario === "dev" || b.scenario === "personal");
    case "add": return config.backends;
    case "feedback": return config.backends.filter(b => b.scenario === "dev" || b.scenario === "personal");
    default: return [config.backends[0]];
  }
}
```

- [ ] **Step 3: Replace memoriesRequest with multi-backend router**

```javascript
async function memoriesRequest(path, options = {}, op = "search") {
  const backends = getBackendsForOp(op);

  if (backends.length === 1) {
    // Single backend — direct call
    const b = backends[0];
    const url = `${b.url}${path}`;
    const headers = { "Content-Type": "application/json" };
    if (b.apiKey) headers["X-API-Key"] = b.apiKey;
    const response = await fetch(url, { ...options, headers: { ...headers, ...options.headers } });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Memories API error ${response.status}: ${body}`);
    }
    return response.json();
  }

  // Multi-backend — parallel fan-out
  const results = await Promise.allSettled(
    backends.map(async (b) => {
      const url = `${b.url}${path}`;
      const headers = { "Content-Type": "application/json" };
      if (b.apiKey) headers["X-API-Key"] = b.apiKey;
      const response = await fetch(url, { ...options, headers: { ...headers, ...options.headers } });
      if (!response.ok) throw new Error(`${b.name}: HTTP ${response.status}`);
      const data = await response.json();
      return { backend: b.name, data };
    })
  );

  // Collect successful results
  const successes = results.filter(r => r.status === "fulfilled").map(r => r.value);
  if (successes.length === 0) {
    throw new Error("All backends failed");
  }

  // For non-search operations, return first success
  if (op !== "search") return successes[0].data;

  // For search: merge results
  const allResults = [];
  for (const s of successes) {
    for (const r of (s.data.results || [])) {
      allResults.push({ ...r, _backend: s.backend });
    }
  }

  // Dedup by exact text match
  const seen = new Set();
  const deduped = allResults.filter(r => {
    const key = r.text || "";
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Sort by score
  deduped.sort((a, b) => (b.similarity ?? b.rrf_score ?? 0) - (a.similarity ?? a.rrf_score ?? 0));

  return { results: deduped, count: deduped.length };
}
```

- [ ] **Step 4: Update tool handlers to pass operation type**

For each tool, pass the operation type to `memoriesRequest`:

```javascript
// memory_search: op = "search"
const data = await memoriesRequest("/search", { method: "POST", body: JSON.stringify(body) }, "search");

// memory_add: op = "add"
const data = await memoriesRequest("/memory/add", { method: "POST", body: JSON.stringify({ text, source, deduplicate }) }, "add");

// memory_extract: op = "extract"
const submitData = await memoriesRequest("/memory/extract", { method: "POST", body: ... }, "extract");

// memory_is_useful: op = "feedback"
await memoriesRequest("/search/feedback", { method: "POST", body: ... }, "feedback");

// Other tools: default op (first backend)
```

Read the existing tool implementations and add the `op` parameter to each `memoriesRequest` call.

- [ ] **Step 5: Run MCP server to verify it starts**

```bash
cd mcp-server && node -e "import('./index.js').catch(e => { console.error(e.message); process.exit(1); })" && echo "OK" || echo "FAIL"
```

- [ ] **Step 6: Commit**

```bash
git add mcp-server/index.js mcp-server/package.json mcp-server/package-lock.json
git commit -m "feat: multi-backend proxy routing in MCP server"
```

---

### Task 5: Search merge dedup tests

**Files:**
- Create: `tests/test_search_merge.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_search_merge.py
"""Test search result merging across multiple backends."""
import pytest

def _merge_results(results_by_backend):
    """Simulate the merge logic from MCP server / hooks."""
    all_results = []
    for backend, results in results_by_backend.items():
        for r in results:
            all_results.append({**r, "_backend": backend})

    # Dedup by exact text
    seen = set()
    deduped = []
    for r in all_results:
        key = r.get("text", "")
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # Sort by score
    deduped.sort(key=lambda x: x.get("similarity", x.get("rrf_score", 0)), reverse=True)
    return deduped

class TestSearchMerge:
    def test_merge_two_backends(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "fact A", "similarity": 0.9, "source": "test/a"}],
            "prod": [{"id": 2, "text": "fact B", "similarity": 0.8, "source": "test/b"}],
        })
        assert len(results) == 2
        assert results[0]["text"] == "fact A"  # higher score first
        assert results[0]["_backend"] == "local"

    def test_dedup_exact_text_match(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "same fact", "similarity": 0.9}],
            "prod": [{"id": 99, "text": "same fact", "similarity": 0.85}],
        })
        assert len(results) == 1
        assert results[0]["_backend"] == "local"  # first seen wins

    def test_different_text_not_deduped(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "fact A", "similarity": 0.9}],
            "prod": [{"id": 2, "text": "fact A extended version", "similarity": 0.85}],
        })
        assert len(results) == 2  # different text, both kept

    def test_single_backend_passthrough(self):
        results = _merge_results({
            "only": [{"id": 1, "text": "solo", "similarity": 0.9}],
        })
        assert len(results) == 1
        assert results[0]["_backend"] == "only"

    def test_backend_tags_preserved(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "A", "similarity": 0.9}],
            "prod": [{"id": 2, "text": "B", "similarity": 0.8}],
        })
        backends = {r["_backend"] for r in results}
        assert backends == {"local", "prod"}

    def test_empty_backend_handled(self):
        results = _merge_results({
            "local": [{"id": 1, "text": "A", "similarity": 0.9}],
            "prod": [],
        })
        assert len(results) == 1
```

- [ ] **Step 2: Run tests**

Run: `/Users/dk/projects/memories/.venv/bin/python -m pytest tests/test_search_merge.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_search_merge.py
git commit -m "test: add search merge and dedup tests for multi-backend"
```

---

### Task 6: Update Memories skill with multi-backend awareness

**Files:**
- Modify: `skills/memories/SKILL.md`

- [ ] **Step 1: Add multi-backend section**

Append to `skills/memories/SKILL.md`:

```markdown
## Multi-Backend Routing

This session may have multiple Memories backends configured via `~/.config/memories/backends.yaml`.
Memory operations are routed automatically:

- **Search** fans out to all search-enabled backends and merges results
- **Extract** writes to dev/personal backends only (configurable)
- **Add** writes to all writable backends
- **Feedback** routes to dev/personal backends

When search results include `_backend` tags, that shows which instance each memory came from.
You don't need to route manually — the hooks and MCP server handle it transparently.

### Source prefix conventions for multi-backend
- `decision/{project}` — shared/team decisions (written to all backends)
- `personal/{project}` — personal notes (stays on personal/dev backend only)
- `claude-code/{project}`, `learning/{project}`, `wip/{project}` — standard prefixes, routed per config
```

- [ ] **Step 2: Commit**

```bash
git add skills/memories/SKILL.md
git commit -m "docs: add multi-backend routing awareness to Memories skill"
```

---

## Post-Implementation Checklist

- [ ] All baseline tests still pass
- [ ] Config loader: YAML → project → global → env fallback
- [ ] Env var interpolation works for API keys
- [ ] Single-backend mode is backward compatible (no config = same as before)
- [ ] Parallel search fan-out works (background subshells + wait)
- [ ] Search results deduped by exact text match
- [ ] Results tagged with `_backend` provenance
- [ ] Extract routes to dev/personal backends per scenario
- [ ] MCP server uses Promise.all for parallel fan-out
- [ ] MCP server search merge returns deduped results
- [ ] Skill docs updated with multi-backend awareness
- [ ] All 7 hooks use shared functions from _lib.sh
