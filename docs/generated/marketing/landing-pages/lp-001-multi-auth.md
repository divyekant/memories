---
doc-type: landing-page
doc-id: lp-001
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Landing Page: Multi-Auth for Memories

## Headline

One server. Many agents. Zero data leakage.

## Subheadline

Memories v1.5.0 introduces prefix-scoped API keys so every AI agent and teammate gets their own credentials, their own namespace, and their own permission level.

---

## Problem Section

### Your agents are sharing more than a server

When multiple AI tools connect to one Memories instance with one API key, every tool can read, write, and delete every memory. There are no boundaries between agents, no limits for teammates, and no containment if something goes wrong.

---

## Solution Section

### Prefix-scoped keys with role-based access

Now you can create dedicated API keys for each agent and user. Each key is locked to specific memory namespaces and assigned a permission level.

**Read-only** — Search and browse memories within allowed namespaces. Perfect for teammates who need to look up knowledge without modifying it.

**Read-write** — Full memory operations within scope. The right fit for AI agents that need to store and retrieve their own context.

**Admin** — Unrestricted access to all memories plus key management. For server operators and power users.

---

## Benefits Section

### Agent isolation

Claude Code, Cursor, and custom tools share one server. Each sees only its own memories.

### Team access control

Give teammates read-only access to shared knowledge bases. Give contributors read-write. Keep admin for yourself.

### Contained blast radius

A compromised integration can only reach the namespaces its key allows. It cannot read unrelated data or wipe the server.

### Zero migration

Your existing API key works unchanged. Multi-auth is opt-in. Adopt it on your schedule.

### Self-service management

Create, update, and revoke keys through the Web UI or API. Keys are SHA-256 hashed and shown only once at creation.

---

## Trust Section

### Built for production

- 346 tests passing, 96 dedicated to authentication
- SHA-256 hashed key storage (GitHub PAT pattern)
- Zero breaking changes from v1.4.0
- Three role tiers covering every access pattern

---

## CTA Section

### Get started in minutes

Update to Memories v1.5.0. Your existing setup works immediately. Create your first scoped key through the Web UI at `/ui`.

**[Update to v1.5.0]**
