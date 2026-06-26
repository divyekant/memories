#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Memories — Claude Code cloud bootstrap
#
# Installs the Memories THIN CLIENT (hooks + MCP bridge + CLAUDE.md) into a
# Claude Code cloud session, pointing at an EXTERNALLY HOSTED Memories backend.
# No backend/database runs in the sandbox — only clients that call your host.
#
# Invoked by the cloud Environment's setup script (see
# environment-setup-script.sh). Idempotent: safe to re-run. All config comes
# from environment variables set in the Claude Code web UI.
# ---------------------------------------------------------------------------
set -euo pipefail

# --- Config (set as Environment Variables in the Claude Code web UI) --------
: "${MEMORIES_URL:?Set MEMORIES_URL to your hosted backend, e.g. https://memories.example.com}"
: "${MEMORIES_API_KEY:?Set MEMORIES_API_KEY to your backend API key}"
MEMORIES_SRC_REPO="${MEMORIES_SRC_REPO:-divyekant/memories}"  # source of hook scripts + MCP bridge
MEMORIES_SRC_REF="${MEMORIES_SRC_REF:-main}"
SETUP_GH_TOKEN="${SETUP_GH_TOKEN:-}"                          # PAT to clone the (private) source repo
INSTALL_MCP="${INSTALL_MCP:-1}"                               # 1 = also install the Node MCP bridge

CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
HOOK_DIR="$CLAUDE_HOME/hooks/memory"
BRIDGE_DIR="$CLAUDE_HOME/memories-mcp"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[memories-setup] %s\n' "$*"; }

# --- 1. Obtain client assets (hook scripts + MCP bridge) --------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [ -d "$SELF_DIR/../plugin/hooks" ]; then
  SRC="$(cd "$SELF_DIR/.." && pwd)"           # running from inside the memories repo
  log "Using local memories checkout at $SRC"
else
  clone_url="https://github.com/${MEMORIES_SRC_REPO}.git"
  [ -n "$SETUP_GH_TOKEN" ] && clone_url="https://${SETUP_GH_TOKEN}@github.com/${MEMORIES_SRC_REPO}.git"
  log "Cloning ${MEMORIES_SRC_REPO}@${MEMORIES_SRC_REF} for client assets"
  git clone --depth 1 --branch "$MEMORIES_SRC_REF" "$clone_url" "$WORK/memories"
  SRC="$WORK/memories"
fi

# --- 2. Install hooks -------------------------------------------------------
log "Installing hooks -> $HOOK_DIR"
mkdir -p "$HOOK_DIR"
cp "$SRC"/plugin/hooks/*.sh "$HOOK_DIR"/
cp "$SRC"/plugin/hooks/*.json "$HOOK_DIR"/ 2>/dev/null || true
chmod +x "$HOOK_DIR"/*.sh

# --- 3. Install global CLAUDE.md -------------------------------------------
log "Installing CLAUDE.md -> $CLAUDE_HOME/CLAUDE.md"
mkdir -p "$CLAUDE_HOME"
cp "$SELF_DIR/templates/CLAUDE.md" "$CLAUDE_HOME/CLAUDE.md"

# --- 4. Register hooks in settings.json (merge if one exists) ---------------
SETTINGS="$CLAUDE_HOME/settings.json"
log "Registering hooks -> $SETTINGS"
HOOKS_JSON="$(sed "s#__HOOK_DIR__#${HOOK_DIR}#g" "$SELF_DIR/templates/settings.json")"
if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  printf '%s' "$HOOKS_JSON" | jq -s '.[0] * .[1]' "$SETTINGS" - > "$tmp" && mv "$tmp" "$SETTINGS"
else
  printf '%s\n' "$HOOKS_JSON" > "$SETTINGS"
fi

# --- 5. Install MCP bridge (optional; hooks work without it) ----------------
if [ "$INSTALL_MCP" = "1" ]; then
  if command -v node >/dev/null 2>&1; then
    log "Installing MCP bridge -> $BRIDGE_DIR"
    rm -rf "$BRIDGE_DIR"; mkdir -p "$BRIDGE_DIR"
    cp -R "$SRC"/mcp-server/* "$BRIDGE_DIR"/
    ( cd "$BRIDGE_DIR" && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1 ) \
      || log "WARN: npm install failed; MCP bridge may not load (hooks still active)"
    if command -v claude >/dev/null 2>&1; then
      claude mcp add memories --scope user \
        --env "MEMORIES_URL=${MEMORIES_URL}" --env "MEMORIES_API_KEY=${MEMORIES_API_KEY}" \
        -- node "$BRIDGE_DIR/index.js" >/dev/null 2>&1 \
        || log "WARN: MCP auto-register failed; add templates/mcp.json to a repo manually"
    fi
  else
    log "WARN: node not found; skipping MCP bridge (hooks still active)"
  fi
fi

# --- 6. Health check --------------------------------------------------------
if curl -sf --max-time 5 -H "X-API-Key: ${MEMORIES_API_KEY}" "${MEMORIES_URL}/health" >/dev/null 2>&1; then
  log "Backend reachable at ${MEMORIES_URL}  OK"
else
  log "WARN: backend NOT reachable at ${MEMORIES_URL}"
  log "      check MEMORIES_URL, MEMORIES_API_KEY, and the Custom network allowlist"
fi
log "Done — hooks + CLAUDE.md installed; memory recall/capture active next prompt."
