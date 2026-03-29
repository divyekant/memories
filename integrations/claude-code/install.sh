#!/bin/bash
# install.sh — installer for Memories automatic integrations
# Usage: ./install.sh [--auto] [--claude] [--codex] [--cursor] [--openclaw] [--uninstall] [--dry-run]
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOKS_SRC="$SCRIPT_DIR/hooks"
OPENCLAW_SKILL_SRC="$REPO_ROOT/integrations/openclaw-skill.md"
CODEX_HOOKS_SRC="$REPO_ROOT/integrations/codex/hooks"

READONLY_MCP_TOOLS='["mcp__memories__memory_search","mcp__memories__memory_list","mcp__memories__memory_count","mcp__memories__memory_stats","mcp__memories__memory_is_novel","mcp__memories__memory_is_useful","mcp__memories__memory_conflicts"]'

CODEX_NOTIFY_MARKER="Memories Codex notify"
CODEX_MCP_MARKER="Memories Codex MCP"
CODEX_DEV_INSTR_MARKER="Memories Codex developer instructions"

TARGET_CLAUDE=false
TARGET_CODEX=false
TARGET_CURSOR=false
TARGET_OPENCLAW=false
EXPLICIT_TARGETS=false
AUTO_DETECT=true
UNINSTALL=false
DRY_RUN=false

usage() {
  cat <<'EOF'
Memories installer

Usage:
  ./integrations/claude-code/install.sh [options]

Options:
  --auto       Auto-detect targets (default)
  --claude     Install Claude Code hooks + MCP
  --codex      Install Codex integration (settings hooks + MCP)
  --cursor     Install Cursor hooks + MCP
  --openclaw   Install OpenClaw skill
  --uninstall  Remove installed files for selected targets
  --dry-run    Print detected/selected targets and exit
  -h, --help   Show this help

Examples:
  ./integrations/claude-code/install.sh
  ./integrations/claude-code/install.sh --claude --codex --cursor
  ./integrations/claude-code/install.sh --auto --dry-run
EOF
}

for arg in "$@"; do
  case "$arg" in
    --auto)
      AUTO_DETECT=true
      ;;
    --claude)
      TARGET_CLAUDE=true
      EXPLICIT_TARGETS=true
      AUTO_DETECT=false
      ;;
    --codex)
      TARGET_CODEX=true
      EXPLICIT_TARGETS=true
      AUTO_DETECT=false
      ;;
    --cursor)
      TARGET_CURSOR=true
      EXPLICIT_TARGETS=true
      AUTO_DETECT=false
      ;;
    --openclaw)
      TARGET_OPENCLAW=true
      EXPLICIT_TARGETS=true
      AUTO_DETECT=false
      ;;
    --uninstall)
      UNINSTALL=true
      ;;
    --dry-run)
      DRY_RUN=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown argument:${NC} $arg"
      usage
      exit 1
      ;;
  esac
done

detect_targets() {
  TARGET_CLAUDE=false
  TARGET_CODEX=false
  TARGET_CURSOR=false
  TARGET_OPENCLAW=false

  if [ -d "$HOME/.claude" ] || [ -f "$HOME/.claude/settings.json" ]; then
    TARGET_CLAUDE=true
  fi
  if [ -d "$HOME/.codex" ] || [ -f "$HOME/.codex/config.toml" ]; then
    TARGET_CODEX=true
  fi
  if [ -d "$HOME/.cursor" ]; then
    TARGET_CURSOR=true
  fi
  if [ -d "$HOME/.openclaw" ] || [ -d "$HOME/.openclaw/skills" ]; then
    TARGET_OPENCLAW=true
  fi
}

if [ "$AUTO_DETECT" = true ] && [ "$EXPLICIT_TARGETS" = false ]; then
  detect_targets
fi

# Fallback for first-time setup
if [ "$TARGET_CLAUDE" = false ] && [ "$TARGET_CODEX" = false ] && [ "$TARGET_CURSOR" = false ] && [ "$TARGET_OPENCLAW" = false ]; then
  TARGET_CLAUDE=true
fi

target_list=()
[ "$TARGET_CLAUDE" = true ] && target_list+=("claude")
[ "$TARGET_CODEX" = true ] && target_list+=("codex")
[ "$TARGET_CURSOR" = true ] && target_list+=("cursor")
[ "$TARGET_OPENCLAW" = true ] && target_list+=("openclaw")
TARGETS_CSV="$(IFS=, ; echo "${target_list[*]}")"

if [ "$DRY_RUN" = true ]; then
  echo "targets=$TARGETS_CSV"
  if [ "$UNINSTALL" = true ]; then
    echo "mode=uninstall"
  else
    echo "mode=install"
  fi
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  echo -e "${RED}jq is required but not installed.${NC}"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo -e "${RED}curl is required but not installed.${NC}"
  exit 1
fi

echo ""
echo -e "${BLUE}Memories — Automatic Memory Layer Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo -e "Targets: ${GREEN}$TARGETS_CSV${NC}"
echo ""

hooks_target_count=0
[ "$TARGET_CLAUDE" = true ] && hooks_target_count=$((hooks_target_count + 1))
[ "$TARGET_CODEX" = true ] && hooks_target_count=$((hooks_target_count + 1))
[ "$TARGET_CURSOR" = true ] && hooks_target_count=$((hooks_target_count + 1))

append_marked_block() {
  local file="$1"
  local marker="$2"
  local body="$3"
  local start="# BEGIN $marker"
  local end="# END $marker"

  if grep -Fq "$start" "$file" 2>/dev/null; then
    return 0
  fi

  {
    echo ""
    echo "$start"
    printf '%s\n' "$body"
    echo "$end"
  } >> "$file"
}

remove_marked_block() {
  local file="$1"
  local marker="$2"
  local start="# BEGIN $marker"
  local end="# END $marker"

  if [ ! -f "$file" ] || ! grep -Fq "$start" "$file"; then
    return 0
  fi

  awk -v start="$start" -v end="$end" '
    $0 == start { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$file" > "$file.tmp"
  mv "$file.tmp" "$file"
  echo -e "  ${GREEN}[OK]${NC} Removed $marker block from $file"
}

toml_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

remove_target() {
  local label="$1"
  local path="$2"
  if [ -d "$path" ]; then
    rm -rf "$path"
    echo -e "  ${GREEN}[OK]${NC} Removed $label: $path"
  else
    echo -e "  ${YELLOW}[WARN]${NC} Not found ($label): $path"
  fi
}

remove_mcp_json_target() {
  local label="$1"
  local settings_file="$2"

  if [ ! -f "$settings_file" ]; then
    return 0
  fi

  if ! jq -e '.mcpServers.memories' "$settings_file" >/dev/null 2>&1; then
    return 0
  fi

  local updated
  updated=$(jq 'del(.mcpServers.memories) | if .mcpServers == {} then del(.mcpServers) else . end' "$settings_file")
  echo "$updated" > "$settings_file"
  echo -e "  ${GREEN}[OK]${NC} Removed $label MCP 'memories' from $settings_file"
}

if [ "$UNINSTALL" = true ]; then
  echo -e "${YELLOW}Uninstalling selected targets...${NC}"

  if [ "$TARGET_CLAUDE" = true ]; then
    remove_target "Claude hooks" "$HOME/.claude/hooks/memory"
    remove_mcp_json_target "Claude" "$HOME/.claude/settings.json"
    echo "  Manual cleanup: remove Memories hook entries from $HOME/.claude/settings.json"
  fi

  if [ "$TARGET_CODEX" = true ]; then
    remove_target "Codex hooks" "$HOME/.codex/hooks/memory"
    if [ -f "$HOME/.codex/hooks.json" ]; then
      local cleaned
      cleaned=$(jq '
        .hooks |= with_entries(
          .value |= map(
            .hooks |= map(select(.command | test("/hooks/memory/memory-") | not))
          )
          | .value |= map(select(.hooks | length > 0))
        )
        | .hooks |= with_entries(select(.value | length > 0))
        | if .hooks == {} then del(.hooks) else . end
      ' "$HOME/.codex/hooks.json" 2>/dev/null)
      if [ -n "$cleaned" ]; then
        echo "$cleaned" > "$HOME/.codex/hooks.json"
        echo -e "  ${GREEN}[OK]${NC} Removed memory hooks from $HOME/.codex/hooks.json"
      fi
    fi
    remove_marked_block "$HOME/.codex/config.toml" "$CODEX_NOTIFY_MARKER"
    remove_marked_block "$HOME/.codex/config.toml" "$CODEX_MCP_MARKER"
    remove_marked_block "$HOME/.codex/config.toml" "$CODEX_DEV_INSTR_MARKER"
    echo "  Manual cleanup: remove Memories hook entries from $HOME/.codex/settings.json and any custom config blocks from $HOME/.codex/config.toml"
  fi

  if [ "$TARGET_CURSOR" = true ]; then
    remove_target "Cursor hooks" "$HOME/.claude/hooks/memory"
    remove_mcp_json_target "Cursor" "$HOME/.cursor/mcp.json"
    echo "  Manual cleanup: remove Memories hook entries from $HOME/.claude/settings.json"
  fi

  if [ "$TARGET_OPENCLAW" = true ]; then
    remove_target "OpenClaw skill" "$HOME/.openclaw/skills/memories"
  fi

  echo ""
  echo "Manual cleanup (optional): remove MEMORIES_* from $HOME/.config/memories/env and EXTRACT_* from $REPO_ROOT/.env"
  exit 0
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"
MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"

echo -e "[1/4] Checking Memories service at ${BLUE}$MEMORIES_URL${NC}..."
HEALTH=$(curl -sf "$MEMORIES_URL/health" 2>/dev/null || echo "FAIL")
if [ "$HEALTH" = "FAIL" ]; then
  echo -e "  ${RED}[FAIL]${NC} Memories service not reachable at $MEMORIES_URL"
  echo "  Start it with: docker compose up -d memories"
  exit 1
fi
TOTAL=$(echo "$HEALTH" | jq -r '.total_memories // 0')
echo -e "  ${GREEN}[OK]${NC} Healthy ($TOTAL memories)"
echo ""

EXTRACT_PROVIDER=""
EXTRACT_KEY_VAR=""
EXTRACT_KEY_VAL=""
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

if [ "$hooks_target_count" -gt 0 ]; then
  echo -e "[2/4] Extraction provider (for automatic learning):"
  echo "  1. Anthropic (recommended, ~\$0.001/turn, full AUDN)"
  echo "  2. OpenAI (~\$0.001/turn, full AUDN)"
  echo "  3. Ollama (free, local, extraction only)"
  echo "  4. Skip (retrieval only)"
  echo ""
  read -r -p "  > " PROVIDER_CHOICE

  case "$PROVIDER_CHOICE" in
    1)
      EXTRACT_PROVIDER="anthropic"
      read -r -p "  Anthropic API key: " EXTRACT_KEY_VAL
      if [ -z "$EXTRACT_KEY_VAL" ]; then
        echo -e "  ${RED}[FAIL]${NC} API key required"
        exit 1
      fi
      EXTRACT_KEY_VAR="ANTHROPIC_API_KEY"
      ;;
    2)
      EXTRACT_PROVIDER="openai"
      read -r -p "  OpenAI API key: " EXTRACT_KEY_VAL
      if [ -z "$EXTRACT_KEY_VAL" ]; then
        echo -e "  ${RED}[FAIL]${NC} API key required"
        exit 1
      fi
      EXTRACT_KEY_VAR="OPENAI_API_KEY"
      ;;
    3)
      EXTRACT_PROVIDER="ollama"
      echo -n "  Checking Ollama at $OLLAMA_URL... "
      OLLAMA_CHECK=$(curl -sf "$OLLAMA_URL/api/tags" 2>/dev/null || echo "FAIL")
      if [ "$OLLAMA_CHECK" = "FAIL" ]; then
        echo -e "${RED}[FAIL]${NC} Not reachable"
        echo "  Make sure Ollama is running: ollama serve"
        exit 1
      fi
      echo -e "${GREEN}[OK]${NC}"
      ;;
    4|"")
      echo -e "  ${YELLOW}[WARN]${NC} Extraction disabled (retrieval-only mode)"
      ;;
    *)
      echo -e "  ${RED}[FAIL]${NC} Invalid choice"
      exit 1
      ;;
  esac
else
  echo -e "[2/4] Skipping extraction provider prompt (no hook targets selected)."
fi

echo ""
echo -e "[3/4] Installing selected targets..."

render_hooks_json() {
  local hooks_dir="$1"
  jq --arg hooks_dir "$hooks_dir" '
    .hooks |= with_entries(
      .value |= map(
        .hooks |= map(
          if .type == "command"
          then .command = ($hooks_dir + "/" + (.command | split("/") | last))
          else .
          end
        )
      )
    )
  ' "$HOOKS_SRC/hooks.json"
}

install_hooks_target() {
  local client="$1"
  local hooks_dir="$2"
  local settings_file="$3"
  local hooks_profile="${4:-default}"

  mkdir -p "$hooks_dir"
  cp "$HOOKS_SRC"/memory-*.sh "$hooks_dir/"
  [ -f "$HOOKS_SRC/_lib.sh" ] && cp "$HOOKS_SRC/_lib.sh" "$hooks_dir/"
  [ -f "$HOOKS_SRC/response-hints.json" ] && cp "$HOOKS_SRC/response-hints.json" "$hooks_dir/"
  chmod +x "$hooks_dir"/*.sh
  echo -e "  ${GREEN}[OK]${NC} Installed $client hooks: $hooks_dir"

  local settings_dir
  settings_dir=$(dirname "$settings_file")
  mkdir -p "$settings_dir"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi

  local hooks_json
  if [ "$hooks_profile" = "codex" ]; then
    hooks_json=$(render_codex_hooks_json "$hooks_dir")
  else
    hooks_json=$(render_hooks_json "$hooks_dir")
  fi

  local merged
  merged=$(jq -s '
    .[0] as $existing |
    .[1] as $new |
    $existing * {hooks: (($existing.hooks // {}) * $new.hooks)}
  ' "$settings_file" <(echo "$hooks_json"))

  echo "$merged" > "$settings_file"
  echo -e "  ${GREEN}[OK]${NC} Merged hook config into $settings_file"

  # Merge read-only memory tool permissions into permissions.allow
  merged=$(jq --argjson tools "$READONLY_MCP_TOOLS" '
    .permissions.allow = ((.permissions.allow // []) + $tools | unique)
  ' "$settings_file")

  echo "$merged" > "$settings_file"
  echo -e "  ${GREEN}[OK]${NC} Merged read-only memory tool permissions into $settings_file"
}

install_mcp_json_target() {
  local label="$1"
  local settings_file="$2"

  local settings_dir
  settings_dir=$(dirname "$settings_file")
  mkdir -p "$settings_dir"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi

  if jq -e '.mcpServers.memories' "$settings_file" >/dev/null 2>&1; then
    echo -e "  ${YELLOW}[SKIP]${NC} $label MCP server 'memories' already configured in $settings_file"
    return 0
  fi

  local mcp_path="$REPO_ROOT/mcp-server/index.js"
  local merged
  merged=$(jq --arg mcp_path "$mcp_path" \
              --arg url "$MEMORIES_URL" \
              --arg key "$MEMORIES_API_KEY" '
    .mcpServers.memories = {
      command: "node",
      args: [$mcp_path],
      env: {
        MEMORIES_URL: $url,
        MEMORIES_API_KEY: $key
      }
    }
  ' "$settings_file")

  echo "$merged" > "$settings_file"
  echo -e "  ${GREEN}[OK]${NC} Added $label MCP config in $settings_file"
}

install_cursor_target() {
  # Cursor natively reads Claude Code's ~/.claude/settings.json via "Third-party skills".
  # All hook events (SessionStart, UserPromptSubmit, Stop, SessionEnd, PreCompact) are
  # supported with automatic name mapping. We install in Claude Code format and Cursor
  # picks it up — no separate hooks.json needed.
  install_hooks_target "Cursor" "$HOME/.claude/hooks/memory" "$HOME/.claude/settings.json"
  install_mcp_json_target "Cursor" "$HOME/.cursor/mcp.json"
  echo ""
  echo -e "  ${YELLOW}[ACTION REQUIRED]${NC} Enable third-party hooks in Cursor:"
  echo -e "  Settings → Features → Third-party skills → toggle ON"
  echo -e "  Then restart Cursor."
}

install_openclaw_target() {
  local skill_dir="$HOME/.openclaw/skills/memories"
  mkdir -p "$skill_dir"
  cp "$OPENCLAW_SKILL_SRC" "$skill_dir/SKILL.md"
  echo -e "  ${GREEN}[OK]${NC} Installed OpenClaw skill: $skill_dir/SKILL.md"
}

install_codex_target() {
  local hook_dir="$HOME/.codex/hooks/memory"
  local codex_hooks_json="$HOME/.codex/hooks.json"
  local codex_settings="$HOME/.codex/settings.json"
  local codex_config="$HOME/.codex/config.toml"

  # Copy Codex-specific hooks (NOT Claude Code hooks)
  mkdir -p "$hook_dir"
  cp "$CODEX_HOOKS_SRC"/memory-*.sh "$hook_dir/"
  [ -f "$CODEX_HOOKS_SRC/_lib.sh" ] && cp "$CODEX_HOOKS_SRC/_lib.sh" "$hook_dir/"
  [ -f "$CODEX_HOOKS_SRC/response-hints.json" ] && cp "$CODEX_HOOKS_SRC/response-hints.json" "$hook_dir/"
  chmod +x "$hook_dir"/*.sh
  echo -e "  ${GREEN}[OK]${NC} Installed Codex hooks: $hook_dir"

  # Write standalone hooks.json with resolved paths
  local rendered_hooks
  rendered_hooks=$(jq --arg hooks_dir "$hook_dir" '
    .hooks |= with_entries(
      .value |= map(
        .hooks |= map(
          if .type == "command"
          then .command = ($hooks_dir + "/" + (.command | split("/") | last))
          else .
          end
        )
      )
    )
  ' "$CODEX_HOOKS_SRC/hooks.json")

  # Merge into existing hooks.json if present, or create new
  mkdir -p "$(dirname "$codex_hooks_json")"
  if [ -f "$codex_hooks_json" ]; then
    local merged
    merged=$(jq -s '
      .[0] as $existing |
      .[1] as $new |
      $existing * {hooks: (($existing.hooks // {}) * $new.hooks)}
    ' "$codex_hooks_json" <(echo "$rendered_hooks"))
    echo "$merged" > "$codex_hooks_json"
  else
    echo "$rendered_hooks" > "$codex_hooks_json"
  fi
  echo -e "  ${GREEN}[OK]${NC} Wrote Codex hook config: $codex_hooks_json"

  # Merge read-only memory tool permissions into settings.json
  mkdir -p "$(dirname "$codex_settings")"
  if [ ! -f "$codex_settings" ]; then
    echo '{}' > "$codex_settings"
  fi
  local perms_merged
  perms_merged=$(jq --argjson tools "$READONLY_MCP_TOOLS" '
    .permissions.allow = ((.permissions.allow // []) + $tools | unique)
  ' "$codex_settings")
  echo "$perms_merged" > "$codex_settings"
  echo -e "  ${GREEN}[OK]${NC} Merged read-only memory tool permissions into $codex_settings"

  # MCP config in config.toml (unchanged)
  mkdir -p "$(dirname "$codex_config")"
  touch "$codex_config"

  if grep -Eq '^[[:space:]]*\[mcp_servers\.memories\][[:space:]]*$' "$codex_config"; then
    echo -e "  ${YELLOW}[SKIP]${NC} Codex MCP server 'memories' already configured"
  else
    local escaped_repo_mcp escaped_memories_url escaped_memories_api_key
    escaped_repo_mcp=$(toml_escape "$REPO_ROOT/mcp-server/index.js")
    escaped_memories_url=$(toml_escape "$MEMORIES_URL")
    escaped_memories_api_key=$(toml_escape "$MEMORIES_API_KEY")
    local mcp_block
    mcp_block=$(cat <<EOF
[mcp_servers.memories]
command = "node"
args = ["$escaped_repo_mcp"]

[mcp_servers.memories.env]
MEMORIES_URL = "$escaped_memories_url"
MEMORIES_API_KEY = "$escaped_memories_api_key"
EOF
)
    append_marked_block "$codex_config" "$CODEX_MCP_MARKER" "$mcp_block"
    echo -e "  ${GREEN}[OK]${NC} Added Codex MCP config in $codex_config"
  fi

  # Developer instructions (unchanged)
  if grep -Eq '^[[:space:]]*developer_instructions[[:space:]]*=' "$codex_config"; then
    echo -e "  ${YELLOW}[SKIP]${NC} developer_instructions already configured in $codex_config"
  else
    local dev_instructions_block
    dev_instructions_block=$(cat <<'EOF'
developer_instructions = """
Use the Memories MCP tools as your memory layer with three responsibilities:

1. READ: Run memory_search before implementation-heavy responses or clarifying questions.
2. WRITE: Use memory_add for single clear facts (check memory_is_novel first). Use memory_extract for rich conversations, decision changes, or deferred work updates — it handles Add/Update/Delete/Noop automatically via AUDN. For scoped keys, always pass a non-empty source on memory_extract.
3. MAINTAIN: Use memory_delete for explicit forget requests. memory_extract handles most lifecycle updates automatically. For bulk cleanup with scoped keys, prefer prefix-based deletion patterns that stay inside authorized sources.

Source prefixes: codex/{project} for decisions (or another authorized decision prefix when using scoped keys), learning/{project} for fixes, wip/{project} for deferred work.
"""
EOF
)
    append_marked_block "$codex_config" "$CODEX_DEV_INSTR_MARKER" "$dev_instructions_block"
    echo -e "  ${GREEN}[OK]${NC} Added Codex developer instructions in $codex_config"
  fi

  echo -e "  ${YELLOW}[NOTE]${NC} Scoped-key tip: set MEMORIES_SOURCE_PREFIXES and MEMORIES_EXTRACT_SOURCE in ~/.config/memories/env if your key is restricted to non-codex prefixes."
}

if [ "$TARGET_CLAUDE" = true ]; then
  install_hooks_target "Claude" "$HOME/.claude/hooks/memory" "$HOME/.claude/settings.json"
  install_mcp_json_target "Claude" "$HOME/.claude/settings.json"
fi

if [ "$TARGET_CODEX" = true ]; then
  install_codex_target
fi

if [ "$TARGET_CURSOR" = true ]; then
  install_cursor_target
fi

if [ "$TARGET_OPENCLAW" = true ]; then
  install_openclaw_target
fi

echo ""
echo -e "[4/4] Writing configuration files..."

# Optional: Multi-backend setup
if [ "$hooks_target_count" -gt 0 ]; then
 if [ -t 0 ]; then
  echo ""
  echo -e "${BLUE}Multi-backend routing (optional):${NC}"
  echo "  Route a single session to multiple Memories instances (e.g., dev+prod, personal+shared)."
  echo "  This is fully backward compatible — skip to keep single-backend behavior."
  echo ""
  read -r -p "  Set up multi-backend? [y/N] " MULTI_BACKEND_CHOICE

  if [[ "$MULTI_BACKEND_CHOICE" =~ ^[Yy]$ ]]; then
    echo ""
    echo "  Choose a scenario:"
    echo "    1. Dev + Prod (search both, extract to dev)"
    echo "    2. Personal + Shared (search both, decisions to shared)"
    echo "    3. Skip"
    echo ""
    read -r -p "  > " SCENARIO_CHOICE

    case "$SCENARIO_CHOICE" in
      1)
        echo ""
        read -r -p "  Dev backend URL [http://localhost:8900]: " DEV_URL
        DEV_URL="${DEV_URL:-http://localhost:8900}"
        read -r -p "  Dev API key (or env var like \${MEMORIES_DEV_KEY}): " DEV_KEY
        read -r -p "  Prod backend URL: " PROD_URL
        if [ -z "$PROD_URL" ]; then
          echo -e "  ${RED}[FAIL]${NC} Prod URL is required for dev+prod scenario"
        else
          read -r -p "  Prod API key (or env var like \${MEMORIES_PROD_KEY}): " PROD_KEY
          BACKENDS_YAML="$HOME/.config/memories/backends.yaml"
          mkdir -p "$(dirname "$BACKENDS_YAML")"
          cat > "$BACKENDS_YAML" <<MBEOF
backends:
  dev:
    url: $DEV_URL
    api_key: $DEV_KEY
    scenario: dev
  prod:
    url: $PROD_URL
    api_key: $PROD_KEY
    scenario: prod
MBEOF
          echo -e "  ${GREEN}[OK]${NC} Wrote multi-backend config: $BACKENDS_YAML"
        fi
        ;;
      2)
        echo ""
        read -r -p "  Personal backend URL [http://localhost:8900]: " PERSONAL_URL
        PERSONAL_URL="${PERSONAL_URL:-http://localhost:8900}"
        read -r -p "  Personal API key (or env var like \${MEMORIES_PERSONAL_KEY}): " PERSONAL_KEY
        read -r -p "  Shared backend URL: " SHARED_URL
        if [ -z "$SHARED_URL" ]; then
          echo -e "  ${RED}[FAIL]${NC} Shared URL is required for personal+shared scenario"
        else
          read -r -p "  Shared API key (or env var like \${MEMORIES_SHARED_KEY}): " SHARED_KEY
          BACKENDS_YAML="$HOME/.config/memories/backends.yaml"
          mkdir -p "$(dirname "$BACKENDS_YAML")"
          cat > "$BACKENDS_YAML" <<MBEOF
backends:
  personal:
    url: $PERSONAL_URL
    api_key: $PERSONAL_KEY
    scenario: personal
  shared:
    url: $SHARED_URL
    api_key: $SHARED_KEY
    scenario: shared
MBEOF
          echo -e "  ${GREEN}[OK]${NC} Wrote multi-backend config: $BACKENDS_YAML"
        fi
        ;;
      3|"")
        echo -e "  ${YELLOW}[SKIP]${NC} Multi-backend not configured"
        ;;
      *)
        echo -e "  ${YELLOW}[SKIP]${NC} Invalid choice, skipping multi-backend"
        ;;
    esac
  fi
 else
  echo -e "  ${YELLOW}[SKIP]${NC} Non-interactive mode: skipping multi-backend setup"
 fi
fi

# ~/.config/memories/env — loaded by hook scripts at runtime
MEMORIES_ENV_DIR="$HOME/.config/memories"
MEMORIES_ENV_FILE="$MEMORIES_ENV_DIR/env"
mkdir -p "$MEMORIES_ENV_DIR"

write_env_var() {
  local var_name="$1"
  local var_value="$2"
  if grep -q "^$var_name=" "$MEMORIES_ENV_FILE" 2>/dev/null; then
    echo -e "  ${YELLOW}[SKIP]${NC} $var_name already in $MEMORIES_ENV_FILE"
  else
    echo "$var_name=\"$var_value\"" >> "$MEMORIES_ENV_FILE"
    echo -e "  ${GREEN}[ADD]${NC} $var_name → $MEMORIES_ENV_FILE"
  fi
}

write_env_var "MEMORIES_URL" "$MEMORIES_URL"

MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
if [ -n "$MEMORIES_API_KEY" ]; then
  write_env_var "MEMORIES_API_KEY" "$MEMORIES_API_KEY"
fi

# Repo .env — read by docker-compose for extraction provider config
if [ -n "$EXTRACT_PROVIDER" ]; then
  REPO_ENV_FILE="$REPO_ROOT/.env"

  write_docker_env_var() {
    local var_name="$1"
    local var_value="$2"
    if grep -q "^$var_name=" "$REPO_ENV_FILE" 2>/dev/null; then
      echo -e "  ${YELLOW}[SKIP]${NC} $var_name already in $REPO_ENV_FILE"
    else
      echo "$var_name=$var_value" >> "$REPO_ENV_FILE"
      echo -e "  ${GREEN}[ADD]${NC} $var_name → $REPO_ENV_FILE"
    fi
  }

  write_docker_env_var "EXTRACT_PROVIDER" "$EXTRACT_PROVIDER"
  if [ -n "$EXTRACT_KEY_VAR" ] && [ -n "$EXTRACT_KEY_VAL" ]; then
    write_docker_env_var "$EXTRACT_KEY_VAR" "$EXTRACT_KEY_VAL"
  fi
  if [ "$EXTRACT_PROVIDER" = "ollama" ]; then
    write_docker_env_var "OLLAMA_URL" "$OLLAMA_URL"
  fi

  echo ""
  echo -e "  ${YELLOW}[NOTE]${NC} Restart docker-compose from the repo directory to apply extraction settings:"
  echo -e "  cd $REPO_ROOT && docker-compose up -d"
fi

echo ""
echo -e "${GREEN}Done.${NC}"
echo -e "Installed targets: ${BLUE}$TARGETS_CSV${NC}"
echo -e "Hook env file:     ${BLUE}$MEMORIES_ENV_FILE${NC}"
[ -n "$EXTRACT_PROVIDER" ] && echo -e "Docker env file:   ${BLUE}$REPO_ROOT/.env${NC}"

# Print hook summary from hooks.json
if [ "$hooks_target_count" -gt 0 ] && [ -f "$HOOKS_SRC/hooks.json" ]; then
  echo ""
  echo -e "${BLUE}Installed hooks:${NC}"
  jq -r '
    .hooks | to_entries[] |
    .key as $event |
    .value[] |
    .matcher as $matcher |
    .hooks[] |
    $event + " -> " + (.command | split("/") | last)
      + (if $matcher != "" then " (matcher: " + $matcher + ")" else "" end)
  ' "$HOOKS_SRC/hooks.json" 2>/dev/null | while read -r line; do
    echo -e "  ${GREEN}*${NC} $line"
  done
fi
