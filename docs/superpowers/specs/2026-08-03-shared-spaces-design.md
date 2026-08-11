---
shaping: true
---

# Shared Spaces — Multi-Collaborator Team Memory — Design

## Source

> Design "Shared Spaces" — first-class multi-collaborator team memory for Memories (dk + Darshan sharing project context across their individual agents). fplguru is the first consumer, but this is a Memories product feature.

Design approved 2026-08-03 (brainstorm session, memory id 52316). This spec documents the approved design and pins the concrete behavior for the implementation plan.

## Problem

Two people (dk and Darshan) collaborate on a project (fplguru) using their own agents (Claude Code, Codex). Each person's agents accumulate decisions, handoffs, and blockers in their own memory — the other person's agents never see them. Decisions get re-derived, contradicted, or lost at the seam between collaborators.

Memories already solves the multi-*agent* case for one person (prefix-scoped keys, multi-backend fan-out). Shared Spaces extends the same primitives to the multi-*person* case without introducing sync, replication, or a new server mode.

## Current State (verified against `develop`)

**Server-side prefix ACLs already exist and are sufficient:**
- [auth_context.py](../../../auth_context.py) — `AuthContext` carries `role`, `prefixes`, `key_name`, `key_id`; `can_read`/`can_write` enforce prefix matching; `filter_results` (line 68) strips non-matching sources from search results.
- [key_store.py](../../../key_store.py) — managed key storage; key records carry `name`, `role`, `prefixes`.
- Key CRUD endpoints exist at [app.py:1513](../../../app.py:1513)–1571 (`/api/keys/me`, `POST/GET/PATCH/DELETE /api/keys`), admin-only, non-admin keys require ≥1 prefix.

**Multi-backend client routing already exists:**
- `~/.config/memories/backends.yaml` (project override: `.memories/backends.yaml`) — named backends with `url`, `api_key`, `scenario`, plus an optional `routing:` map of op → backend names.
- MCP bridge: [mcp-server/index.js:58](../../../mcp-server/index.js:58) `getBackendsForOp` — search fans out to all backends and merges/dedupes with backend labels (`_backend` tag, line 141); extract/feedback go to `dev`/`personal` scenarios.
- Hooks: [plugin/hooks/_lib.sh:454](../../../plugin/hooks/_lib.sh:454) `_get_backends_for_op` mirrors the same logic in shell; `_extract_multi` (line 599) fans extraction out per the `extract` op.

**Confirmed gaps:**
1. **No attribution.** `/memory/add` ([app.py:2271](../../../app.py:2271)) and `/memory/extract` ([app.py:3236](../../../app.py:3236)) have `auth.key_name` in hand but never stamp it into the stored memory. In a shared space you cannot tell who wrote what.
2. **Write fan-out is wrong for multi-person.** `getBackendsForOp("add")` returns ALL backends ([index.js:70](../../../mcp-server/index.js:70)); `_get_backends_for_op add` in `_lib.sh` does the same. A personal memory written while a team backend is configured would land on the team instance (and vice-versa fails ACL).
3. **No promotion guidance.** Nothing tells an agent when a decision belongs in the team space.
4. **No onboarding path** for a collaborator (key minting flow, client config, conventions).

**Storage/read mechanics that make this cheap** (verified): `add_memories` flattens caller metadata into the memory record ([memory_engine.py:717](../../../memory_engine.py:717) `**filtered_extra`), and `search` returns the full record (`{**meta, similarity}`). A metadata key stamped at write time therefore surfaces in every search/recall result with zero read-path changes.

## Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| R1 | Two collaborators share team memory through a single host instance + prefix-scoped keys. No sync/replication. | Must-have |
| R2 | Works whether or not the collaborator runs their own instance (host-only mode and federated mode via backends.yaml). | Must-have |
| R3 | Team memories carry authorship — every write through a managed key is attributed, server-authoritatively. | Must-have |
| R4 | Writes route by source prefix: `team/*` → team backend only, everything else → personal backend only. No dual-writes. | Must-have |
| R5 | Reads require no agent decision — existing fan-out surfaces team results labeled with backend + author. | Must-have |
| R6 | Existing single-backend and dev/prod multi-backend setups keep working unchanged (backward compatible). | Must-have |
| R7 | Plugin skill teaches promotion ("does this affect the other person?") and team-recall respect ("their decisions are decisions, not context to overwrite"). | Must-have |
| R8 | A collaborator can onboard in ~10 minutes from a doc (COLLABORATOR.md) + one minted key. | Must-have |
| R9 | Design must not preclude OAuth 2.1 identities mapping onto AuthContext later. | Constraint |
| R10 | `team/fplguru/decisions` seeded with standing shared decisions. | Must-have |

## Approved Design

### Topology

Single shared **host instance** (dk's DO droplet, `memory.divyekant.com`) holds the team namespaces. Collaborators get prefix-scoped API keys. There is no replication and no server-to-server traffic — all multi-instance behavior is client-side routing via the existing `backends.yaml` fan-out.

Two supported collaborator modes:

1. **Host-only** (Darshan today): no instance of his own. His `backends.yaml` has one entry — dk's instance with his scoped key. Single-backend fast path everywhere; his personal prefixes and the team prefixes both live on dk's instance.
2. **Federated** (Darshan later, or any collaborator with their own instance): `backends.yaml` gains two entries — `personal` (their instance) and `team` (dk's instance, scoped key). Search fans out across both; writes route by prefix (below).

dk himself is in federated shape already conceptually — his instance is both his personal and the team backend, so he stays single-entry.

### Namespaces

Two team prefixes per project, with distinct semantics:

- `team/<project>/decisions` — durable team truth. Standing decisions, conventions, architecture choices that bind both collaborators. Long-lived; superseded explicitly, never casually overwritten.
- `team/<project>/state` — handoffs, blockers, "where I left it". Ephemeral by nature; freely superseded.

Personal write-own prefixes (`claude-code/<project>`, `codex/<project>`, `wip/…`, `learning/…`) are unchanged.

First consumer: `team/fplguru/decisions` and `team/fplguru/state`.

### Auth & identity

- Mint Darshan a **read-write managed key** on the host instance, scoped to `["team/fplguru", "codex/fplguru", "claude-code/fplguru"]` (team space + his personal project prefixes on the host). Key `name` identifies the person: `darshan`.
- **Key naming convention:** `name` = person identifier (`dk`, `darshan`), optionally `-<agent>` suffixed if a person wants per-agent keys later. `name` is the attribution identity (below), so per-person is the default.
- **dk's existing per-agent keys must gain the team prefixes.** Server-side `filter_results` strips sources a key can't read, so a key scoped to `claude-code/memories` alone will never see `team/*` results. Onboarding includes `PATCH /api/keys/{id}` to append `team/fplguru` to each existing agent key. This is an ops step, not a code change.
- Prefix ACL enforcement is entirely existing code (`auth_context.py`, `key_store.py`). No server auth changes.
- **OAuth forward-compatibility (R9):** attribution reads only `AuthContext.key_name`. When OAuth 2.1 lands, the token-validation layer constructs an `AuthContext` with `key_name` = OAuth subject identity and everything downstream (attribution, ACLs, audit) works untouched. Nothing in this design assumes X-API-Key specifically.

### Read path — no agent decision

Hooks and `memory_search` already fan out to all configured backends and merge results. Team results arrive labeled twice:

- **Backend label:** the bridge's existing `_backend` tag (and the hooks' equivalent) tells the agent which instance a result came from.
- **Author label:** the new `author` metadata field (below) tells the agent *who* wrote it.

The plugin skill (component 4) instructs: **a team memory authored by the other person is a decision to respect, not context to overwrite.** Contradicting it requires an explicit superseding write to the team space, ideally after human confirmation.

### Write path — prefix-based routing (the one behavioral change)

Rule, applied identically in the MCP bridge and the shell hooks:

1. **Single backend configured → that backend.** (Unchanged fast path; covers Darshan host-only mode and dk today.)
2. **Explicit `routing:` map in backends.yaml → honored as today.** (Unchanged escape hatch.)
3. **New: prefix claims.** A backend entry may declare `write_prefixes: ["team/"]` (list of source prefixes, same matching semantics as server-side ACLs). For a write with source `S`:
   - If any backend claims `S` via `write_prefixes` → route to exactly the claiming backend(s).
   - Else → route to all backends that declare **no** `write_prefixes` (the personal/default sinks).
4. **No backend declares `write_prefixes` → current behavior preserved** (add fans out to all; dev/prod dual-write setups keep working). This is the R6 backward-compat guarantee.

Federated example:

```yaml
backends:
  personal:
    url: https://memories.darshan.dev
    api_key: ${DARSHAN_MEMORIES_KEY}
    scenario: personal
  team:
    url: https://memory.divyekant.com
    api_key: ${DARSHAN_TEAM_KEY}
    scenario: team
    write_prefixes: ["team/"]
```

`memory_add(source="team/fplguru/state", …)` → team only. `memory_add(source="codex/fplguru", …)` → personal only. Search still fans out to both.

Extraction routing follows the same rule keyed on the extraction `source`: personal-prefixed extraction never reaches the team backend. (In practice team writes come from explicit `memory_add` promotion, not extraction; extraction into `team/*` is allowed but not part of the default flow.)

## Build List

### 1. Backend: attribution stamping (the only server delta)

- `/memory/add` ([app.py:2271](../../../app.py:2271)) and `/memory/add-batch` ([app.py:2328](../../../app.py:2328)): after `_require_write`, stamp `metadata["author"] = auth.key_name` when `auth.key_name` is set. **Server-authoritative:** overwrite any client-supplied `author` — a scoped key must not be able to impersonate. Env/admin callers with no `key_name` leave `author` unset (absence = instance owner).
- `/memory/extract` ([app.py:3236](../../../app.py:3236)): thread `auth.key_name` through both the queued path (`run_extraction`, [llm_extract.py:935](../../../llm_extract.py:935)) and the fallback path (`_run_fallback_extraction`) so every stored extracted memory carries the same `author` stamp. The queued worker runs detached from the request, so `key_name` must be captured into the job payload at enqueue time — the job record already captures `auth_key_id` this way; attribution extends the same pattern to the memories themselves.
- Surfacing is free: metadata flattens into the record and search returns the full record, so `author` appears in `memory_search` / recall results with no read-path changes. Client formatting (bridge tool output, hook injection templates) should render it for `team/*` results.
- Reserved-key note: add `author` handling deliberately — it is *not* added to `_reserved_add` (server writes it before the engine sees it), but the endpoint must set it after accepting client metadata so the overwrite wins.

### 2. MCP bridge: prefix-based write routing

- [mcp-server/index.js:58](../../../mcp-server/index.js:58) `getBackendsForOp(op)` → becomes `getBackendsForOp(op, source)`. Implement the four-step rule above. Parse optional `write_prefixes` per backend in `loadBackends()`.
- Call sites for `add` / `extract` / `feedback` pass the operation's `source` through `memoriesRequest`.
- Prefix matching mirrors `source_matches_prefixes` semantics (exact segment boundary: `team` claims `team/x` but not `teamx`).

### 3. Hooks: same routing in shell

- [plugin/hooks/_lib.sh:454](../../../plugin/hooks/_lib.sh:454) `_get_backends_for_op` gains the same `source`-aware rule; `_parse_backends_yaml` (line 298) and the node fallback parser (line 426) learn `write_prefixes`.
- `_extract_multi` (line 599) passes its `source` into routing.

### 4. Plugin skill: promotion + team recall

Add a "Shared Spaces" section to the Memories plugin skill:

- **Promotion rule:** after a decision, ask "does this affect the other person?" If yes → *also* write `team/<project>/decisions` (durable) or `team/<project>/state` (handoff/blocker). Personal extraction continues unchanged; promotion is an explicit additional `memory_add`.
- **Wording discipline:** team memories name the decision, the why, and boundary conditions (`until`/`unless`/`because`) — they are read by someone without this session's context.
- **Team recall:** results with `author` ≠ you from `team/*` are the other person's decisions. Respect them; supersede explicitly (with attribution, via `on_duplicate=supersede`) rather than writing a contradicting personal memory.
- **State hygiene:** `team/<project>/state` entries should be superseded when the handoff is picked up.

### 5. COLLABORATOR.md + key minting flow

`docs/COLLABORATOR.md` — Darshan's 10-minute setup, host-only mode first:

1. Host admin mints the key: `POST /api/keys` with `name`, `role: read-write`, `prefixes` (exact curl provided).
2. Collaborator installs the plugin/MCP bridge, writes minimal `backends.yaml` (single entry, host URL + key via env var — never a plaintext literal, per the existing credential-hygiene finding).
3. Verify: `GET /api/keys/me`, one test write to `team/<project>/state`, one search.
4. Federated upgrade path: the two-entry `backends.yaml` from this spec, when they stand up their own instance.
5. Conventions: namespace semantics, promotion rule, supersede etiquette.

Also covers the host-side ops step: extending existing agent keys with the team prefix.

### 6. Seed `team/fplguru/decisions`

Backfill the standing fplguru decisions both collaborators already operate under (collected from existing `codex/fplguru` + conversation history) as attributed team memories. Done via `memory_add` with dk's key after components 1–2 land, so seeds carry attribution and route correctly.

## Fit Check

| Req | Requirement | Met by |
|-----|-------------|--------|
| R1 | Shared host + scoped keys, no sync | Topology; existing ACLs; component 5 |
| R2 | Host-only and federated modes | Write-routing rules 1 & 3; COLLABORATOR.md §4 |
| R3 | Server-authoritative attribution | Component 1 |
| R4 | Prefix-based write routing | Components 2–3 |
| R5 | Zero-decision reads, labeled results | Existing fan-out + `_backend` tag + `author` surfacing |
| R6 | Backward compatible | Routing rules 1, 2, 4 (no `write_prefixes` → today's behavior) |
| R7 | Promotion + respect rules | Component 4 |
| R8 | 10-minute onboarding | Component 5 |
| R9 | OAuth forward-compat | Attribution reads only `AuthContext.key_name` |
| R10 | Seeded team space | Component 6 |

## Out of Scope (deferred, do not design in)

- **MCP-over-HTTP + OAuth 2.1** for claude.ai remote connectors. The "claude.ai Memories" connector stays broken until then — the backend speaks only REST + X-API-Key; Claude Code works via the local stdio bridge. This design keeps the door open (R9) but builds none of it.
- Server-to-server sync/replication between instances (explicitly rejected in favor of client-side routing).
- Per-memory ACLs, roles beyond the existing three, or >2-person group mechanics (the design scales to N collaborators via more keys + prefixes, but nothing N-specific is built now).
- UI/webui changes for team views.

## Risks & Notes

- **Read visibility depends on key prefixes** — the most likely onboarding bug is a key missing `team/<project>` and silently seeing no team results (`filter_results` strips them without error). COLLABORATOR.md's verify step exists to catch this.
- **Attribution only covers managed keys.** Writes with the env admin key are unattributed; acceptable because team writes flow through named keys by convention. Revisit if it becomes noise.
- **`author` collides with any pre-existing client metadata named `author`.** Server overwrite is intentional (anti-spoofing); grep confirmed no current writer uses the field.
- **Hook parser duplication:** `write_prefixes` must be added in three parsers (index.js, `_parse_backends_yaml`, the node fallback in `_lib.sh`). Tests should cover all three to prevent drift.
