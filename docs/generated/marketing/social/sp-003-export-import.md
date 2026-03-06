---
id: sp-003
type: social-posts
audience: marketing
topic: Export/Import
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Social Posts: Export/Import

## Twitter / X

### Launch Post
Your AI memory shouldn't be trapped on one machine. Memories now supports export/import — move knowledge between instances with smart deduplication. No lock-in, no duplicates. github.com/divyekant/memories

### Thread (3-5 tweets)
**1/** Your AI assistant spent weeks learning your codebase. Then you get a new machine. Starting from scratch?

**2/** The problem isn't persistence — tools like Memories solve that. The problem is portability. Knowledge trapped in one instance can't help your teammate or your new laptop.

**3/** Export/import fixes this. Export what you need (all, one project, a date range). Import into any instance. Smart dedup handles the rest.

**4/** Three modes: bulk (fast migration), smart (skips duplicates, keeps newer facts), and AI-powered (resolves contradictions). Auto-backup before every import.

**5/** Your AI memory is now portable. `memories export -o backup.jsonl && memories import backup.jsonl --strategy smart` github.com/divyekant/memories

### One-Liner
AI memory that moves with you. No lock-in.

## LinkedIn

### Announcement Post
Your AI assistant's knowledge shouldn't be trapped on one machine.

We just shipped export/import for Memories — the open-source semantic memory system for AI assistants. Now you can selectively export knowledge (by project, by date range) and import it into any instance with smart conflict resolution.

The import system detects duplicates, keeps newer information when facts conflict, and creates automatic backups before every operation. Three strategy tiers let you choose speed vs. thoroughness.

This was the most requested feature from teams running multiple instances. No more knowledge silos.

#AIMemory #DeveloperTools #OpenSource #AI

### Short Post
Built export/import for AI memory this week. The hardest part wasn't the export — it was making import smart enough to merge two knowledge bases without creating duplicates or losing newer information.

Vector similarity handles the easy cases (exact duplicates). Timestamp comparison handles the medium cases (same fact, different versions). And for the edge cases, there's an AI-powered mode.

What's your approach to sharing context between AI instances?

## Generic / Newsletter

### Blurb
Memories now supports export/import — move AI memory between instances with smart deduplication. Export selectively by project or date range. Import with automatic conflict resolution that skips duplicates and keeps the freshest facts. Auto-backup before every import. github.com/divyekant/memories
