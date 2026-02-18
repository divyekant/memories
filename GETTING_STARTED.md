# Getting Started

This is the fastest path to a working Memories setup with optional automatic extraction.

## 1) Start the service

```bash
git clone git@github.com:divyekant/memories.git
cd memories
docker compose -f docker-compose.snippet.yml up -d
```

### Optional: Start with a vector cluster (N nodes)

```bash
python scripts/render_cluster_compose.py \
  --nodes 3 \
  --output docker-compose.cluster.generated.yml

docker compose \
  -f docker-compose.yml \
  -f docker-compose.cluster.generated.yml \
  up -d
```

## 2) Verify API and UI

```bash
curl -s http://localhost:8900/health | jq .
```

Then open `http://localhost:8900/ui` in your browser.

If `/ui` shows 404, rebuild to pick up current web assets:

```bash
docker compose down
docker compose up -d --build memories
```

## 3) Choose your memory mode

| Mode | Cost | What you get |
|------|------|--------------|
| Retrieval only (`EXTRACT_PROVIDER` unset) | Free | Recalls existing memories, does not learn new ones automatically |
| Ollama extraction (`EXTRACT_PROVIDER=ollama`) | Free | Learns new facts with simplified decisions (`ADD/NOOP`) |
| Anthropic/OpenAI extraction | Small API cost (~$0.001/turn typical) | Full AUDN (`ADD/UPDATE/DELETE/NOOP`) and better long-term memory quality |

## 4) Install hooks (recommended)

```bash
./integrations/claude-code/install.sh --auto
```

This auto-detects and configures Claude Code, Codex, and OpenClaw where available.

For guided LLM setup, use:
- [`integrations/QUICKSTART-LLM.md`](integrations/QUICKSTART-LLM.md)

## 5) Verify extraction status (if enabled)

```bash
curl -s http://localhost:8900/extract/status | jq .
```

Expected:
- `enabled: true` when extraction is configured
- selected `provider` and `model`

## 6) First memory smoke test

```bash
curl -X POST http://localhost:8900/memory/add \
  -H "Content-Type: application/json" \
  -d '{"text":"Team prefers strict TypeScript mode","source":"getting-started"}'

curl -X POST http://localhost:8900/search \
  -H "Content-Type: application/json" \
  -d '{"query":"TypeScript preferences","k":3,"hybrid":true}'
```

## 7) Where to go next

- Full architecture: [`docs/architecture.md`](docs/architecture.md)
- Decisions/tradeoffs: [`docs/decisions.md`](docs/decisions.md)
- Full API docs (running service): `http://localhost:8900/docs`
