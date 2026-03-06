---
doc-type: release-announcement
doc-id: ra-v1.5.0
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Memories v1.5.0: Multi-Auth

## Summary

Memories v1.5.0 introduces prefix-scoped API keys with role-based access control. Every agent and user can now have their own key, scoped to specific memory namespaces with the minimum permissions needed.

Your existing setup works unchanged. Multi-auth is opt-in.

## What Is New

### Prefix-Scoped API Keys

Create dedicated API keys for each agent, integration, or teammate. Each key specifies which memory namespaces (prefixes) it can access. A key scoped to `claude-code/*` can only see and modify memories under that namespace — everything else is invisible.

### Three Role Tiers

Every key is assigned one of three permission levels:

- **Read-only** — search and browse within allowed namespaces
- **Read-write** — full memory operations within scope
- **Admin** — unrestricted access plus key management

### Web UI Key Management

The new API Keys page (admin-gated) provides full lifecycle management: create keys, view usage stats, update permissions, and revoke access. Keys are shown once at creation and stored as SHA-256 hashes.

### Key Management API

Five new endpoints for programmatic key lifecycle:

- Create keys with name, role, and prefix scope
- List keys with usage statistics
- Update key name, role, or prefix scope
- Revoke keys (soft-delete with audit trail)
- Check caller identity and permissions

## What Has Not Changed

- The `API_KEY` environment variable works as before (implicit admin)
- Existing MCP clients and HTTP integrations require no changes
- No migration steps required
- All existing API endpoints behave identically for current users

## By the Numbers

- 346 tests passing
- 96 tests dedicated to authentication and authorization
- Zero breaking changes
- Three role tiers covering all observed access patterns

## Upgrade Path

Update to Memories v1.5.0. Everything works immediately. When you are ready to add scoped keys:

1. Open the Web UI and navigate to API Keys
2. Create a key for each agent, scoped to its namespace
3. Update each agent's configuration with its new key

No migration scripts. No configuration rewrites. No downtime.
