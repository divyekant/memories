---
id: op-003
type: one-pager
audience: marketing
topic: Export/Import
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Export/Import

## The Problem
AI assistants accumulate valuable context — decisions, patterns, project knowledge — over weeks of use. But that knowledge is trapped in a single instance. Moving machines means starting over. Sharing with teammates means duplicating work. Merging knowledge bases means risking contradictions and duplicates.

## The Solution
Memories export/import creates portable snapshots of AI memory that transfer cleanly between instances. Smart conflict resolution means you never accidentally create duplicates, and automatic backups mean you can always roll back.

## Key Benefits
- **Portable knowledge:** Export to a standard file, import into any instance
- **Smart deduplication:** Automatically skips duplicates and keeps newer versions of conflicting facts
- **Selective export:** Filter by project or date range — share only what's relevant
- **Built-in safety:** Automatic backup before every import, restorable with one command

## How It Works
1. **Export** — Select the memories you want (all, by project, by date) and save to a file
2. **Import** — Load the file into your target instance with your chosen strategy
3. **Done** — Smart dedup handles conflicts, backup protects your data

## Who It's For
- **Development teams:** Share project learnings between personal and shared instances
- **Infrastructure teams:** Migrate AI memory during upgrades or cloud migrations
- **Individual developers:** Keep knowledge synchronized across work and personal setups

## Get Started
```bash
memories export -o knowledge.jsonl
memories import knowledge.jsonl --strategy smart
```
