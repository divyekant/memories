---
doc-type: blog-post
doc-id: bp-001
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Your AI Agents Are Sharing a Brain — And That Is a Problem

If you run multiple AI agents against a shared memory server, you have an isolation problem. Claude Code can read memories meant for Cursor. A webhook integration can delete knowledge it never created. Every tool on the server has the same unrestricted key and the same unrestricted access.

This is the equivalent of giving every employee the root password to your database. It works — until it doesn't.

## The Shared-Key Problem

Most AI memory setups start simple: one server, one API key, one agent. It is clean. It is easy. Then a second agent shows up — maybe Cursor for code navigation alongside Claude Code for architecture work. Then a teammate wants access to the shared knowledge base. Then you connect a CI hook that writes build context. Before long, five different tools share one key with full read-write-delete access to everything.

The risks compound quickly:

**Data leakage.** Agent A can search and read Agent B's private memories. Project-specific knowledge bleeds across boundaries you thought existed.

**Accidental destruction.** A cleanup script meant for one namespace can wipe unrelated memories. With one key and no scoping, there is no guardrail.

**All-or-nothing access.** You cannot give a teammate read-only access to a shared knowledge base. You cannot limit a new integration to a single namespace. Everyone gets everything, or no one gets anything.

**Blast radius.** If one integration is compromised, the attacker has full access to your entire knowledge base. There is no containment. Every architectural decision, every debugging insight, every piece of institutional knowledge is exposed through a single point of failure.

These are not hypothetical risks. They are the natural consequence of a single-key model applied to a multi-agent world.

## Introducing Multi-Auth

Memories v1.5.0 solves this with prefix-scoped API keys and role-based access control. Every agent and user gets their own key, scoped to the exact memory namespaces they need.

Here is how it works:

**Namespace scoping.** When you create a key, you specify which memory prefixes it can access. A key scoped to `claude-code/*` can only read and write memories under that namespace. Everything else is invisible.

**Three role tiers.** Each key gets one of three permission levels: read-only (search and browse), read-write (full operations within scope), or admin (unrestricted access plus key management). Three tiers cover every access pattern without turning configuration into a full-time job.

**Automatic filtering.** Search results are filtered at the API layer based on the caller's allowed prefixes. There is no way to accidentally access memories outside your scope, even with a crafted query. This applies to every operation — search, add, delete, and extraction.

**Self-service lifecycle.** Create, rename, re-scope, and revoke keys through the Web UI or API. No server restarts. No configuration file edits. The API Keys page in the Web UI provides full visibility into each key's usage, last access time, and permission scope.

## What This Means in Practice

**Agent isolation without separate servers.** Run Claude Code, Cursor, and custom tools against one Memories instance. Each agent gets its own key scoped to its own namespace. They share infrastructure, not data.

**Team access control.** Share a knowledge base with your team. Give some people read-only access for lookups. Give others read-write access for contribution. Keep admin access for key management and server operations.

**Contained blast radius.** If a webhook integration is compromised, the attacker can only access the namespaces that key was scoped to. They cannot read unrelated memories. They cannot wipe the server. The damage is contained.

**Zero migration.** Your existing `API_KEY` environment variable continues to work as an implicit admin key with full access to everything — exactly as before. Existing MCP clients, integrations, and workflows are unchanged. Multi-auth is entirely opt-in. There is no forced adoption timeline. You can run v1.5.0 identically to v1.4.0 for as long as you want, and add scoped keys only when the need arises.

## Built on Industry-Standard Security

Keys follow the GitHub personal access token pattern: generated with a `mem_` prefix, shown exactly once at creation, and stored as SHA-256 hashes. They are never retrievable after creation.

Key management is available through the Web UI (admin-gated) and the API. You can create, rename, re-scope, and revoke keys without restarting the server. Revoked keys are soft-deleted, preserving audit trails. The Web UI shows usage statistics for each key — when it was last used and how many requests it has served — so you always know which keys are active and which can be cleaned up.

## The Numbers

We shipped multi-auth with 346 tests passing, including 96 tests dedicated specifically to authentication and authorization. Zero breaking changes. Zero migration steps. Three role tiers that cover every access pattern we have seen across single-user, multi-agent, and team deployments.

## Get Started

Update to Memories v1.5.0. Your existing setup works immediately — nothing breaks. When you are ready to add isolation:

1. Open the Web UI at `/ui` and navigate to the API Keys page
2. Create a key for each agent, scoped to its namespace
3. Update each agent's configuration with its dedicated key

That is it. No migration scripts. No configuration files to rewrite. No downtime. The entire process takes minutes, and every existing integration keeps working while you roll out scoped keys at your own pace.

Your AI agents can finally share a server without sharing each other's knowledge.
