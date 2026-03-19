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
   cp .env.example .env
   # Edit .env with your settings:
   # - API_KEY: Your access key (default: generate a random one)
   # - EXTRACT_PROVIDER: anthropic|openai|ollama (default: anthropic)
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

5. Install Claude Code integration:
   ```bash
   bash integrations/claude-code/install.sh
   ```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | (required) | Access key for the Memories API |
| `EXTRACT_PROVIDER` | `anthropic` | LLM provider for extraction (anthropic/openai/ollama) |
| `ANTHROPIC_API_KEY` | — | Required if EXTRACT_PROVIDER=anthropic |
| `OPENAI_API_KEY` | — | Required if EXTRACT_PROVIDER=openai |
| `EMBEDDER_MODEL` | `all-MiniLM-L6-v2` | Sentence transformer model for embeddings |
| `EMBEDDER_AUTO_RELOAD_ENABLED` | `true` | Auto-reload embedder on model change |
| `MAX_EXTRACT_MESSAGE_CHARS` | `120000` | Max chars accepted for extraction |
| `EXTRACT_MAX_INFLIGHT` | `2` | Max concurrent extraction jobs |
| `AUDIT_LOG` | — | Path to audit log file (optional) |

## Backup & Restore

### Backup
The data directory contains all persistent state:
```bash
# Stop the service first for consistency
docker compose stop
# Backup the data directory
tar czf memories-backup-$(date +%Y%m%d).tar.gz data/
# Restart
docker compose up -d
```

### Restore
```bash
docker compose stop
# Remove current data
rm -rf data/
# Restore from backup
tar xzf memories-backup-YYYYMMDD.tar.gz
docker compose up -d
# Verify
curl -s http://localhost:8900/health -H "X-API-Key: $API_KEY" | jq .total_memories
```

### Validation
```bash
# Count memories before and after to verify integrity
curl -s http://localhost:8900/health -H "X-API-Key: $API_KEY" | jq .total_memories
```

## Troubleshooting

### Service not responding
```bash
docker compose logs memories
# Check if port 8900 is available
lsof -i :8900
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
