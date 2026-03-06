---
doc-type: feature-brief
doc-id: fb-001
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Feature Brief: Multi-Auth — Prefix-Scoped API Keys

## One-Line Summary

Memories v1.5.0 lets you give every AI agent and teammate their own API key, each scoped to only the memory namespaces they need.

## The Problem

Today, every tool connected to a Memories server shares one key with full access. Claude Code can read Cursor's memories. A compromised webhook can delete everything. Teams sharing a server have no way to limit who sees what.

One key, zero boundaries.

## The Solution

Multi-auth introduces prefix-scoped API keys with role-based access control. Each key is locked to specific memory namespaces and assigned one of three permission levels:

- **Read-only** — search and browse, nothing else
- **Read-write** — full memory operations within allowed namespaces
- **Admin** — unrestricted access plus key management

## Key Benefits

**Agent isolation without separate servers.** Run Claude Code, Cursor, and custom agents against one Memories instance. Each sees only its own data.

**Team access control.** Share a knowledge base with colleagues. Give some people read-only access, others read-write, and keep admin privileges for yourself.

**Least-privilege security.** A compromised integration can only touch its own namespace. It cannot read unrelated memories or wipe the server.

**Zero migration required.** Your existing setup works unchanged. Multi-auth is entirely opt-in. The original `API_KEY` environment variable continues to function as an implicit admin key.

**Self-service key management.** Create, update, and revoke keys through the Web UI or API. Keys are shown exactly once at creation and stored as SHA-256 hashes — the same pattern used by GitHub personal access tokens.

## How It Works

1. An admin creates a new key scoped to specific prefixes (e.g., `claude-code/*`, `learning/*`)
2. The key holder can only read or write memories within those namespaces
3. Search results are automatically filtered to show only permitted memories
4. The Web UI API Keys page provides full lifecycle management (admin-gated)

## Proof Points

- Three role tiers cover every access pattern without configuration complexity
- SHA-256 hashed key storage follows industry-standard security practices
- 346 tests passing, including 96 dedicated auth tests
- Zero breaking changes — fully backward compatible

## Target Users

- AI engineers running multiple agents against one Memories server
- Teams sharing a self-hosted Memories instance
- Anyone who needs isolation without infrastructure overhead

## Availability

Memories v1.5.0, available now.
