#!/usr/bin/env bash
# Start the Python reference server for a parity run.
# Usage: parity/run_python_server.sh <data dir> <port>
# PARITY_PYTHON_QDRANT_URL selects a Qdrant server. The default is the local (embedded) mode.
set -euo pipefail
data_dir="$1"
port="$2"
mkdir -p "$data_dir"
cd "$(dirname "$0")/.."
# The provider points at a closed port, so each extract job ends with a provider failure (wire rule W7).
# PARITY_OLLAMA_URL points it at a stub with recorded replies (task P5-3).
exec env \
  API_KEY="${PARITY_ADMIN_KEY:-parity-admin-key}" \
  DATA_DIR="$data_dir" \
  WORKSPACE_DIR="$PWD/parity/workspace" \
  QDRANT_URL="${PARITY_PYTHON_QDRANT_URL:-}" \
  MEMORIES_PERSIST_DELAY_SEC=0 \
  USAGE_TRACKING=true \
  AUDIT_LOG=true \
  EXTRACT_PROVIDER=ollama \
  OLLAMA_URL="${PARITY_OLLAMA_URL:-http://127.0.0.1:9}" \
  MAINTENANCE_ENABLED=false \
  CLOUD_SYNC_ENABLED=false \
  PYTHONHASHSEED=0 \
  "${PYTHON:-.venv/bin/python}" -m uvicorn app:app --host 127.0.0.1 --port "$port"
