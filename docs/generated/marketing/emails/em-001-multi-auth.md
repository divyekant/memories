---
doc-type: email-announcement
doc-id: em-001
feature: multi-auth
version: 1.5.0
date: 2026-03-05
audience: marketing
source-tier: direct
hermes-version: 1.0.0
status: final
---

# Email Announcement: Multi-Auth

## Subject: Your AI agents no longer share one key

## Body

Memories v1.5.0 is here with multi-auth: prefix-scoped API keys for your AI memory server.

**The problem:** Every agent connected to your Memories server shares one key with full access. No isolation, no boundaries.

**Now you can:** Give each agent and teammate their own key, scoped to specific memory namespaces with role-based permissions (read-only, read-write, or admin).

**What changes for you:**
- Claude Code and Cursor share a server without seeing each other's data
- Teammates get exactly the access level they need
- A compromised integration can only reach its own namespace

**What does not change:** Your existing setup. Multi-auth is opt-in. Your current API key works unchanged as an implicit admin.

Update to v1.5.0 and create your first scoped key through the Web UI or API.

## CTA

Update to Memories v1.5.0
