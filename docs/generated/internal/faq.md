---
doc-id: faq-001
type: faq
title: "FAQ: Multi-Auth (v1.5.0)"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# FAQ: Multi-Auth

## General

### Q: Does this replace the API_KEY environment variable?

No. The `API_KEY` env var continues to work exactly as before. It acts as an implicit admin key with full unrestricted access. Managed keys are an additional layer on top.

### Q: Do I need to create managed keys?

Only if you need multiple clients with different access levels. If a single admin key is sufficient, the env var alone works fine.

### Q: What happens if I set no API_KEY and create no managed keys?

The system runs in unrestricted mode -- all requests get admin access with no authentication. This is the local-only mode, same as previous versions.

### Q: Are there any new environment variables?

No. The multi-auth feature requires no new configuration. The existing `API_KEY` env var behavior is unchanged. The `keys.db` database is created automatically.

## Keys

### Q: Can I see a key after it's created?

No. The raw key is returned exactly once in the creation response. The system stores only the SHA-256 hash. If the key is lost, revoke it and create a new one.

### Q: What does the key look like?

Format: `mem_` followed by 32 hex characters. Example: `mem_a3f8b2c1d4e5f6071829304a5b6c7d8e`. Total length: 36 characters.

### Q: Can I change a key's value (rotate it)?

No. The key value is immutable. To rotate, create a new key with the same settings, update the client, then revoke the old key. Both keys can be active during the transition.

### Q: Can I un-revoke a key?

No. Revocation is permanent (soft-delete -- the record stays for audit, but the key cannot authenticate again). Create a new key instead.

### Q: Can two keys have the same name?

Yes. Names are not unique. Keys are identified by their UUID (`id` field) and can be distinguished by their `key_prefix` (first 8 characters of the raw key).

### Q: Is there a limit on how many keys I can create?

No hard limit. Keys are stored in a SQLite table. Practical limits depend on database size and operational needs.

### Q: What header do I use?

`X-API-Key`. Not `Authorization`, not `Bearer`. The header name is case-sensitive.

## Roles

### Q: What can each role do?

| Role | Read | Write | Extract | Delete | Key Mgmt | Maintenance |
|------|------|-------|---------|--------|----------|-------------|
| read-only | Scoped | No | No | No | No | No |
| read-write | Scoped | Scoped | Scoped | Scoped | No | No |
| admin | All | All | All | All | Yes | Yes |

### Q: Can a read-only key use the extract endpoint?

No. Extraction creates new memories, which is a write operation. Read-only keys cannot extract.

### Q: Can a non-admin key see stats or metrics?

No. Stats, metrics, and all maintenance/data-management endpoints require admin access.

## Prefixes

### Q: How does prefix scoping work?

A prefix like `claude-code/*` is normalized to the base `claude-code`. A memory's source is then checked: does it equal the base, or does it start with `base + "/"`? If yes, access is allowed.

### Q: Can I use nested prefixes?

Yes. A prefix of `team/frontend/*` would match sources like `team/frontend/components` and `team/frontend/hooks/auth`.

### Q: Can a non-admin key have no prefixes?

No. The API rejects the creation of non-admin keys with an empty prefix list (HTTP 400: "Non-admin keys must have at least one prefix"). Admin keys have their prefixes silently cleared since they are unrestricted.

### Q: Do prefixes affect admin keys?

No. Admin keys (both env key and managed admin keys) have `prefixes=None`, which bypasses all prefix checks.

### Q: What if two keys share a prefix?

Both keys see the same data under that prefix. Isolation is prefix-based, not key-based. Two keys with `shared/*` access will see identical results when querying that namespace.

### Q: Can I use wildcards beyond the trailing /*?

No. The system only supports the trailing `/*` glob pattern. There is no regex or mid-path wildcard support. The `/*` is normalized away -- it simply means "this prefix and all sub-paths."

### Q: What about memories with no source?

Memories with an empty source string do not match any prefix. Only admin/unrestricted keys can read or write memories without a source.

## Security

### Q: How is the env key compared?

Using `hmac.compare_digest` for constant-time comparison, preventing timing attacks.

### Q: How are managed keys stored?

As SHA-256 hex digests. The raw key is never written to disk.

### Q: What prevents brute-force attacks?

Rate limiting: 10 failed auth attempts per IP per 60-second sliding window. After the limit, requests return HTTP 429 until the window resets.

### Q: Can someone use path traversal to escape prefix scope?

No. Any source path containing `..` as a component is rejected before prefix matching occurs.

### Q: Are revoked keys logged?

Revoked key records remain in the database with `revoked=1`. They appear in `GET /api/keys` listings. The last_used_at timestamp reflects the last time the key was successfully used before revocation.

## Web UI

### Q: Why can't I see the API Keys page?

The API Keys page content is gated to admin keys. Non-admin keys see an empty "access denied" state. Use an admin key (env key or managed admin key) to access the key management interface.

### Q: Can I manage keys only through the API?

No, both the API and the Web UI are available. The Web UI provides a visual interface for the same operations (create, edit, revoke). Both require admin access.

### Q: Does the Web UI work without auth?

The Web UI shell and static files are served without authentication. However, API calls made by the UI (fetching memories, searching, managing keys) require authentication. The UI picks up the API key from its configuration or prompts the user.

## CLI

### Q: How does the CLI decide between JSON and human-readable output?

The `OutputFormatter` checks three things in order: (1) if `--json` flag is set, output is JSON; (2) if `--pretty` flag is set, output is human-readable; (3) otherwise, it calls `sys.stdout.isatty()` -- TTY means human-readable, non-TTY (piped) means JSON. Agents should always use `--json` to guarantee JSON output regardless of environment.

### Q: Where does the CLI store its configuration?

In `~/.config/memories/config.json`. The file is created by `memories config set <key> <value>`. Valid keys are `url`, `api_key`, and `default_source`. The file is standard JSON. If the file does not exist or is malformed, the CLI falls through to environment variables (`MEMORIES_URL`, `MEMORIES_API_KEY`) and then to defaults (`http://localhost:8900`, no key).

### Q: How do I see which config source is active for each setting?

Run `memories config show`. Each value displays its source in parentheses: `(from flag)`, `(from file)`, `(from env)`, or `(from default)`. This is useful for debugging unexpected behavior caused by config layer conflicts.

### Q: Can the CLI read from stdin?

Yes. Commands that accept text (`add`, `upsert`, `is-novel`) accept `-` as the text argument or auto-detect piped stdin. Batch commands (`batch add`, `batch search`, `batch upsert`) and `extract submit` accept a file path or `-` for stdin. This enables piping: `echo "fact" | memories add -s source` or `cat data.jsonl | memories batch add -`.

### Q: What exit codes does the CLI use?

Five exit codes: 0 (success), 1 (general error -- validation, server error, unexpected), 2 (not found -- HTTP 404), 3 (connection error -- server unreachable or timeout), 4 (auth error -- HTTP 401 or 403). Agents can branch on exit code for coarse-grained error handling before parsing the JSON envelope.

### Q: Do destructive commands require confirmation?

Yes, in interactive (TTY) mode. `delete-by source`, `delete-by prefix`, and `backup restore` prompt for confirmation. The prompt defaults to No. Pass `--yes` or `-y` to skip the prompt. Agents running non-interactively should always pass `--yes`.

### Q: Can I use the CLI with a managed (non-admin) key?

Yes. The CLI sends the configured API key via the `X-API-Key` header, same as any other client. Commands that require admin access (e.g., `admin stats`, `admin deduplicate`, `backup create`) will return exit code 4 with an `AUTH_REQUIRED` error if the key lacks admin role. Read and write commands work within the key's prefix scope.

### Q: How does the CLI handle HTTP 422 validation errors?

The `MemoriesClient` parses the `detail` field from the server's 422 response and raises a `ValueError`. This maps to exit code 1 and error code `GENERAL_ERROR`. The server's validation message (e.g., "field required", "value is not a valid integer") is passed through to the user. Run `memories <command> --help` to check required options and argument types.

## Export/Import

### Q: How is export/import different from backup/restore?
**A:** Backup copies the entire database (metadata.json) and rebuilds all vectors on restore -- it's for disaster recovery. Export/import is selective: you filter by source prefix or date range, the output is portable NDJSON (no IDs or embeddings), and import supports conflict resolution strategies. Use backup for safety, export/import for migration and sharing.

**Related:** [fh-003-export-import](feature-handoffs/fh-003-export-import.md)

### Q: Which import strategy should I recommend?
**A:** For clean migration to an empty instance: `add` (fastest, no checks). For merging with existing data: `smart` (skips duplicates, newer timestamp wins for conflicts). For active instances with potential contradictions: `smart+extract` (LLM resolves borderline cases, costs ~5-10% tokens on borderline memories).

**Related:** [fh-003-export-import](feature-handoffs/fh-003-export-import.md)

### Q: Will import overwrite existing memories?
**A:** Never directly. With `add`, every record creates a new memory (may create duplicates). With `smart`, exact duplicates are skipped. Near-duplicates where the import is newer cause the old one to be deleted and the new one added -- effectively an update. Original IDs are never reused.

**Related:** [uc-006-instance-migration](use-cases/uc-006-instance-migration.md)

### Q: What if a customer's import fails partway through?
**A:** Import creates an automatic backup before processing. Per-line errors don't abort the import -- partial results are returned with an error count. If the server crashes, restore via `memories backup restore pre-import_TIMESTAMP`.

**Related:** [ts-003-export-import](troubleshooting/ts-003-export-import.md)
