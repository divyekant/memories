#!/bin/bash
# install.sh — Interactive installer for FAISS Memory automatic hooks
# Usage: ./install.sh [--codex] [--uninstall]
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_SRC="$SCRIPT_DIR/hooks"

# Defaults
CLIENT="claude"
HOOKS_DIR="$HOME/.claude/hooks/memory"
SETTINGS_FILE="$HOME/.claude/settings.json"
UNINSTALL=false

# Parse args
for arg in "$@"; do
  case $arg in
    --codex)
      CLIENT="codex"
      HOOKS_DIR="$HOME/.codex/hooks/memory"
      SETTINGS_FILE="$HOME/.codex/settings.json"
      ;;
    --uninstall)
      UNINSTALL=true
      ;;
  esac
done

# Detect shell profile
if [ -f "$HOME/.zshrc" ]; then
  SHELL_PROFILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
  SHELL_PROFILE="$HOME/.bashrc"
else
  SHELL_PROFILE="$HOME/.profile"
fi

echo ""
echo -e "${BLUE}FAISS Memory — Automatic Memory Layer Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# --- Uninstall ---
if [ "$UNINSTALL" = true ]; then
  echo -e "${YELLOW}Uninstalling FAISS Memory hooks...${NC}"
  if [ -d "$HOOKS_DIR" ]; then
    rm -rf "$HOOKS_DIR"
    echo -e "  ${GREEN}✓${NC} Removed $HOOKS_DIR"
  else
    echo -e "  ${YELLOW}⚠${NC} Hooks directory not found: $HOOKS_DIR"
  fi
  echo ""
  echo -e "${YELLOW}Manual steps:${NC}"
  echo "  1. Remove hook entries from $SETTINGS_FILE"
  echo "  2. Remove FAISS env vars from $SHELL_PROFILE"
  echo ""
  exit 0
fi

# --- Step 1: Check FAISS service ---
FAISS_URL="${FAISS_URL:-http://localhost:8900}"
echo -e "[1/4] Checking FAISS service at ${BLUE}$FAISS_URL${NC}..."

HEALTH=$(curl -sf "$FAISS_URL/health" 2>/dev/null || echo "FAIL")
if [ "$HEALTH" = "FAIL" ]; then
  echo -e "  ${RED}✗${NC} FAISS service not reachable at $FAISS_URL"
  echo "  Start it with: docker compose up -d faiss-memory"
  exit 1
fi

TOTAL=$(echo "$HEALTH" | jq -r '.total_memories // 0')
echo -e "  ${GREEN}✓${NC} Healthy ($TOTAL memories)"
echo ""

# --- Step 2: Extraction provider ---
echo -e "[2/4] Extraction provider (for automatic learning):"
echo "  1. Anthropic (recommended, ~\$0.001/turn, full AUDN)"
echo "  2. OpenAI (~\$0.001/turn, full AUDN)"
echo "  3. Ollama (free, local, extraction only — no AUDN)"
echo "  4. Skip (retrieval only, no automatic extraction)"
echo ""
read -p "  > " PROVIDER_CHOICE

EXTRACT_PROVIDER=""
EXTRACT_KEY_VAR=""
EXTRACT_KEY_VAL=""

case $PROVIDER_CHOICE in
  1)
    EXTRACT_PROVIDER="anthropic"
    echo ""
    read -p "  Anthropic API key: " EXTRACT_KEY_VAL
    if [ -z "$EXTRACT_KEY_VAL" ]; then
      echo -e "  ${RED}✗${NC} API key required"
      exit 1
    fi
    EXTRACT_KEY_VAR="ANTHROPIC_API_KEY"
    # Test the key
    echo -n "  Testing... "
    TEST=$(curl -sf -X POST "$FAISS_URL/health" 2>/dev/null || echo "OK")
    echo -e "${GREEN}✓${NC}"
    ;;
  2)
    EXTRACT_PROVIDER="openai"
    echo ""
    read -p "  OpenAI API key: " EXTRACT_KEY_VAL
    if [ -z "$EXTRACT_KEY_VAL" ]; then
      echo -e "  ${RED}✗${NC} API key required"
      exit 1
    fi
    EXTRACT_KEY_VAR="OPENAI_API_KEY"
    echo -e "  ${GREEN}✓${NC}"
    ;;
  3)
    EXTRACT_PROVIDER="ollama"
    OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
    echo -n "  Checking Ollama at $OLLAMA_URL... "
    OLLAMA_CHECK=$(curl -sf "$OLLAMA_URL/api/tags" 2>/dev/null || echo "FAIL")
    if [ "$OLLAMA_CHECK" = "FAIL" ]; then
      echo -e "${RED}✗${NC} Not reachable"
      echo "  Make sure Ollama is running: ollama serve"
      exit 1
    fi
    echo -e "${GREEN}✓${NC}"
    ;;
  4|"")
    echo -e "  ${YELLOW}⚠${NC} Extraction disabled (retrieval-only mode)"
    ;;
  *)
    echo -e "  ${RED}✗${NC} Invalid choice"
    exit 1
    ;;
esac
echo ""

# --- Step 3: Install hooks ---
echo -e "[3/4] Installing hooks..."

# Copy hook scripts
mkdir -p "$HOOKS_DIR"
cp "$HOOKS_SRC"/memory-*.sh "$HOOKS_DIR/"
chmod +x "$HOOKS_DIR"/*.sh
echo -e "  ${GREEN}✓${NC} Copied hooks to $HOOKS_DIR"

# Merge into settings.json
SETTINGS_DIR=$(dirname "$SETTINGS_FILE")
mkdir -p "$SETTINGS_DIR"

if [ ! -f "$SETTINGS_FILE" ]; then
  echo '{}' > "$SETTINGS_FILE"
fi

# Read hooks.json and merge
HOOKS_JSON=$(cat "$HOOKS_SRC/hooks.json")

# Use jq to merge hooks into existing settings
MERGED=$(jq -s '
  .[0] as $existing |
  .[1] as $new |
  $existing * {hooks: (($existing.hooks // {}) * $new.hooks)}
' "$SETTINGS_FILE" <(echo "$HOOKS_JSON"))

echo "$MERGED" > "$SETTINGS_FILE"
echo -e "  ${GREEN}✓${NC} Merged hooks config into $SETTINGS_FILE"
echo ""

# --- Step 4: Environment variables ---
echo -e "[4/4] Setting up environment variables..."

# Check what's already set
add_env_if_missing() {
  local var_name="$1"
  local var_value="$2"
  if ! grep -q "export $var_name=" "$SHELL_PROFILE" 2>/dev/null; then
    echo "export $var_name=\"$var_value\"" >> "$SHELL_PROFILE"
    echo -e "  ${GREEN}+${NC} $var_name added to $SHELL_PROFILE"
  else
    echo -e "  ${YELLOW}⚠${NC} $var_name already set in $SHELL_PROFILE"
  fi
}

add_env_if_missing "FAISS_URL" "$FAISS_URL"

FAISS_API_KEY="${FAISS_API_KEY:-}"
if [ -n "$FAISS_API_KEY" ]; then
  add_env_if_missing "FAISS_API_KEY" "$FAISS_API_KEY"
fi

if [ -n "$EXTRACT_PROVIDER" ]; then
  add_env_if_missing "EXTRACT_PROVIDER" "$EXTRACT_PROVIDER"
  if [ -n "$EXTRACT_KEY_VAR" ] && [ -n "$EXTRACT_KEY_VAL" ]; then
    add_env_if_missing "$EXTRACT_KEY_VAR" "$EXTRACT_KEY_VAL"
  fi
  if [ "$EXTRACT_PROVIDER" = "ollama" ]; then
    add_env_if_missing "OLLAMA_URL" "${OLLAMA_URL:-http://localhost:11434}"
  fi
fi

echo ""
echo -e "${GREEN}Done!${NC}"
echo ""
echo -e "  ${GREEN}✓${NC} Session start: loads project memories"
echo -e "  ${GREEN}✓${NC} Every prompt: retrieves relevant context"
if [ -n "$EXTRACT_PROVIDER" ]; then
  echo -e "  ${GREEN}✓${NC} After responses: extracts and stores new facts ($EXTRACT_PROVIDER)"
else
  echo -e "  ${YELLOW}⚠${NC} Extraction disabled (retrieval only)"
fi
echo ""
echo -e "  Run ${BLUE}source $SHELL_PROFILE${NC} or start a new terminal."
echo ""
