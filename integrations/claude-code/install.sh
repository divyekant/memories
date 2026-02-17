#!/bin/bash
# install.sh — installer for Memories automatic integrations
# Usage: ./install.sh [--auto] [--claude] [--codex] [--openclaw] [--uninstall] [--dry-run]
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

TARGET_CLAUDE=false
TARGET_CODEX=false
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
  --claude     Install Claude Code hooks
  --codex      Install Codex hooks
  --openclaw   Install OpenClaw skill
  --uninstall  Remove installed files for selected targets
  --dry-run    Print detected/selected targets and exit
  -h, --help   Show this help

Examples:
  ./integrations/claude-code/install.sh
  ./integrations/claude-code/install.sh --claude --codex
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
  TARGET_OPENCLAW=false

  if [ -d "$HOME/.claude" ] || [ -f "$HOME/.claude/settings.json" ]; then
    TARGET_CLAUDE=true
  fi
  if [ -d "$HOME/.codex" ] || [ -f "$HOME/.codex/settings.json" ]; then
    TARGET_CODEX=true
  fi
  if [ -d "$HOME/.openclaw" ] || [ -d "$HOME/.openclaw/skills" ]; then
    TARGET_OPENCLAW=true
  fi
}

if [ "$AUTO_DETECT" = true ] && [ "$EXPLICIT_TARGETS" = false ]; then
  detect_targets
fi

# Fallback for first-time setup
if [ "$TARGET_CLAUDE" = false ] && [ "$TARGET_CODEX" = false ] && [ "$TARGET_OPENCLAW" = false ]; then
  TARGET_CLAUDE=true
fi

target_list=()
[ "$TARGET_CLAUDE" = true ] && target_list+=("claude")
[ "$TARGET_CODEX" = true ] && target_list+=("codex")
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

# Detect shell profile
if [ -f "$HOME/.zshrc" ]; then
  SHELL_PROFILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
  SHELL_PROFILE="$HOME/.bashrc"
else
  SHELL_PROFILE="$HOME/.profile"
fi

echo ""
echo -e "${BLUE}Memories — Automatic Memory Layer Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo -e "Targets: ${GREEN}$TARGETS_CSV${NC}"
echo ""

hooks_target_count=0
[ "$TARGET_CLAUDE" = true ] && hooks_target_count=$((hooks_target_count + 1))
[ "$TARGET_CODEX" = true ] && hooks_target_count=$((hooks_target_count + 1))

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

if [ "$UNINSTALL" = true ]; then
  echo -e "${YELLOW}Uninstalling selected targets...${NC}"

  if [ "$TARGET_CLAUDE" = true ]; then
    remove_target "Claude hooks" "$HOME/.claude/hooks/memory"
    echo "  Manual cleanup: remove Memories hook entries from $HOME/.claude/settings.json"
  fi

  if [ "$TARGET_CODEX" = true ]; then
    remove_target "Codex hooks" "$HOME/.codex/hooks/memory"
    echo "  Manual cleanup: remove Memories hook entries from $HOME/.codex/settings.json"
  fi

  if [ "$TARGET_OPENCLAW" = true ]; then
    remove_target "OpenClaw skill" "$HOME/.openclaw/skills/memories"
  fi

  echo ""
  echo "Manual cleanup (optional): remove MEMORIES_* and EXTRACT_* vars from $SHELL_PROFILE"
  exit 0
fi

MEMORIES_URL="${MEMORIES_URL:-http://localhost:8900}"

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

  mkdir -p "$hooks_dir"
  cp "$HOOKS_SRC"/memory-*.sh "$hooks_dir/"
  chmod +x "$hooks_dir"/*.sh
  echo -e "  ${GREEN}[OK]${NC} Installed $client hooks: $hooks_dir"

  local settings_dir
  settings_dir=$(dirname "$settings_file")
  mkdir -p "$settings_dir"
  if [ ! -f "$settings_file" ]; then
    echo '{}' > "$settings_file"
  fi

  local hooks_json
  hooks_json=$(render_hooks_json "$hooks_dir")

  local merged
  merged=$(jq -s '
    .[0] as $existing |
    .[1] as $new |
    $existing * {hooks: (($existing.hooks // {}) * $new.hooks)}
  ' "$settings_file" <(echo "$hooks_json"))

  echo "$merged" > "$settings_file"
  echo -e "  ${GREEN}[OK]${NC} Merged hook config into $settings_file"
}

install_openclaw_target() {
  local skill_dir="$HOME/.openclaw/skills/memories"
  mkdir -p "$skill_dir"
  cp "$OPENCLAW_SKILL_SRC" "$skill_dir/SKILL.md"
  echo -e "  ${GREEN}[OK]${NC} Installed OpenClaw skill: $skill_dir/SKILL.md"
}

if [ "$TARGET_CLAUDE" = true ]; then
  install_hooks_target "Claude" "$HOME/.claude/hooks/memory" "$HOME/.claude/settings.json"
fi

if [ "$TARGET_CODEX" = true ]; then
  install_hooks_target "Codex" "$HOME/.codex/hooks/memory" "$HOME/.codex/settings.json"
fi

if [ "$TARGET_OPENCLAW" = true ]; then
  install_openclaw_target
fi

echo ""
echo -e "[4/4] Updating shell environment in $SHELL_PROFILE..."

add_env_if_missing() {
  local var_name="$1"
  local var_value="$2"
  if ! grep -q "export $var_name=" "$SHELL_PROFILE" 2>/dev/null; then
    echo "export $var_name=\"$var_value\"" >> "$SHELL_PROFILE"
    echo -e "  ${GREEN}[ADD]${NC} $var_name"
  else
    echo -e "  ${YELLOW}[SKIP]${NC} $var_name already set"
  fi
}

add_env_if_missing "MEMORIES_URL" "$MEMORIES_URL"

MEMORIES_API_KEY="${MEMORIES_API_KEY:-}"
if [ -n "$MEMORIES_API_KEY" ]; then
  add_env_if_missing "MEMORIES_API_KEY" "$MEMORIES_API_KEY"
fi

if [ -n "$EXTRACT_PROVIDER" ]; then
  add_env_if_missing "EXTRACT_PROVIDER" "$EXTRACT_PROVIDER"
  if [ -n "$EXTRACT_KEY_VAR" ] && [ -n "$EXTRACT_KEY_VAL" ]; then
    add_env_if_missing "$EXTRACT_KEY_VAR" "$EXTRACT_KEY_VAL"
  fi
  if [ "$EXTRACT_PROVIDER" = "ollama" ]; then
    add_env_if_missing "OLLAMA_URL" "$OLLAMA_URL"
  fi
fi

echo ""
echo -e "${GREEN}Done.${NC}"
echo -e "Installed targets: ${BLUE}$TARGETS_CSV${NC}"
echo "Run: source $SHELL_PROFILE"
