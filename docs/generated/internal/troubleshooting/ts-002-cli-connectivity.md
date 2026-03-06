---
id: ts-002
type: troubleshooting
audience: internal
topic: CLI Connectivity & Errors
status: draft
generated: 2026-03-05
source-tier: direct
hermes-version: 1.0.0
---

# Troubleshooting: CLI Connectivity & Errors

## Quick Diagnosis

| Exit Code | Meaning | Error Code in JSON | First Thing to Check |
|-----------|---------|-------------------|----------------------|
| 0 | Success | n/a | n/a |
| 1 | General error | `GENERAL_ERROR` | Check error message, usually validation or server-side issue |
| 2 | Not found | `NOT_FOUND` | Wrong memory ID, wrong endpoint, version mismatch |
| 3 | Connection error | `CONNECTION_ERROR` | Is the server running? Is the URL correct? |
| 4 | Auth error | `AUTH_REQUIRED` | Is the API key correct? Is it revoked? |

## Decision Tree

### Problem: "Cannot connect to server"

Exit code: `3`. Error code: `CONNECTION_ERROR`.

```
Error: Cannot connect to server: [Errno 61] Connection refused
```

**Step 1: Check the configured URL**

```bash
memories config show
```

Look at the `url` line. If it shows `http://localhost:8900 (from default)` and the server is running on a different port or host, set the correct URL:

```bash
memories config set url http://localhost:8900
# or use env var:
export MEMORIES_URL=http://localhost:8900
# or pass as flag:
memories --url http://localhost:8900 search "test"
```

**Step 2: Is the server running?**

Check if the Memories server process is alive:

```bash
# If running via Docker/OrbStack:
docker ps | grep memories

# If running directly:
curl -s http://localhost:8900/health
```

If the server is down, start it:

```bash
docker compose up -d
```

**Step 3: Is a firewall or proxy blocking the connection?**

If using Cloudflare WARP or a corporate VPN, localhost connections are usually unaffected. But if the server is on a remote host, check that the port is accessible:

```bash
curl -v http://<server-host>:<port>/health
```

**Step 4: Is the server still starting up?**

The Memories server loads the embedding model on startup, which takes several seconds. The health endpoint may not respond until the model is loaded. Wait and retry:

```bash
# Check readiness:
curl -s http://localhost:8900/health/ready
```

### Problem: "Request timed out"

Exit code: `3`. Error code: `CONNECTION_ERROR`.

```
Error: Request timed out: ...
```

The CLI has a 30-second timeout per HTTP request. This can occur if:
- The server is overloaded (e.g., processing a large batch or extraction).
- The network connection is slow.
- The embedding model is being reloaded.

Resolution: Retry the command. If timeouts are persistent, check server logs for resource exhaustion.

### Problem: "Authentication failed: 401"

Exit code: `4`. Error code: `AUTH_REQUIRED`.

```
Error: Authentication failed: 401
```

**Step 1: Check the configured API key**

```bash
memories config show
```

Look at the `api_key` line. It shows the first 8 characters and the source. Verify:
- If `(from default)`: No API key is configured. Set one.
- If `(from env)`: The `MEMORIES_API_KEY` env var is set but may be wrong.
- If `(from file)`: The config file has a key but it may be wrong.
- If `(from flag)`: The `--api-key` flag value is wrong.

**Step 2: Is the key correct?**

Compare the key prefix shown by `config show` with the expected key. If using a managed key, check that it has not been revoked:

```bash
# Using the admin key to check:
curl http://localhost:8900/api/keys \
  -H "X-API-Key: <admin-key>"
```

Look for the key's record. If `"revoked": 1`, the key is permanently disabled. Create a new key.

**Step 3: Is auth configured on the server?**

If the server has an `API_KEY` env var set, all requests require authentication. If the CLI has no key configured, every request returns 401.

**Step 4: Config source conflicts**

The layered config can cause surprises. For example, if `MEMORIES_API_KEY` is set in the environment to an old key, but the config file has the correct key, the config file wins (it has higher priority). Use `config show` to see which source is active.

Priority: flags > config file > env vars > defaults.

### Problem: "Authentication failed: 403"

Exit code: `4`. Error code: `AUTH_REQUIRED`.

```
Error: Authentication failed: 403
```

The key authenticated but lacks permission. This is a role or prefix issue, not a connectivity issue. See [ts-001-auth-failures.md](../troubleshooting/ts-001-auth-failures.md) for detailed 403 diagnosis.

Common CLI scenarios:
- Running `memories admin stats` with a non-admin key (admin endpoints require admin role).
- Running `memories add -s "other-prefix/data"` with a key scoped to a different prefix.
- Running `memories delete-by source "..."` with a read-only key.

### Problem: "Not found: /path"

Exit code: `2`. Error code: `NOT_FOUND`.

```
Error: Not found: /memory/99999
```

**Cause 1: Wrong memory ID**

The memory ID does not exist. Verify with `memories list` or `memories search`.

**Cause 2: Version mismatch**

If the CLI version is newer than the server, the CLI may call endpoints that do not exist on the server. Ensure the CLI and server versions are compatible:

```bash
memories --version
memories admin health  # shows server version
```

**Cause 3: Wrong endpoint path**

This is an internal issue. The `MemoriesClient` constructs paths like `/memory/{id}`, `/search`, etc. If the server's API prefix has changed, the CLI will get 404s on every call.

### Problem: "Validation error: ..."

Exit code: `1`. Error code: `GENERAL_ERROR`.

```
Error: Validation error: [{"loc": ["body", "source"], "msg": "field required", "type": "value_error.missing"}]
```

The server returned HTTP 422. The CLI passed invalid arguments to the API.

Common causes:
- Missing required options (e.g., `memories add "text"` without `-s`/`--source`).
- Invalid JSON in batch input.
- Wrong argument types (e.g., non-numeric threshold).

Resolution: Check the command's help text:

```bash
memories add --help
memories batch add --help
```

### Problem: "Server error 5xx"

Exit code: `1`. Error code: `GENERAL_ERROR`.

```
Error: Server error 500: Internal Server Error
```

The server encountered an unhandled error. Check the server logs for the stack trace. This is not a CLI issue.

## Config Debugging

The `memories config show` command is the primary diagnostic tool. It displays:

```
            url: http://localhost:8900  (from file)
        api_key: god-is-a****  (from file)
```

Each value shows its resolved source:
- `default` -- no explicit configuration, using built-in default
- `env` -- from `MEMORIES_URL` or `MEMORIES_API_KEY` environment variable
- `file` -- from `~/.config/memories/config.json`
- `flag` -- from `--url` or `--api-key` CLI flag

If the wrong source is active, either remove the higher-priority source or override it with a flag.

### Inspecting the config file directly

```bash
cat ~/.config/memories/config.json
```

Expected format:

```json
{
  "url": "http://localhost:8900",
  "api_key": "god-is-an-astronaut"
}
```

If the file is malformed JSON, the CLI silently ignores it and falls through to env vars and defaults. Fix the JSON syntax or delete the file and re-create it with `memories config set`.

### Clearing config

To reset to defaults, delete the config file:

```bash
rm ~/.config/memories/config.json
```

Then rely on env vars or flags.

## Agent-Specific Troubleshooting

### Error envelopes go to stderr

When the CLI is in JSON mode (piped or `--json`), error envelopes are written to stderr, not stdout. Agents must capture stderr separately:

```python
result = subprocess.run(
    ["memories", "--json", "search", "query"],
    capture_output=True, text=True,
)
# Success data on stdout:
if result.returncode == 0:
    data = json.loads(result.stdout)
# Error envelope on stderr:
else:
    error = json.loads(result.stderr)
```

### Confirmation prompts block non-interactive agents

Destructive commands (`delete-by source`, `delete-by prefix`, `backup restore`) prompt for confirmation. In a non-interactive environment (piped stdin), the prompt may hang or abort. Always pass `--yes` or `-y`:

```bash
memories --json delete-by source "old-data" --yes
memories --json backup restore backup-name --yes
```

### Multiple CLI processes

Concurrent CLI invocations are safe. Each process creates its own `httpx.Client`. There is no file-level locking or shared state. The config file is read-only during normal operation.

## Exit Code Reference

| Code | Constant | Exception | HTTP Status | Meaning |
|------|----------|-----------|-------------|---------|
| 0 | -- | -- | 2xx | Command succeeded |
| 1 | `GENERAL_ERROR` | `ValueError`, `CliServerError`, generic `Exception` | 422, 5xx, other | Validation error, server error, or unexpected failure |
| 2 | `NOT_FOUND` | `CliNotFoundError` | 404 | Resource not found |
| 3 | `CONNECTION_ERROR` | `CliConnectionError` | -- (no response) | Server unreachable or request timed out |
| 4 | `AUTH_REQUIRED` | `CliAuthError` | 401, 403 | Authentication or authorization failure |
