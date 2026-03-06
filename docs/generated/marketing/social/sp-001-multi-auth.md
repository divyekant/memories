---
doc-type: social-posts
doc-id: sp-001
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Social Posts: Multi-Auth

## Twitter / X

### Post 1 (Launch)

Memories v1.5.0: prefix-scoped API keys. Every AI agent gets its own key, locked to its own namespace. Share one server, not one brain. Zero breaking changes.

### Post 2 (Problem)

Your AI agents share one memory server with one key. Claude Code can read Cursor's memories. A bad webhook can wipe everything. That changes today with multi-auth in Memories v1.5.0.

### Post 3 (Proof)

346 tests. 96 auth-specific. Zero breaking changes. Three role tiers. SHA-256 hashed keys. Memories v1.5.0 multi-auth is the update your shared AI memory server needed.

---

## LinkedIn

### Post 1 (Launch)

Excited to ship multi-auth in Memories v1.5.0.

The problem: shared AI memory servers have zero isolation. Every agent reads every other agent's data. One compromised key exposes everything.

The fix: prefix-scoped API keys with role-based access control. Three tiers (read-only, read-write, admin) cover every access pattern. Each key is locked to specific memory namespaces.

What this means in practice:
- Claude Code and Cursor share a server without seeing each other's data
- Teammates get read-only access to shared knowledge bases
- A compromised integration can only reach its own namespace

Zero breaking changes. Your existing setup works unchanged. Multi-auth is opt-in.

346 tests passing, 96 dedicated to auth. SHA-256 hashed keys following the GitHub PAT pattern.

#AI #DeveloperTools #OpenSource #SemanticMemory #Security

### Post 2 (Thought Leadership)

If you run multiple AI agents against one memory server, ask yourself: can Agent A read Agent B's private knowledge?

For most setups, the answer is yes.

That is the shared-key problem. One key, full access, zero boundaries. It works until a cleanup script wipes the wrong namespace or a compromised webhook reads data it was never meant to see.

In Memories v1.5.0, we shipped prefix-scoped API keys. Every agent gets its own key scoped to its own namespace. Three role tiers enforce least privilege. The blast radius of any single compromise is limited to one namespace.

Your agents can share infrastructure without sharing data.

#AIEngineering #Security #DevTools #MemoryLayer

---

## Newsletter Blurb

### Subject: Memories v1.5.0 — Your agents no longer share one key

Your AI agents deserve their own credentials. Memories v1.5.0 introduces prefix-scoped API keys with three role tiers: read-only, read-write, and admin. Each key is locked to specific memory namespaces, so Claude Code and Cursor can share one server without accessing each other's data.

The best part: zero migration. Your existing setup works unchanged. Multi-auth is opt-in. Create your first scoped key through the Web UI or API whenever you are ready.

346 tests passing. 96 auth-specific. Zero breaking changes.
