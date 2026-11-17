# Deployment Guide

## Quick Start (Docker Compose)

The recommended deployment uses Docker Compose with OrbStack or Docker Desktop.

### Prerequisites
- Docker (via OrbStack or Docker Desktop)
- An API key for extraction (Anthropic recommended, OpenAI optional, Ollama for local-only)

### Steps

1. Clone the repository:
   ```bash
   git clone https://github.com/divyekant/memories.git
   cd memories
   ```

2. Configure environment:
   ```bash
   touch .env
   # Edit .env with your settings:
   # - API_KEY: Your access key (default: generate a random one)
   # - EXTRACT_PROVIDER: anthropic|openai|ollama|omlx (empty: no extraction)
   # - ANTHROPIC_API_KEY or OPENAI_API_KEY: For extraction LLM
   ```

3. Start the service:
   ```bash
   docker compose up -d
   ```

4. Verify health:
   ```bash
   curl -s http://localhost:8900/health | jq .
   ```

5. Install the client integrations:
   ```bash
   npx memories-mcp@latest init
   ```

## Configuration Reference

v6 runs the Rust server. The full list of variables, with the v5 variables
that v6 does not use, is in `docs/rust-rewrite/configuration.md`.

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | (required) | Access key for the Memories API |
| `EXTRACT_PROVIDER` | (none) | LLM provider for extraction (`anthropic`, `openai`, `ollama`, `omlx`) |
| `ANTHROPIC_API_KEY` | — | Standard Anthropic API key. Set this or `ANTHROPIC_OAUTH_TOKEN` if EXTRACT_PROVIDER=anthropic |
| `ANTHROPIC_OAUTH_TOKEN` | — | Anthropic subscription OAuth token. Can only call Haiku 4.5 |
| `ANTHROPIC_AUTH` | API key when set | `api_key` or `oauth`. Selects the Anthropic credential |
| `ANTHROPIC_WORKSPACE_ID` | (none) | Workspace ID (`wrkspc_…`). Required for an API key that is not scoped to a workspace (for example `sk-ant-usr-`). Sent as the `anthropic-workspace-id` header |
| `OPENAI_API_KEY` | — | Required if EXTRACT_PROVIDER=openai |
| `MODEL_NAME` | `all-MiniLM-L6-v2` | ONNX embedding model |
| `MAX_EXTRACT_MESSAGE_CHARS` | `120000` | Max chars accepted for extraction |
| `EXTRACT_MAX_INFLIGHT` | `2` | Extraction workers |
| `EXTRACT_FOREIGN_TARGET_ADD` | `true` (Rust server only) | If an extract decision updates or conflicts with a memory of another source, add the fact as a new memory (`fallback_add`, with `foreign_old_id`). Set `false` to get the error action of the Python server |
| `EXTRACT_NOVELTY_GATE` | `1` (on) | Gate extraction ADDs through an explicit novelty check; near-duplicates become noops. Set `0` to disable |
| `EXTRACT_NOVELTY_THRESHOLD` | `0.85` | Cosine similarity above which an extraction ADD is gated as a near-duplicate |
| `AUDIT_LOG` | `false` | `true` writes the audit trail to `$DATA_DIR/audit.db` |

## Upgrade from v5 (Python) to v6 (Rust)

Follow `docs/rust-rewrite/cutover-runbook.md`, part 1. The first start
migrates the v5 data. The v5 data stays unchanged, so the rollback image can
return to v5.

## Backup & Restore

On v6, the records are in Qdrant (collection `memories_v2`), not in
`metadata.json`. A backup must hold the data folder and the Qdrant storage.

CAUTION: Stop both services before the copy. A copy of a running Qdrant can be
inconsistent.

### Backup
```bash
docker compose stop -t 120 memories qdrant
tar czf memories-data-$(date +%Y%m%d).tar.gz data/
docker run --rm -v memories_qdrant-storage:/q -v "$PWD":/out alpine \
  tar czf /out/memories-qdrant-$(date +%Y%m%d).tar.gz -C /q .
docker compose up -d
```

The volume name is `<compose project>_qdrant-storage`. Read it with
`docker volume ls`.

For a backup with no stop, use the API. It writes a Qdrant snapshot and copies
of the SQLite files under `data/backups/`. `POST /restore` restores only the
Qdrant snapshot. Copy the SQLite files back by hand, with the server stopped.

```bash
curl -X POST -H "X-API-Key: $API_KEY" "http://localhost:8900/backup?prefix=manual"
```

### Restore
```bash
docker compose stop -t 120 memories qdrant
rm -rf data/
tar xzf memories-data-YYYYMMDD.tar.gz
docker run --rm -v memories_qdrant-storage:/q -v "$PWD":/in alpine \
  sh -c 'rm -rf /q/* && tar xzf /in/memories-qdrant-YYYYMMDD.tar.gz -C /q'
docker compose up -d
curl -s http://localhost:8900/health -H "X-API-Key: $API_KEY" | jq .total_memories
```

### Validation
```bash
# Count memories before and after to verify integrity
curl -s http://localhost:8900/health -H "X-API-Key: $API_KEY" | jq .total_memories
docker compose exec memories memories-server verify --sample 1000
```

## Troubleshooting

### Service not responding
```bash
docker compose logs memories
# Check if port 8900 is available
lsof -i :8900
```

### Server stops at start with "metadata.json changed"
A v5 server wrote to the data after the migration (for example after a
rollback). Run the merge in the v6 image, then start again:
```bash
docker compose stop -t 120 memories
docker compose run --rm memories migrate --merge
docker compose up -d memories
```

### Extraction not working
```bash
# Check extraction provider status
curl -s http://localhost:8900/extract/status -H "X-API-Key: $API_KEY" | jq .
```

### Hooks not firing
```bash
# Check hook logs
cat ~/.config/memories/hook.log | tail -20
# Verify hooks are registered
grep -l "memory-" ~/.claude/settings.json
```

### Memory search returns no results
```bash
# Check total memory count
curl -s http://localhost:8900/health -H "X-API-Key: $API_KEY" | jq .total_memories
# Test a search directly
curl -s http://localhost:8900/search \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "k": 5}' | jq .
```
