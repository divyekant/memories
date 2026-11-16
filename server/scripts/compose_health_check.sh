#!/usr/bin/env bash
# Start the bundled compose file with a local image, with no change to the file.
# Pass when Docker reports the memories container as healthy.
# Usage: scripts/compose_health_check.sh <tag of ghcr.io/divyekant/memories> [timeout seconds]
set -euo pipefail

tag="${1:?image tag}"
timeout="${2:-180}"
root="$(cd "$(dirname "$0")/../.." && pwd)"
compose_file="$root/mcp-server/assets/backend/docker-compose.standalone.yml"
project="memories-health-$$"

compose() { MEMORIES_IMAGE_TAG="$tag" docker compose -f "$compose_file" -p "$project" "$@"; }
trap '{ compose logs memories | tail -50; } || true; compose down -v >/dev/null 2>&1 || true' EXIT

compose up -d --pull missing
cid="$(compose ps -q memories)"
for ((i = 0; i < timeout; i += 5)); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$cid")"
  echo "health: $status (${i}s)"
  if [[ "$status" == healthy ]]; then
    echo "OK: the memories container is healthy."
    exit 0
  fi
  if [[ "$status" == unhealthy || "$(docker inspect -f '{{.State.Running}}' "$cid")" != true ]]; then
    break
  fi
  sleep 5
done
echo "FAIL: the memories container is not healthy." >&2
exit 1
