---
id: lp-002
type: landing-page
audience: marketing
topic: CLI
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Landing Page: Memories CLI

## Hero

### Headline

Your AI memory server, one command away.

### Subheadline

The Memories CLI delivers 30+ operations to your terminal. Human-friendly when you type. Machine-readable when agents pipe. Zero configuration to start.

### CTA

`pip install memories`

---

## Problem Section

### Still writing HTTP clients for memory access?

Your AI agent needs persistent memory. So you write an HTTP client -- manage headers, construct payloads, parse responses, handle errors. Your teammates want quick lookups, so they open a browser and navigate the Web UI. Your automation pipeline needs batch imports, so someone wraps curl in a shell script.

Three workflows. Three different approaches. None of them native to the terminal where developers actually work.

---

## Solution Section

### One CLI for humans and machines

The Memories CLI brings every API operation to your terminal. Search, add, delete, batch import, backup, sync, extract, admin -- the complete surface area as native commands.

The design principle: adapt to your environment. In a terminal, output is colored and readable. In a pipe, output is structured JSON. No flags to toggle. No modes to switch. The CLI detects context and does the right thing.

---

## How It Works

### Step 1: Install

```
pip install memories
```

One package. No Docker required for CLI usage. No build steps.

### Step 2: Connect

```
memories admin health
```

Confirms your Memories server is running and reachable. Default configuration works for standard local setups.

### Step 3: Use

```
memories search "deployment patterns"
memories add --content "Use blue-green deploys" --source "team/decisions"
cat imports.jsonl | memories batch add
```

Search, add, batch import -- every operation is one command.

---

## Features Grid

### Agent-First Output

Automatic JSON when piped. Colored text when interactive. AI agents and humans use the same commands, get the format they need.

### 30+ Commands

Full coverage of every API operation. Search, add, batch, delete, admin, backup, sync, extract, config. No "lite" subset that sends you to curl for the rest.

### Smart Configuration

Layered precedence: flags override config file, config file overrides env vars, env vars override defaults. `memories config show` reveals exactly what is active and where it came from.

### Batch Operations

Import hundreds of memories from JSONL files. Delete by prefix. Pipe stdin to batch-add. Operations that would take dozens of API calls collapse into one command.

### Stdin and Pipe Support

Chain commands with Unix pipes. Stream data through stdin. Branch on exit codes. Build memory-aware automation that follows standard Unix conventions.

### Zero-Config Start

Install and run. Default settings connect to a standard local Memories server with no configuration file needed. Customize only when you need to.

---

## Social Proof

*[Placeholder: Developer testimonials, GitHub stars, integration count, community quotes]*

---

## Final CTA

### Start using Memories from your terminal

```
pip install memories
memories search "your first query"
```

From install to first search in under 30 seconds. Full documentation available with `memories --help`.

**[Install the Memories CLI]**
