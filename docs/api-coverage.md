# API Coverage Matrix

Feature availability across the three interfaces: REST API, MCP tools, and CLI commands.

## Memory CRUD

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Add Memory | POST /memory/add | memory_add | `add` | Full parity |
| Add Batch | POST /memory/add-batch | -- | `batch add` | No MCP equivalent |
| Get Memory | GET /memory/{id} | -- | `get` | No MCP equivalent |
| Get Batch | POST /memory/get-batch | -- | `batch get` | No MCP equivalent |
| Delete Memory | DELETE /memory/{id} | memory_delete | `delete` | Full parity |
| Delete Batch | POST /memory/delete-batch | memory_delete_batch | `batch delete` | Full parity |
| Delete by Source | POST /memory/delete-by-source | -- | `delete-by-source` | No MCP equivalent |
| Delete by Prefix | POST /memory/delete-by-prefix | -- | `delete-by-prefix` | No MCP equivalent |
| Bulk Delete | DELETE /memories?source= | memory_delete_by_source | -- | MCP uses source prefix match |
| Patch Memory | PATCH /memory/{id} | -- | -- | API-only |
| Upsert | POST /memory/upsert | -- | `upsert` | No MCP equivalent |
| Upsert Batch | POST /memory/upsert-batch | -- | `batch upsert` | No MCP equivalent |
| List Memories | GET /memories | memory_list | `list` | Full parity |
| Count | GET /memories/count | memory_count | `count` | Full parity |
| Is Novel | POST /memory/is-novel | memory_is_novel | `is-novel` | Full parity |
| Supersede | POST /memory/supersede | -- | -- | API-only |

## Search

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Search | POST /search | memory_search | `search` | Full parity |
| Search Explain | POST /search/explain | -- | -- | Admin-only, API-first |
| Search Batch | POST /search/batch | -- | `batch search` | No MCP equivalent |
| Search Feedback | POST /search/feedback | memory_is_useful | -- | MCP tool wraps the API endpoint |

## Extraction

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Extract (submit) | POST /memory/extract | memory_extract | `extract submit` | MCP polls to completion internally |
| Extract (poll) | GET /memory/extract/{job_id} | -- | `extract status`, `extract poll` | MCP handles polling internally |
| Extract Debug | POST /memory/extract (debug=true) | -- | -- | API-only, returns detailed trace |
| Extract System Status | GET /extract/status | -- | -- | API-only |

## Conflicts & Links

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| List Conflicts | GET /memory/conflicts | memory_conflicts | `admin conflicts` | Full parity |
| Add Link | POST /memory/{id}/link | -- | -- | API-only |
| Get Links | GET /memory/{id}/links | -- | -- | API-only |
| Remove Link | DELETE /memory/{id}/link/{target_id} | -- | -- | API-only |

## Folders

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| List Folders | GET /folders | -- | `folders` | No MCP equivalent |
| Rename Folder | POST /folders/rename | -- | -- | API-only, admin-only |

## Maintenance (Admin)

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Stats | GET /stats | memory_stats | `admin stats` | MCP returns lightweight version |
| Health | GET /health | -- | `admin health` | No MCP equivalent |
| Health Ready | GET /health/ready | -- | -- | API-only |
| Metrics | GET /metrics | -- | `admin metrics` | No MCP equivalent |
| Usage | GET /usage | -- | `admin usage` | No MCP equivalent |
| Deduplicate | POST /memory/deduplicate | -- | `admin deduplicate` | No MCP equivalent |
| Consolidate | POST /maintenance/consolidate | -- | `admin consolidate` | No MCP equivalent |
| Prune | POST /maintenance/prune | -- | `admin prune` | No MCP equivalent |
| Reload Embedder | POST /maintenance/embedder/reload | -- | `admin reload-embedder` | No MCP equivalent |
| Re-embed | POST /maintenance/reembed | -- | -- | API-only |
| Compact (discover clusters) | POST /maintenance/compact | -- | -- | API-only |
| Build Index | POST /index/build | -- | -- | API-only |

## Backup & Restore

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| List Backups | GET /backups | -- | `backup list` | No MCP equivalent |
| Create Backup | POST /backup | -- | `backup create` | No MCP equivalent |
| Restore Backup | POST /restore | -- | `backup restore` | No MCP equivalent |

## Cloud Sync

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Sync Status | GET /sync/status | -- | `sync status` | No MCP equivalent |
| Sync Upload | POST /sync/upload | -- | `sync upload` | No MCP equivalent |
| Sync Download | POST /sync/download | -- | `sync download` | No MCP equivalent |
| Sync Snapshots | GET /sync/snapshots | -- | `sync snapshots` | No MCP equivalent |
| Sync Restore | POST /sync/restore/{name} | -- | `sync restore` | No MCP equivalent |

## Export / Import

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Export (NDJSON) | GET /export | -- | `export` | No MCP equivalent |
| Import (NDJSON) | POST /import | -- | `import` | No MCP equivalent |

## Events & Webhooks

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Event Stream (SSE) | GET /events/stream | -- | -- | API-only |
| Recent Events | GET /events/recent | -- | -- | API-only |
| Register Webhook | POST /webhooks | -- | -- | API-only, admin-only |
| List Webhooks | GET /webhooks | -- | -- | API-only, admin-only |
| Delete Webhook | DELETE /webhooks/{id} | -- | -- | API-only, admin-only |

## Quality Metrics

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Search Quality | GET /metrics/search-quality | -- | -- | API-only |
| Extraction Quality | GET /metrics/extraction-quality | -- | -- | API-only, admin-only |
| Quality Summary | GET /metrics/quality-summary | -- | -- | API-only, admin-only |
| Failure Metrics | GET /metrics/failures | -- | -- | API-only, admin-only |

## Auth / API Keys

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Current Key Info | GET /api/keys/me | -- | -- | API-only |
| Create Key | POST /api/keys | -- | -- | API-only, admin-only |
| List Keys | GET /api/keys | -- | -- | API-only, admin-only |
| Update Key | PATCH /api/keys/{id} | -- | -- | API-only, admin-only |
| Delete Key | DELETE /api/keys/{id} | -- | -- | API-only, admin-only |

## Audit

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Query Audit Log | GET /audit | -- | -- | API-only, admin-only |
| Purge Audit Log | POST /audit/purge | -- | -- | API-only, admin-only |

## Web UI

| Feature | API | MCP | CLI | Notes |
|---------|-----|-----|-----|-------|
| Dashboard | GET /ui | -- | -- | Browser-only |

## Coverage Gaps

The following features are API-only with no MCP or CLI equivalent. These are intentional -- they are either admin-only operations or specialized features that don't suit agent use:

- **Search Explain** -- detailed scoring breakdown, admin-only. Useful for debugging search quality but not appropriate for agent workflows.
- **Extract Debug** -- `debug=true` parameter on POST /memory/extract returns the full extraction trace including LLM prompt/response. API-only for diagnostics.
- **Memory Links** -- typed relationships between memories (supersedes, related_to, blocked_by, caused_by, reinforces). API-only, planned for MCP exposure.
- **Patch Memory** -- partial field updates. Agents should use supersede or extract (AUDN) instead.
- **Events & Webhooks** -- real-time event subscriptions. Infrastructure-level feature, not agent-facing.
- **Quality Metrics** -- search quality, extraction quality, failure dashboards. Admin observability.
- **API Key Management** -- creating/rotating scoped keys. Admin operation via API or UI.
- **Audit Log** -- append-only audit trail query/purge. Admin compliance feature.
