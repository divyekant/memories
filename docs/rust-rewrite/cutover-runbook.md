# Rust v6.0.0: upgrade, release and rollback runbook

This runbook is the deliverable of task P7-2. It has four parts:

1. Upgrade an install from Python v5 to Rust v6.
2. Return an install to Python (rollback).
3. Release v6.0.0.
4. Known gaps of v6.0.0.

The production droplet runs the Rust server since 2026-11-17 00:31 UTC
(deploy only, no release). Part 5 records that deploy.

## 1. Upgrade an install from Python v5 to Rust v6

The Rust server never changes or deletes the Python data. It reads
`$DATA_DIR/metadata.json`, writes the collection `<QDRANT_COLLECTION>_v2`
and records the finished migration in `$DATA_DIR/state.db`. The old
collection and `metadata.json` stay as they are until task P7-3
(`prune-legacy`, after the rollback window of 7 days).

CAUTION: Make a backup before the upgrade. Stop the Python server with a long
grace period. A cut-off write can break the start of the Python server.

1. Make a backup with the Python server:

   ```bash
   curl -X POST -H "X-API-Key: $API_KEY" http://localhost:8900/backup
   ```

2. Stop the server: `docker compose stop -t 120 memories`.
3. Copy the data folder and the Qdrant storage to a safe place.
4. Remove the variables of the "Not used" table in
   `docs/rust-rewrite/configuration.md` from `.env` and the compose file.
5. Remove `MALLOC_MMAP_THRESHOLD_` and `MALLOC_TRIM_THRESHOLD_`.
6. Select the Rust image:
   - npm installer: `npx memories-mcp@6.0.0 update` (the standalone compose uses `ghcr.io/divyekant/memories:${MEMORIES_IMAGE_TAG:-latest}`).
   - Repository checkout: `docker compose build memories` (the compose file builds `server/`).
7. Start: `docker compose up -d memories`.
8. Wait for the migration. Only `GET /health` answers during the migration.
   The other routes give HTTP 503. The production data (93,017 records) took
   55 s.
9. Check the result:

   ```bash
   curl -s http://localhost:8900/health
   docker compose exec memories memories-server verify --sample 1000
   ```

   `verify` gives exit code 1 if a count, a field or a sampled vector is
   different.

If the migration or the check fails, the server stops with a message. The
Python data is not changed, so a start of the Python image works.

## 2. Return an install to Python (rollback)

The rules are in `docs/decisions/2026-11-16-rust-rollback.md`. The tools are
`memories-server export-legacy` and the rollback image (`server/rollback/`).

| Case | Action | Data loss |
|---|---|---|
| A. The Rust server took writes | Start the rollback image. It runs `export-legacy`, then the Python server. | None |
| B. The Rust server did not finish its first start | Start the rollback image. The export skips. | None |
| C. The export fails and cannot be repaired | Restore the backup of step 1. | All writes after the upgrade |

CAUTION: Do not start the plain Python image after a failed export. The old
collection can be partly written. Correct the cause and start the rollback
image again.

After case A:

1. Read the log: `docker logs memories 2>&1 | head -50`. The export prints a
   JSON report, then uvicorn starts.
2. Make sure that the Qdrant count of the old collection is equal to the
   count of `metadata.json`.
3. If `$DATA_DIR/rollback-extract-jobs.ndjson` has lines, send them again:

   ```bash
   MEMORIES_API_KEY=<admin key> server/scripts/resend_extract_jobs.py \
     /data/rollback-extract-jobs.ndjson --base-url http://localhost:8900
   ```

To go back to Rust after the Python server took writes, run
`memories-server migrate --merge` in the Rust image first. Without the merge,
the Rust server refuses to start.

To build the rollback image:

```bash
docker build --target core -t memories-python .
docker build -f server/rollback/Dockerfile --build-arg BASE_IMAGE=memories-python -t memories-rollback .
```

## 3. Release v6.0.0

CAUTION: Each step below is outside the repository or cannot be undone. Get
the approval of dk in a message before step 1.

Prerequisites:

- The monitor week on production ended (2026-11-24) with no fault.
- The items of `docs/rust-rewrite/release-readiness.md` that block the
  release are closed.
- `rust-rewrite` is merged into `main` (PR, review, CI green).
- `CHANGELOG.md` has the date in the heading `## [6.0.0]`.

Steps:

1. Publish the image. Run the workflow `publish-image.yml` on `main` with
   `version` = `6.0.0` and `latest` off. The workflow checks that
   `server/Cargo.toml` and `BACKEND_VERSION` are `6.0.0`, builds amd64 and
   arm64 on native runners, runs the health check of each image, and pushes
   `ghcr.io/divyekant/memories:6.0.0`. A workflow with `workflow_dispatch`
   can run only after it is on the default branch.
2. Deploy `ghcr.io/divyekant/memories:6.0.0` on the droplet (part 5). Obey the
   deploy rules: backup, `stop -t 120`, count check before and after.
3. Wait 30 minutes. Check `/health`, the error log, and the count.
4. Run `publish-image.yml` again with `latest` on. This run builds nothing.
   It points `latest` at `6.0.0`.
5. Run `release.yml` with `version` = `6.0.0` and `sha` = the merge commit on
   `main`. It pushes the tag `v6.0.0`, makes the GitHub release from the
   `CHANGELOG.md` section and starts `publish-npm.yml`.
6. Bump the pin in `dk-marketplace` to `6.0.0`.
7. On the Mac: `claude plugin marketplace update dk-marketplace` and
   `claude plugin update memories@dk-marketplace`. The hooks show "Backend
   Update Available" until the client has `BACKEND_VERSION` 6.0.0.

Cloud sessions install `memories-mcp` from npm with a release-age rule of
2 days. They get 6.0.0 about 2 days after step 5.

### Rollback release

If v6.0.0 fails after the release, withdraw it:

1. Point `latest` back at the last Python image tag.
2. Publish the rollback image (Python v5.19.0 plus `export-legacy`) as a
   patch version of v5. A v5 client works with it.
3. Deprecate `memories-mcp@6.0.0` on npm.

## 4. Known gaps of v6.0.0

| Gap | Effect | Workaround |
|---|---|---|
| Backups do not copy `state.db` | `POST /backup` and `POST /restore` skip the webhooks, the extract job queue, the write journal and the migration record. | Copy `state.db` with the data folder. |
| A Rust restore cannot read a Python backup folder | `POST /restore` of a Python backup gives 404 "Backup '<name>' is incomplete". | Restore a Python backup with the Python image. |
| `POST /memory/conflicts/resolve` has no write journal entry | A forced stop in the middle of a resolve can leave the two records half changed. | Run the resolve again. |
| An encoded slash (`%2F`) in a path parameter | Python decodes the path before it routes and gives 404. Rust routes on the raw path and gives the reply of the route. This applies to each route with a path parameter (`docs/decisions/2026-11-16-rust-sync-off.md`). | Do not send `%2F` in a path parameter. |
| No cloud sync, no OpenAI embedder, no `chatgpt-subscription` provider | The server refuses `EMBED_PROVIDER=openai` and `EXTRACT_PROVIDER=chatgpt-subscription`. | Stay on v5 if an install needs them. |
| No training data | `EXTRACT_TRAINING_DATA_DIR` is not used. | None. |
| Project promotion only in the `off` mode | `PROJECT_PROMOTION_MODE=shadow` or `auto` stops the start. | Production uses `off`. |
| No `/docs` page | FastAPI served Swagger UI at `/docs`. Rust serves only `/openapi.json`. | Open `/openapi.json` in an OpenAPI viewer. |
| Extract drops a fact whose AUDN target belongs to another source | The log says "old_id not authorized". Python has the same rule. | None. Not a regression. |
| Many nonstop writers | With 10 or more nonstop writers, the add p95 is 0.8 to 1.4 s. All writes use one write lock, and the ONNX embedder uses most of the CPU. | Real traffic is a few requests each second. |

## 5. Production droplet record

Droplet `dk-services-sfo3`. Compose folder `/opt/dk-services/compose/memories`.
Data: `/opt/dk-services/data/memories` (bind mount `/data`) and
`/opt/dk-services/data/qdrant`.

The compose file loads only `docker-compose.override.yml`. Copy one of these
files over it to select a server:

| File | Server |
|---|---|
| `docker-compose.override.yml.rust` | The current Rust image. Its entrypoint removes `MALLOC_MMAP_THRESHOLD_` and `MALLOC_TRIM_THRESHOLD_`. |
| `docker-compose.override.yml.rust-<sha>` | An earlier Rust image. The Rust images share `state.db` and `memories_v2`, so no migration is necessary between them. |
| `docker-compose.override.yml.rollback` | Python, with the export of the Rust writes at start (rollback case A). |
| `docker-compose.override.yml.python` | Plain Python, no export. Use only for rollback case C. |

CAUTION: Do not run `docker compose build memories` while the Rust override is
active. The `.python` override builds the Python image into `memories:extract`.

| Date (UTC) | Image | Source |
|---|---|---|
| 2026-11-17 00:31 | `memories:rust-7868429` | First switch. Counts 93,017 = 93,017. Migration 55 s. |
| 2026-11-17 03:16 | `memories:rust-a320cc0` | PR #163, graph kept until the next write. |
| 2026-11-17 04:07 | `memories:rust-b4d549a` | PR #164, archive-filtered graph kept too. Counts 93,268 = 93,268. |

The rollback image on the droplet is `memories:rollback-5.19.0`. The Python
image is kept as `memories:extract` and `memories:extract-pre-6.0.0`. The
backups of each switch are in `/opt/dk-services/backups/`.

At the release, the droplet changes from a local image tag to
`ghcr.io/divyekant/memories:6.0.0`. Update the `.rust` override, and keep
the entrypoint that removes the two allocator variables, or remove the two
variables from `.env`.

The scheduled backup (`dk-memories-backup.timer`) needs the Rust version of
`/opt/dk-services/config/maintenance/memories-backup.sh`. The old script
runs `python` in the container and snapshots the old collection. See
`docs/rust-rewrite/release-readiness.md`.
