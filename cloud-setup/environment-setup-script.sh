#!/usr/bin/env bash
# Paste THIS into the "Setup script" field of your Claude Code cloud Environment.
# It clones the bootstrap repo and runs setup.sh. All real logic lives in
# setup.sh so it stays version-controlled instead of buried in a UI textbox.
#
# Requires these Environment Variables (set them in the same UI):
#   MEMORIES_URL      https://your-backend
#   MEMORIES_API_KEY  <backend api key>
#   SETUP_GH_TOKEN    <GitHub PAT with read access to your private repos>
set -euo pipefail

BOOTSTRAP_REPO="${BOOTSTRAP_REPO:-divyekant/claude-cloud-setup}"
DEST="$HOME/.cloud-setup"

rm -rf "$DEST"
git clone --depth 1 "https://${SETUP_GH_TOKEN}@github.com/${BOOTSTRAP_REPO}.git" "$DEST"
bash "$DEST/setup.sh"
