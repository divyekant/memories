---
id: fb-003
type: feature-brief
audience: marketing
topic: Export/Import
status: draft
generated: 2026-03-05
source-tier: direct
context-files: [docs/plans/2026-03-05-export-import-design.md]
hermes-version: 1.0.0
---

# Feature Brief: Export/Import

## One-Liner
Move your AI memories between instances — selectively, safely, and without duplicates.

## What It Is
Export/import lets you create portable snapshots of your AI memory and transfer them between Memories instances. You choose what to export (by project or date range), pick an import strategy that handles conflicts intelligently, and the system takes care of the rest — including automatic backups before every import.

## Who It's For
- **Primary:** Teams running multiple Memories instances who need to share knowledge between them — development teams with per-developer instances feeding into a shared team instance
- **Secondary:** Individual users migrating between machines, upgrading infrastructure, or maintaining separate work/personal knowledge bases

## The Problem It Solves
AI assistants build valuable context over weeks and months — architectural decisions, debugging patterns, project conventions. But that knowledge gets trapped in a single instance. Moving to a new machine means starting from scratch. Sharing learnings with teammates requires manual re-entry. And merging two knowledge bases risks creating hundreds of contradictory or duplicate entries.

## Key Benefits
- **Zero lock-in:** Your knowledge is portable. Export to a standard file format anytime.
- **Smart merging:** Import into an instance that already has data — the system automatically detects duplicates and keeps the most recent information.
- **Selective control:** Export everything, or just one project's memories from a specific time period.
- **Safety net:** Every import creates an automatic backup first. One command restores everything if needed.
- **Namespace flexibility:** Reorganize how your knowledge is organized during import — change project names, consolidate namespaces, or separate shared from private.

## How It Works (Simplified)
Export creates a portable file containing your selected memories. Import reads that file and adds the memories to your target instance. Before importing, the system photographs your current state (automatic backup). Then it checks each incoming memory: if it's brand new, it adds it. If it's identical to something you already have, it skips it. If it's a newer version of existing knowledge, it keeps the fresh one. The result: a clean, up-to-date knowledge base with no duplicates.

## Competitive Context
Competitive positioning requires additional context — provide competitive analysis docs to populate this section.

## Proof Points
- Tested with 8,867 memories in production — full export + round-trip import verified
- Smart dedup correctly identifies duplicates via vector similarity, preventing bloat
- Three strategy tiers let users choose their speed/accuracy tradeoff

## Suggested Messaging
- **Announcement:** "Your AI memories are now portable. Export knowledge from any instance, import into another — with smart deduplication that prevents duplicates and keeps your facts fresh."
- **Sales pitch:** "Teams no longer lose institutional knowledge when switching tools or machines. Export/import moves AI memory between instances with automatic conflict resolution."
- **One-liner:** "Move your AI memory anywhere — no lock-in, no duplicates."
