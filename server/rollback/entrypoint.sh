#!/bin/sh
# Entrypoint of the rollback image (task P7-1). It writes the changes of the
# Rust server back to the data of the Python server, then starts the Python
# server. export-legacy skips the export when memories_v2 is not the canonical
# store, for example after the Python server wrote to metadata.json.
set -eu
bin="${MEMORIES_SERVER_BIN:-memories-server}"
if ! "$bin" export-legacy; then
  echo "memories-rollback: export-legacy failed, so the Python server does not start." >&2
  echo "memories-rollback: metadata.json did not change. Run 'memories-server export-legacy' again, or restore the backup of the cutover." >&2
  exit 1
fi
exec "$@"
