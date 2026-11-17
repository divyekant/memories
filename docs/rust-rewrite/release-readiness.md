# Rust v6.0.0: release readiness

State on 2026-11-17. Production runs Rust 6.0.0 (image `memories:rust-b4d549a`)
since 00:31 UTC. The monitor week ends on 2026-11-24. Nothing is released:
no merge to `main`, no tag, no npm package, no GitHub release, no `latest`
image, no marketplace pin.

## Done

| Item | Where |
|---|---|
| Gates G3 to G6 | `docs/rust-rewrite/gates/`. G4, G5 and G6 accepted by dk with conditions. |
| Rollback tool and drill (P7-1) | `server/rollback/`, `server/scripts/rollback_drill.sh` |
| Production deploy and load test | `docs/rust-rewrite/cutover-runbook.md`, part 5 |
| Version 6.0.0 in the client files | `BACKEND_VERSION`, `mcp-server/package.json`, plugin manifests |
| Health check command | `memories-server healthcheck` |
| Compose files build the Rust server | `docker-compose.yml`, `docker-compose.snippet.yml` |
| Image workflow | `.github/workflows/publish-image.yml` |
| Release notes | `CHANGELOG.md`, section `[6.0.0]` |
| Extract keeps a fact whose AUDN target belongs to another source | PR #165, merged into `rust-rewrite`. Production runs it only after a deploy of an image that includes it. |
| Upgrade, rollback and release runbook | `docs/rust-rewrite/cutover-runbook.md` |
| Configuration reference | `docs/rust-rewrite/configuration.md` |
| README, Getting Started, deployment, architecture, `server/README.md`, `CLAUDE.md` | Updated for v6 |
| Rust backup script on the droplet | Fixed 2026-11-17 04:38 UTC. Retention approved by dk: all backups for 24 h, 7 daily; Google Drive every 6 h for 7 days, then weekly for 8 weeks. |
| Droplet disk | Old backups removed. Disk at 48% on 2026-11-17. |

## Needs dk

| # | Item | Blocks the release | Note |
|---|---|---|---|
| 1 | Approve the release after the monitor week (2026-11-24) | Yes | Plan task P7-2. Read the monitor log (`~/rust-monitor/metrics.ndjson`, `errors.log`) first. |
| 2 | Close gate G7 (shadow run, task P6-2) | Yes | The plan needs G1 to G7 before P7-2. The production deploy of 2026-11-17 took the place of the shadow run. dk decides if the monitor week closes G7. Check G1 and G2 on the tracker. |
| 3 | Merge `rust-rewrite` into `main` | Yes | A PR with a review and green CI. `develop` is stale at v5.16.2; do not use it. |
| 4 | Set the release date in `CHANGELOG.md` | Yes | Heading `## [6.0.0] - <date>`. `release.yml` reads this section. |
| 5 | Run the release steps of the runbook, part 3 | Yes | Image, droplet switch to `ghcr.io/divyekant/memories:6.0.0`, 30 min check, `latest`, `release.yml`, marketplace pin. |
| 6 | Mac: update the plugin and the local install | No | `claude plugin marketplace update dk-marketplace`, `claude plugin update memories@dk-marketplace`. The Mac hooks show "Backend Update Available" until then. |
| 7 | Remove the droplet monitor cron line | No | After the week. |
| 8 | Approve task P7-3 (delete of the v5 data and the Python code) | No | After the 7-day rollback window. A rollback is not possible after `prune-legacy`. |
| 9 | Decide on the mixed-load add p95 | No | G5 passes on production (197 ms at 5 workers). With 10 or more nonstop writers the add p95 is 0.8 to 1.4 s. dk accepted this for now. |

## Open gaps to document or fix later

| Gap | Plan |
|---|---|
| A restore does not put back the SQLite files (the backup copies them) | Documented in the runbook, part 4. Decide if a later 6.0.x restores them. |
| A Rust restore cannot read a v5 backup folder | Documented. Restore a v5 backup with the v5 image. |
| `POST /memory/conflicts/resolve` has no write journal entry | Fix in a later 6.0.x. |
| `%2F` in a path parameter routes differently | Documented. |
| `scripts/backup.sh` copies the v5 files | Port the fix of the droplet backup script to the repository script. README has a caution. |
| `EXTRACT_TRAINING_DATA_DIR` is not used | No training pairs while on Rust. Port only if fine-tuning restarts. |
| No latency view in the web UI | Pending: a separate thread builds a latency and health dashboard. `GET /metrics` already has avg, p95 and max latency for each route. |
| No cloud sync, no OpenAI embedder, no `chatgpt-subscription` provider | Port only if an install needs one. |
| The new `publish-image.yml` has not run | It can run only after it is on `main`. The first run is release step 1. |
| Droplet allocator variables | The `.rust` override removes `MALLOC_MMAP_THRESHOLD_` and `MALLOC_TRIM_THRESHOLD_` in its entrypoint. At the release, remove them from `.env` too. |
