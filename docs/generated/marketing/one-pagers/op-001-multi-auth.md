---
doc-type: one-pager
doc-id: op-001
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Multi-Auth: Scoped API Keys for Memories

## The Problem

Shared AI memory servers have zero isolation. Every connected agent — Claude Code, Cursor, custom tools — can read and modify every other agent's data. One compromised integration can wipe the entire knowledge base. Teams sharing a server all have identical, unrestricted access.

## The Solution

Memories v1.5.0 introduces prefix-scoped API keys with three role tiers. Each agent or user gets their own key, locked to specific memory namespaces with the minimum permissions needed.

## Three Roles, Zero Complexity

| Role | Can Do | Use Case |
|------|--------|----------|
| Read-only | Search and browse | Teammate lookups, dashboards |
| Read-write | Full memory operations within scope | Agent-specific workspaces |
| Admin | Everything, plus key management | Server operators |

## Why It Matters

**Run multiple agents safely.** Claude Code and Cursor share one server without seeing each other's data.

**Control team access.** Give teammates exactly the access they need — nothing more.

**Contain compromises.** A breached integration can only reach its own namespace. The rest of your knowledge base stays untouched.

**No migration.** Your existing key still works. Multi-auth is opt-in.

## Security

Keys are hashed with SHA-256 and shown only once at creation — the same approach GitHub uses for personal access tokens. All memory operations enforce prefix-scoped filtering at the API layer.

## By the Numbers

- 346 tests passing, 96 dedicated to auth
- Zero breaking changes
- Three role tiers covering all access patterns

## Get Started

Update to Memories v1.5.0. Your existing setup works unchanged. When you are ready, create your first scoped key through the Web UI or API.
