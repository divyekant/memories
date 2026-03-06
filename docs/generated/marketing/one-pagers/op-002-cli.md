---
id: op-002
type: one-pager
audience: marketing
topic: CLI
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Memories CLI: Full Memory Access From Your Terminal

## The Problem

AI agents need persistent memory. Today, accessing that memory means writing HTTP client code, managing request headers, and parsing JSON responses manually. Shell scripts resort to curl one-liners. Automation pipelines treat the memory layer as an afterthought because there is no first-class terminal tool.

Developers waste time on plumbing instead of building.

## The Solution

The Memories CLI delivers all 30+ API operations as terminal commands. Search, add, batch import, delete, backup, sync, extract -- everything the REST API offers, now accessible with a single command.

The CLI is agent-first: it detects whether output is going to a human or a pipe and adapts automatically. Colored, readable output in your terminal. Structured JSON when an agent or script consumes the result.

## Why It Matters

**For humans:** Search memories in seconds. `memories search "auth patterns"` returns results faster than opening a browser. Colored output, clean formatting, no setup.

**For agents:** Pipe-native JSON output means AI agents can call the CLI and parse results without any configuration. Exit codes enable branching logic. Stdin support enables streaming workflows.

**For automation:** Batch operations accept JSONL files. Import hundreds of memories in one command. Delete by prefix. Chain commands with pipes. Build memory-aware pipelines in minutes.

## How It Works

| Step | Command | What Happens |
|------|---------|-------------|
| Install | `pip install memories` | One package, no Docker needed |
| Verify | `memories admin health` | Confirms server connection |
| Search | `memories search "topic"` | Hybrid search with ranked results |
| Add | `memories add --content "fact" --source "project"` | Store a new memory |
| Batch | `cat facts.jsonl \| memories batch add` | Import at scale |

## Smart Configuration

Flags override config files. Config files override environment variables. Environment variables override defaults. Run `memories config show` to see exactly which value is active and where it came from. No more guessing.

## Who It's For

- Developers building AI agents with persistent memory needs
- DevOps teams adding memory operations to automation pipelines
- Power users who prefer the terminal over web interfaces

## Get Started

```
pip install memories
memories admin health
memories search "your first query"
```

Zero configuration required. Works immediately against a running Memories server.
