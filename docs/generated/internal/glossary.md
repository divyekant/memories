---
doc-id: glossary-001
type: glossary
title: "Glossary: Multi-Auth Terminology"
version: 1.5.0
date: 2026-03-05
audience: internal
source-tier: direct
hermes-version: 1.0.0
status: complete
---

# Glossary: Multi-Auth

**Admin key**
A key with `role: "admin"`. Has unrestricted access to all data and all endpoints, including key management and maintenance operations. The env key (`API_KEY` env var) is always an admin key. Managed admin keys can also be created via the API.

**AuthContext**
A Python dataclass (`auth_context.py`) attached to each request at `request.state.auth`. Encapsulates the caller's role, allowed prefixes, and key type. Provides methods for permission checks (`can_read`, `can_write`, `can_manage_keys`) and result filtering (`filter_results`).

**Batch operations**
CLI commands under the `batch` group (`batch add`, `batch get`, `batch delete`, `batch search`, `batch upsert`) that operate on multiple memories in a single API call. Batch input accepts both JSON arrays and JSONL (one JSON object per line) from a file path or stdin.

**CLI**
The Memories command-line interface, installed via `pip install memories` and invoked as `memories`. Built with Click. Wraps all ~38 REST API endpoints as 37 commands organized into 9 top-level commands and 9 command groups. Designed agent-first with JSON output when piped and human-readable output in TTY mode.

**Constant-time comparison**
The use of `hmac.compare_digest` when checking the env key. Prevents timing side-channel attacks where an attacker could infer key characters based on response time differences.

**Env key**
The API key set via the `API_KEY` environment variable. Compared using constant-time digest comparison. Always grants admin access with `key_type: "env"` and no prefix restrictions.

**Exit code**
An integer returned by the CLI process indicating the result category. Five values are defined: 0 (success), 1 (general error -- validation, server error, or unhandled exception), 2 (not found -- HTTP 404), 3 (connection error -- server unreachable or timeout), 4 (auth error -- HTTP 401 or 403). Agents use exit codes for coarse-grained error branching before parsing the JSON envelope.

**JSON envelope**
The structured JSON format used by the CLI for machine-readable output. Success: `{"ok": true, "data": {...}}`. Error: `{"ok": false, "error": "...", "code": "..."}`. Emitted automatically when stdout is not a TTY (piped), or forced with the `--json` flag. Error envelopes are written to stderr.

**Key hash**
The SHA-256 hex digest of a managed key's raw value. This is the only form of the key stored in the database. Used for lookup on every authenticated request.

**Key prefix**
The first 8 characters of a raw managed key (e.g., `mem_a3f8`). Stored in the `key_prefix` column for display and identification purposes in the admin UI and API listings. Not used for authentication.

**Key store**
The `KeyStore` class (`key_store.py`). A SQLite-backed store that manages the `api_keys` table. Handles key generation, hashing, creation, lookup, listing, updating, and revocation. Uses WAL mode and thread-local connections.

**Layered config**
The CLI's configuration resolution strategy implemented in `cli/config.py`. Four layers are checked in strict precedence order: CLI flags (`--url`, `--api-key`) > config file (`~/.config/memories/config.json`) > environment variables (`MEMORIES_URL`, `MEMORIES_API_KEY`) > defaults (`http://localhost:8900`, no key). The resolved source of each value is tracked and can be inspected with `memories config show`.

**Managed key**
An API key created through the `POST /api/keys` endpoint and stored in the `keys.db` database. Distinguished from the env key by `key_type: "managed"`. Has an assigned role and optional prefix scope.

**MemoriesClient**
The HTTP client class in `cli/client.py`. Uses `httpx.Client` (synchronous) with a 30-second timeout and `X-API-Key` header authentication. Wraps all server API endpoints as typed Python methods. Translates HTTP error responses into typed exceptions: `CliConnectionError` (connect/timeout), `CliAuthError` (401/403), `CliNotFoundError` (404), `CliServerError` (5xx), and `ValueError` (422).

**OutputFormatter**
The output formatting class in `cli/output.py`. Determines whether to emit JSON envelopes or human-readable text based on TTY auto-detection and the `--json`/`--pretty` flags. Provides `echo(data, human_fn)` for success output and `echo_error(message, code)` for errors. In JSON mode, success goes to stdout and errors go to stderr. In human mode, errors are printed in red via `click.secho`.

**Prefix scope**
A list of source-path prefixes assigned to a non-admin managed key (e.g., `["claude-code/*", "shared/*"]`). Restricts the key's read and write access to memories whose `source` field matches one of the prefixes. Admin keys have `prefixes: None` (unrestricted).

**Rate limiting**
A per-IP throttle on failed authentication attempts. The middleware tracks failures in a sliding 60-second window and rejects requests (HTTP 429) after 10 failures from the same IP.

**Raw key**
The full plaintext API key (e.g., `mem_a3f8b2c1d4e5f6071829304a5b6c7d8e`). Returned exactly once at creation time. Never stored -- only its SHA-256 hash is persisted.

**Read-only**
A role (`role: "read-only"`) that permits searching and reading memories within the key's prefix scope but prohibits all write operations (add, delete, extract, supersede).

**Read-write**
A role (`role: "read-write"`) that permits reading and writing (including extraction and deletion) within the key's prefix scope. Does not permit key management or access to maintenance endpoints.

**Revocation**
Soft-deletion of a managed key. Sets `revoked=1` in the database. The key immediately stops authenticating (returns 401). The record is preserved for audit purposes. Revocation is irreversible.

**Role**
One of three access levels assigned to a key: `read-only`, `read-write`, or `admin`. Determines which operations the key can perform. Checked via `AuthContext` methods.

**SHA-256**
The hash algorithm used to store managed keys. Applied via `hashlib.sha256(raw_key.encode()).hexdigest()`. The hex digest is stored in the `key_hash` column and indexed for fast lookup.

**Soft-delete**
The revocation mechanism. Rather than removing the key record from the database, the `revoked` column is set to 1. The record remains queryable through the admin listing endpoint.

**Source**
The `source` field on a memory (e.g., `"claude-code/decisions"`). Used by the prefix-scoping system to determine whether a key has access to a particular memory. The source acts as a namespace.

**Source prefix**
See "Prefix scope."

**TTY auto-detection**
The mechanism by which the CLI determines output format. The `OutputFormatter.is_json` property checks `sys.stdout.isatty()`. When stdout is a terminal (TTY), output is human-readable with colored text. When stdout is piped to another process or redirected to a file (non-TTY), output is JSON with the envelope format. This behavior can be overridden with `--json` (force JSON) or `--pretty` (force human-readable).

**Unrestricted access**
The access level granted when no auth is configured (no `API_KEY` env var, no managed keys), or when the caller authenticates as admin. Represented by `prefixes=None` in the `AuthContext`, which causes all prefix checks to return true.

**WAL mode**
SQLite Write-Ahead Logging mode. Enabled on the `keys.db` database for non-blocking concurrent reads during writes. Set via `PRAGMA journal_mode=WAL`.

**X-API-Key**
The HTTP header used to transmit the API key on every request. Case-sensitive. This is the only supported authentication header. Both the server middleware and the CLI's `MemoriesClient` use this header.
