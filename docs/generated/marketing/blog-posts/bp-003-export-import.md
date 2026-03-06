---
id: bp-003
type: blog-post
audience: marketing
topic: Export/Import
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Your AI Memory Shouldn't Be Trapped on One Machine

You've spent weeks building up context with your AI assistant. It knows your architecture decisions. It remembers that obscure fix from two Tuesdays ago. It understands your project's conventions without being told. Then you get a new laptop, and it's all gone.

## The Problem

AI assistants are getting better at remembering context across sessions. Tools like Memories give AI persistent, searchable knowledge that compounds over time. But that knowledge lives on a single instance. The moment you need to move — new machine, new team, new infrastructure — you face an ugly choice: start over, or spend hours manually recreating what the AI learned organically.

It gets worse when teams are involved. Developer A discovers a critical debugging pattern. Developer B is on a separate instance, about to waste an afternoon rediscovering the same thing. The knowledge exists — it's just trapped in the wrong place.

And merging? If two instances have been running independently, combining them naively creates a mess. Duplicate entries. Contradictory facts ("the API timeout is 30s" vs "we changed the API timeout to 60s last week"). Stale learnings that were superseded months ago.

## A Better Way

What if you could export exactly the knowledge you need — by project, by time range — into a portable file? And what if importing that file into another instance was smart enough to know the difference between new knowledge, duplicates, and outdated facts?

That's not hypothetical. That's what a good import system looks like: selective export, smart deduplication, and conflict resolution that keeps the freshest information without human intervention.

## How Memories Does This

Memories now supports export/import with three levels of intelligence:

**Export** is straightforward — select what you want (everything, one project, a date range) and save it. The output is a standard file format that any instance can read.

**Import** is where it gets interesting. You choose how smart you want the system to be:

For clean migrations to an empty instance, bulk import is instant — no checking needed. For merging with existing data, smart mode compares each incoming memory against what's already there. Identical knowledge gets skipped. Genuinely new information gets added. And when two memories conflict (like that API timeout change), the newer one wins automatically.

For the most thorough merging — when two active instances have been running independently — an AI-powered mode can evaluate borderline cases that simple comparison can't resolve. It looks at context, not just similarity scores.

Every import creates an automatic backup first. If anything goes wrong, one command restores your previous state.

## Results

In testing with nearly 9,000 memories, export and round-trip import works correctly — preserving all content, timestamps, and metadata while generating fresh identifiers for the target instance. Smart deduplication correctly identifies and skips duplicates via vector similarity, preventing the knowledge base bloat that makes naive merging unusable.

## Getting Started

If you're already running Memories, export/import is available now:

```bash
memories export -o knowledge.jsonl
memories import knowledge.jsonl --strategy smart
```

Your AI memory is no longer trapped. Move it, share it, merge it — without losing what makes it valuable.
