# Shared project memory uses explicit policy domains

Date: 2026-08-13
Status: adopted for Phase 1

## Context

Two or more contributors need durable project knowledge that survives local
and cloud sessions, without exposing or mutating one another's private memory.
Existing sources such as `codex/fplguru` and `claude-code/fplguru` identify a
client, not a person, and can legitimately overlap across clients.

## Decision

Use separate structured policy domains:

- `person/<principal>/<project>/<kind>` for one contributor's private memory.
- `project/<project>/<kind>` for deliberately shared memory.

The four Phase 1 kinds are `decisions`, `knowledge`, `state`, and
`operations`. A repository may opt in with a strict `.memories/project.yaml`,
but authorization and principal identity come only from one configured
backend and its managed API key. The declaration contains no credentials or
membership.

Automatic extraction is private. Shared writes are explicit and all add-like
MCP tools use one project-write gate. The server owns author, contributors,
origin, and source-memory provenance. Every mutation and maintenance path
partitions structured data by the complete policy identity: namespace plus
principal/project for person memory, or namespace plus project for shared
memory. Moving to another owner or project is an authored replacement and is
restamped.

Missing declarations retain legacy behavior. A declaration that exists but
is invalid fails closed. Ordinary legacy sources retain global cross-client
duplicate detection and consolidation, but structured records are excluded
from those legacy clusters.

Historical malformed reserved sources are grandfathered for read, export, and
delete only. They must be explicitly migrated to a non-reserved legacy source
before being rewritten into a strict structured source. We reject silent
reinterpretation because an invalid path does not contain enough information
to prove its owner or project.

## Consequences

- Contributors can share a project namespace without sharing private memory.
- Concurrent source moves cannot race replacement validation because the
  engine re-reads under the relevant source-domain locks.
- Legacy users keep cross-client deduplication and consolidation.
- Operators must perform an explicit, auditable migration for malformed
  historical reserved records.
- Phase 1 has no automatic promotion, Git-derived membership, cross-backend
  federation, server replication, or production seed.
