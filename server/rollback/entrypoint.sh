#!/bin/sh
# Entrypoint of the rollback image (task P7-1). It writes the changes of the
# Rust server back to the data of the Python server, then starts the Python
# server. export-legacy skips the export when memories_v2 is not the canonical
# store, for example after the Python server wrote to metadata.json.
set -eu
bin="${MEMORIES_SERVER_BIN:-memories-server}"
if ! "$bin" export-legacy; then
  echo "memories-rollback: export-legacy failed, so the Python server does not start." >&2
  echo "memories-rollback: read the error above. Do not start the Python server without this image until the export finishes: the old collection can be partly written." >&2
  echo "memories-rollback: Correct the cause, then start the image again: the export runs again and finishes a stopped export." >&2
  exit 1
fi
exec "$@"
