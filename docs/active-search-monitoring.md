# Active Search Monitoring

Use this after the enterprise-readiness rollout to check whether agents are
actually using Memories the way the active-search eval expects.

## What Gets Logged

Claude Code and Codex hooks, plus the OpenCode plugin, append local JSONL telemetry to:

```bash
~/.config/memories/active-search.jsonl
```

Override the path with:

```bash
MEMORIES_ACTIVE_SEARCH_LOG=/path/to/active-search.jsonl
```

Disable local telemetry with:

```bash
MEMORIES_ACTIVE_SEARCH_METRICS=0
```

The log is metadata-only. Claude Code and Codex hooks can store prompt and tool metadata:

- timestamp
- client (`claude-code`, `codex`, or `opencode`)
- session id
- project basename
- SHA-256 prompt hash
- whether the hook classified the prompt as requiring active search
- candidate count and candidate source prefixes
- memory tool name
- memory tool source prefix
- source-prefix quality (`exact_project`, `broad_or_unscoped`, or `other`)

OpenCode currently logs memory tool-call telemetry only (`event: "tool_call"`) through its plugin. It does not emit `prompt_evaluated` events yet, so prompt classification, candidate-count, follow-up-rate, and passive-risk metrics are Claude/Codex hook metrics only for now.

It does not store prompt text, memory text, retrieved snippets, or API keys.

## Check Active Search Health

Run:

```bash
.venv/bin/python scripts/active_search_metrics.py \
  --log ~/.config/memories/active-search.jsonl \
  --followup-window-seconds 300
```

Key fields:

- `active_search_followup_rate`: fraction of required Claude/Codex hook prompts followed by `memory_search` within the window. OpenCode is excluded until it emits `prompt_evaluated` events.
- `passive_risk_prompts`: required Claude/Codex hook prompts with no observed follow-up `memory_search`. OpenCode is excluded until it emits `prompt_evaluated` events.
- `exact_project_searches`: `memory_search` calls scoped to the active project
  family, such as `codex/memories` or `learning/memories`.
- `broad_or_unscoped_searches`: broad family or unscoped searches, such as
  `codex/` or an empty source prefix.
- `by_client`: split between Codex, Claude Code, and OpenCode where event types are available. OpenCode currently contributes `tool_call` events only.

Expected enterprise-ready behavior for Claude/Codex hook clients:

- `active_search_followup_rate` should be close to 1.0.
- `passive_risk_prompts` should stay near 0.
- `broad_or_unscoped_searches` should be rare and should generally appear only
  after exact project prefixes were tried.

## Generic MCP Clients

Generic MCP clients do not have prompt/tool telemetry hooks, so the server
cannot know which prompts should have searched. OpenCode is not generic MCP-only
after plugin setup because its plugin provides prompt-time recall context and
memory tool-call telemetry, but it does not yet log prompt classification events.
For clients without comparable prompt telemetry, use server metrics to check
actual memory tool usage:

```bash
curl -s -H "X-API-Key: $MEMORIES_API_KEY" \
  "$MEMORIES_URL/usage?period=7d" | jq '.operations.search'

curl -s -H "X-API-Key: $MEMORIES_API_KEY" \
  "$MEMORIES_URL/metrics/search-quality?period=7d" | jq

curl -s -H "X-API-Key: $MEMORIES_API_KEY" \
  "$MEMORIES_URL/metrics/temporal-search?period=7d" | jq
```

For generic MCP adoption, watch search volume, search quality feedback,
temporal-search usage, and any application-level user corrections.
