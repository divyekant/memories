---
id: fb-002
type: feature-brief
audience: marketing
topic: CLI
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Feature Brief: Command-Line Interface

## One-Line Summary

Manage AI memories from your terminal -- human-friendly when you type, machine-readable when agents pipe.

## Who It's For

**Primary:** Developers building AI agents who need memory access in scripts and automation pipelines.

**Secondary:** Power users who prefer terminal workflows over API calls or web interfaces.

## The Problem

Today, interacting with Memories means crafting HTTP requests or clicking through the Web UI. There is no way to quickly search, add, or manage memories from the terminal. AI agents that need memory access must include custom HTTP client code. Shell scripts cannot tap into the memory layer without curl gymnastics. Automation pipelines treat Memories as a second-class citizen because there is no native CLI.

The result: friction everywhere a terminal would be the natural interface.

## The Solution

The Memories CLI gives users and agents full terminal access to all 30+ API operations. It detects its environment automatically -- colored, human-readable output when running interactively, clean JSON when piped to another process. One tool for both humans and machines.

## Key Benefits

**Instant terminal access.** Search, add, delete, and manage memories without leaving your terminal. No curl. No Postman. No browser tab. Just `memories search "deployment patterns"` and get results.

**Agent-ready JSON output.** When an AI agent pipes the CLI, output switches to structured JSON automatically. No flags needed. Agents get machine-readable data without any special configuration.

**Batch operations for scale.** Import hundreds of memories from JSONL files. Delete by prefix. Pipe stdin to batch-add. Operations that would require dozens of API calls collapse into one command.

**Smart, layered configuration.** Flags override config files. Config files override environment variables. Environment variables override defaults. The `config show` command tells you exactly where each value came from -- no more guessing which setting is active.

**Zero-config start.** Install with pip and run your first command immediately. No Docker required for CLI-only usage. `pip install memories && memories admin health` gets you from zero to working in seconds.

## Suggested Messaging

**Announcement:** Memories now has a CLI. 30+ commands. Human-friendly in your terminal, JSON-native when agents pipe. Search, add, batch, delete, backup, sync -- everything the REST API offers, now one keystroke away.

**Sales pitch:** Your AI agents need memory access in scripts, pipelines, and automation. The Memories CLI delivers all 30+ API operations with automatic JSON output for machines and colored output for humans. Layered config. Batch operations. Stdin support. Zero setup friction.

**One-liner:** Terminal-native AI memory management -- human when you type, machine when you pipe.

## Target Users

- AI engineers scripting agent workflows that read and write memories
- DevOps teams integrating memory operations into CI/CD pipelines
- Power users who live in the terminal and want fast memory lookups
- Anyone building automation that needs persistent AI context

## Availability

Available now. Install with `pip install memories`.
