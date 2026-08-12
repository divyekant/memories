# Shared Project Memory Phase 1 Implementation Plan

> **For implementers:** Execute this plan task-by-task with test-driven development. Keep the feature on the existing `docs/shared-spaces-spec` branch and preserve all legacy behavior when no valid `.memories/project.yaml` is present.

**Goal:** Let multiple authenticated people use one Memories host for durable project knowledge while keeping automatic capture private and making server-owned authorship impossible to spoof.

**Architecture:** Continue using the existing source-prefix ACL and Qdrant/metadata storage path. Add stable principal identity to managed keys, derive trusted authorship from request authentication, enforce project-memory invariants at the shared engine creation boundary, and teach supported clients to activate collaborative namespace behavior only from a strict repository declaration and a single configured backend.

**Tech stack:** Python 3.11+, FastAPI, Pydantic, SQLite, pytest, Node.js 20+, MCP SDK, shell hooks, Node test runner.

---

## Scope guard

This plan implements only Phase 1 from `docs/superpowers/specs/2026-08-03-shared-spaces-design.md`.

It must not add promotion automation, review queues, reconciliation jobs, membership tables, a second memory store, generic entities/spaces, federation, backend-qualified handles, OAuth work, or UI changes.

## Task 1: Add namespace and trusted-authorship policy

**Files:**

- Create: `project_memory.py`
- Create: `tests/test_project_memory.py`
- Modify: `memory_engine.py`
- Modify: `tests/test_memory_engine.py`

1. Write failing tests for strict source parsing:
   - `person/<principal_id>/<project_id>/<kind>` accepts only valid slugs and the four declared kinds.
   - `project/<project_id>/<kind>` accepts only valid project IDs and kinds.
   - Similar-looking legacy sources remain legacy sources.
2. Write failing tests showing that client metadata cannot persist `author`, `contributors`, or an unnormalized `origin_client`.
3. Write failing tests showing that project creation without trusted principal/system authorship raises a policy error and stores nothing.
4. Write failing tests showing trusted principal authorship stamps `author` and normalized `origin_client`; trusted system authorship stamps `author: system` plus normalized contributors and source-memory IDs.
5. Implement the minimal namespace helpers, origin-client allowlist, reserved metadata set, and trusted-authorship value object in `project_memory.py`.
6. Add an optional trusted-authorship argument to `MemoryEngine.add_memories`. Strip trusted fields from caller metadata, enforce project-source authorship, and apply trusted fields after caller metadata is filtered.
7. Re-run the focused tests and commit.

Verification:

```bash
uv run pytest -q tests/test_project_memory.py tests/test_memory_engine.py
```

## Task 2: Add stable managed-key principal identity

**Files:**

- Modify: `key_store.py`
- Modify: `auth_context.py`
- Modify: `app.py`
- Modify: `tests/test_key_store.py`
- Modify: `tests/test_auth_context.py`
- Modify: `tests/test_multi_auth.py`

1. Write a failing migration test that opens a pre-feature `api_keys` database and proves existing rows receive a stable `principal_id` equal to the existing key name.
2. Write failing create/list/lookup/update tests for explicit principal IDs and for the compatibility default.
3. Write failing API tests proving `/api/keys/me` exposes `principal_id` for managed keys, key creation accepts it, and changing a key display name does not silently change its principal.
4. Add an additive SQLite migration for `principal_id`, backfill existing rows once, and include the field in create, lookup, list, and update operations.
5. Add slug validation and carry `principal_id` separately in `AuthContext`.
6. Keep env/unconfigured admin identity empty; those callers retain legacy admin behavior but cannot create project memories without an explicit trusted system context.
7. Re-run the focused tests and commit.

Verification:

```bash
uv run pytest -q tests/test_key_store.py tests/test_auth_context.py tests/test_multi_auth.py tests/test_auth_backward_compat.py
```

## Task 3: Thread trusted authorship through every creation path

**Files:**

- Modify: `app.py`
- Modify: `memory_engine.py`
- Modify: `llm_extract.py`
- Modify: `consolidator.py`
- Modify: `tests/test_project_memory.py`
- Modify: `tests/test_memory_api.py`
- Modify: `tests/test_extract_api.py`
- Modify: `tests/test_llm_extract.py`
- Modify: `tests/test_supersede_endpoint.py`
- Modify: `tests/test_export_import_api.py`
- Modify: `tests/test_merge_api.py`
- Modify: `tests/test_missed_memory.py`

1. Add failing endpoint tests for add, batch add, upsert, batch upsert, supersede, merge, extraction commit/fallback/queued jobs, missed capture, and import. Each principal-originated creation or replacement must stamp the authenticated principal and overwrite spoofed trusted fields.
2. Add a request helper that builds trusted authorship from `AuthContext.principal_id` and normalized `X-Memories-Client`.
3. Thread the trusted value through `add_with_doctrine`, `supersede`, `merge_memories`, `upsert_memory`, `upsert_memories`, import helpers, and `llm_extract.execute_actions` into `add_memories`.
4. Store trusted authorship in queued extraction jobs so the worker cannot lose or re-derive request identity.
5. Mark server consolidation outputs as `system` and derive contributors/source-memory IDs from their inputs.
6. Reuse `transcript_hygiene.redact_secrets` as the deterministic explicit-project-write gate. If project-memory text contains a credential-shaped value, reject the write before storage rather than silently altering it. Legacy/person-private writes retain current behavior.
7. Ensure project policy errors map to a stable 4xx response and never become an opaque 500.
8. Re-run focused creation-path tests and commit.

Verification:

```bash
uv run pytest -q tests/test_project_memory.py tests/test_memory_api.py tests/test_extract_api.py tests/test_llm_extract.py tests/test_supersede_endpoint.py tests/test_export_import_api.py tests/test_merge_api.py tests/test_missed_memory.py tests/test_consolidator.py tests/test_secret_redaction.py
```

## Task 4: Parse the repository declaration and enforce single-host activation

**Files:**

- Modify: `mcp-server/lib-tools.mjs`
- Modify: `mcp-server/test/lib-tools.test.mjs`
- Modify: `mcp-server/assets/claude-code/hooks/_lib.sh`
- Modify: `mcp-server/assets/codex/hooks/_lib.sh`
- Modify: `tests/test_claude_memory_hooks.py`
- Modify: `tests/test_codex_plugin.py`
- Modify: `tests/test_multi_backend_config.py`
- Modify: `tests/test_worktree_project.py`

1. Add failing shared fixtures for valid, missing, malformed, unknown-field, false, and invalid-slug `.memories/project.yaml` files.
2. Add failing tests proving worktrees resolve the declaration from the main repository boundary.
3. Add failing tests proving project mode activates only with exactly one configured backend; multiple backends preserve existing routing and produce no project namespace behavior.
4. Implement strict parsing for exactly `project_id` and `shared_memory`. Unknown fields or anything other than boolean `true` disables collaborative mode with a diagnostic.
5. Resolve the authenticated principal through `/api/keys/me` only after valid project config and single-backend checks succeed. Missing/invalid principal disables collaborative mode.
6. Keep the two packaged hook libraries behaviorally identical through shared contract fixtures even though they remain separate files.
7. Re-run hook and MCP tests and commit.

Verification:

```bash
uv run pytest -q tests/test_claude_memory_hooks.py tests/test_codex_plugin.py tests/test_multi_backend_config.py tests/test_worktree_project.py
npm --prefix mcp-server test -- --test-name-pattern='project|backend|tool'
```

## Task 5: Make capture, recall, and agent guidance project-aware

**Files:**

- Modify: `mcp-server/assets/claude-code/hooks/memory-recall.sh`
- Modify: `mcp-server/assets/claude-code/hooks/memory-query.sh`
- Modify: `mcp-server/assets/claude-code/hooks/memory-rehydrate.sh`
- Modify: `mcp-server/assets/claude-code/hooks/memory-subagent-recall.sh`
- Modify: `mcp-server/assets/claude-code/hooks/memory-extract.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-recall.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-query.sh`
- Modify: `mcp-server/assets/codex/hooks/memory-extract.sh`
- Modify: `mcp-server/assets/claude-code/skills/memories/SKILL.md`
- Modify: `mcp-server/lib-tools.mjs`
- Modify: associated hook and Node tests

1. Add failing tests proving collaborative extraction writes only to `person/<principal>/<project>/knowledge` and never infers `project/...`.
2. Add failing tests proving collaborative recall orders sources as project, current-person private, then configured legacy exact-project prefixes. ACLs remain authoritative when a legacy prefix is not granted.
3. Add failing tests proving explicit project writes use the existing add tool exactly once and must name one of the four project kinds.
4. Implement conditional namespace substitution in the hook helpers and MCP tool guidance. Leave legacy behavior byte-for-byte equivalent when project mode is inactive.
5. Update injected guidance and the packaged Memories skill with the durable-sharing test: another contributor will need the fact without the current session. State explicitly that automatic extraction remains private.
6. Render author and origin-client labels in project recall output without treating them as higher-confidence truth.
7. Re-run focused tests and commit.

Verification:

```bash
uv run pytest -q tests/test_claude_memory_hooks.py tests/test_codex_plugin.py tests/test_playbook_gate.py tests/test_mcp_server_stdio.py
npm --prefix mcp-server test
```

## Task 6: Document onboarding, isolation verification, and revocation

**Files:**

- Modify: `README.md`
- Modify: `docs/memory-playbook.md`
- Modify: `docs/superpowers/specs/2026-08-03-shared-spaces-design.md` only if implementation evidence requires a wording correction
- Add or modify the repository's most relevant setup documentation discovered during implementation
- Add documentation contract tests where existing suites enforce packaged-copy parity

1. Document `.memories/project.yaml`, the namespace/kind model, and the single-shared-host boundary.
2. Document administrator commands/API payloads for two managed keys with separate principal-private prefixes and one shared project prefix.
3. Document fresh-session verification: both principals can read project memory; neither can read the other's person namespace; authorship and origin client are present.
4. Document revocation/narrowing and the fact that repository config grants no access.
5. Add a short migration note: legacy prefixes remain readable only when the principal's key is explicitly authorized for them; nothing is auto-promoted or renamed.
6. Do not seed production or FPLGuru memories as part of code implementation. Provide the reviewed manual seeding/verification step after the feature is deployed and access isolation passes.
7. Re-run documentation and packaging tests and commit.

Verification:

```bash
uv run pytest -q tests/test_playbook_gate.py tests/test_installer.py tests/test_codex_plugin.py tests/test_claude_memory_hooks.py
npm --prefix mcp-server test
```

## Task 7: Full verification and PR handoff

**Files:** All changed files.

1. Inspect the full diff against `origin/develop` for scope creep, duplicate policy logic, missing creation paths, and accidental legacy behavior changes.
2. Run formatting/static checks already defined by the repository, if any.
3. Run the complete Python and Node suites.
4. Confirm the worktree is clean except for intended changes and commit any final test/documentation fixes.
5. Push `docs/shared-spaces-spec` and post a PR summary covering architecture, migration, security properties, explicit deferrals, and verification evidence.
6. Request review on the exact pushed head. Address only technically valid, actionable current-head findings; explain rejected suggestions with evidence.

Verification:

```bash
uv run pytest -q
npm --prefix mcp-server test
git diff --check
git status --short --branch
```

## Acceptance checklist

- [ ] Person A cannot read or search Person B's namespace.
- [ ] Both authorized principals can read and explicitly write the project namespace.
- [ ] Every principal-originated creation/replacement stamps server-derived authorship.
- [ ] Client-supplied trusted metadata cannot impersonate an author.
- [ ] System-derived memory records contributors and source-memory IDs.
- [ ] Invalid/missing config and multi-backend ambiguity fail private without changing legacy behavior.
- [ ] Automatic extraction in collaborative repositories remains person-private.
- [ ] Explicit project writes are single-destination and credential-checked.
- [ ] MCP, Claude Code, and Codex paths pass equivalent project-mode fixtures.
- [ ] No promotion, reconciler, membership service, second store, federation, or UI subsystem was added.
