# Codex Parity and Distribution Design

## Goal

Make Codex a first-class Memories client on the current Codex lifecycle and MCP surfaces while preserving a safe fallback for older Codex installations. Deliver correctness parity first, then modernize remote MCP and plugin setup without forcing changes to Codex's native Memories feature.

## Release baseline

- Base: Memories v5.12.0 (`f50b05d`).
- Current validated Codex client: `codex-cli 0.146.0`.
- Existing baseline: 211 Node tests and 1,766 Python tests passing.
- The backend/API contract remains unchanged unless a test proves a client-facing change is required.
- Existing npm audit findings are outside this work.

## Deliverable 1: Codex parity

### Hook runtime reliability

Port the v5.10-v5.12 hook guarantees to the Codex hook implementation:

- silent-by-default activation when no backend is configured;
- `MEMORIES_ENABLED` and `MEMORIES_DISABLED` precedence;
- one backend-config resolver shared by activation, health, and routing;
- health checks against the routed search set;
- per-backend circuit-breaker state;
- deadline-aware SessionStart and SubagentStart recall;
- partial recall output when the deadline is exhausted;
- a distinct authenticated-search diagnostic for HTTP 401;
- timeout attribution that does not trip a breaker when the hook supplied a materially short budget.

The implementation will port only the shared behavior required by Codex. It will not perform a broad shell-library redesign. Behavioral and structural parity tests will prevent these guarantees from drifting again.

### Lifecycle expansion

Current Codex supports the five events already installed by Memories plus `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, and `SessionEnd`. The current-client hook set will therefore add:

- `PreCompact` -> aggressive extraction before context loss;
- `PostCompact` -> scoped rehydration after compaction;
- `SubagentStart` -> relevant project memory injection;
- `SubagentStop` -> subagent decision capture;
- `SessionEnd` -> final non-blocking extraction submission.

Each script will consume Codex's documented payload fields rather than assuming Claude payload shapes. `SessionEnd` will use Codex's maximum three-second timeout and will only submit work that the backend can enqueue within that bound.

The installer will detect the installed Codex version. Codex 0.146.0 and newer receive the expanded lifecycle. Older or unparseable versions retain the existing five-hook configuration and receive a clear status message rather than an incompatible config.

### Current Codex MCP configuration

The Codex adapter will move the seven read-only tool approvals into the documented `config.toml` MCP tool approval structure for the installer-owned `memories` server. Write and destructive tools remain prompt-gated.

Legacy allow-rules previously written to `~/.codex/settings.json` will be removed only when the install-state record proves Memories owns them. Foreign and pre-existing rules remain untouched. Uninstall will remove only installer-owned TOML blocks and recorded legacy settings.

### MCP server instructions

Both stdio and remote servers use the same `buildServer` factory, so the factory will publish concise MCP `instructions` covering:

- exact project-scoped search before broad search;
- evidence treatment for user versus assistant transcript lines;
- `memory_add` versus `memory_extract` write selection;
- explicit deletion for forget requests.

The first 512 characters will contain the most important read workflow. Existing per-tool descriptions remain authoritative for tool-specific arguments.

## Deliverable 2: Codex distribution

### Direct remote MCP and OAuth

The installer will add a distinct `--mcp-url <url>` option for a Streamable HTTP MCP endpoint. It is separate from the existing `--url`, which continues to mean the Memories REST backend used by the local stdio proxy.

For Codex, `--mcp-url` writes an installer-owned URL-based MCP configuration with OAuth enabled and prints the exact `codex mcp login memories` follow-up. It does not store a backend API key in the Codex MCP block. Local stdio remains the default and existing installs remain idempotent.

The remote server will attribute requests conservatively:

- Codex-identifying request metadata -> `codex`;
- Claude browser origin or Claude-identifying metadata -> `claude-web`;
- otherwise -> `remote-mcp`.

Attribution changes telemetry headers only; authorization decisions never depend on this detection.

### Repo-local Codex plugin

The plugin remains a small setup-and-discipline package rather than duplicating user-specific MCP credentials in a cached plugin. Its setup skill will stop requiring a repository checkout or invoking the deprecated shell installer. It will invoke the published npm installer with the Codex target, explain local versus remote MCP setup, and verify the current Codex config locations.

The manifest and tests will describe this portable bootstrap truthfully. Hook and MCP ownership stays with the npm installer, preventing duplicate lifecycle registrations when the plugin and CLI are both used.

### Native Codex Memories coexistence

External Memories and native Codex Memories serve different boundaries:

- external Memories is the durable, searchable, cross-client/project memory authority;
- native Codex Memories is an optional local derived cache controlled by the user.

The installer will not enable, disable, or rewrite native Codex Memories settings. `doctor` and documentation will report the coexistence policy and recommend `memories.disable_on_external_context = true` only as an optional deduplication mode for users who keep both systems enabled. MCP instructions will tell Codex to use external Memories for durable shared decisions and explicit recall.

## Error handling and migration

- An existing unmanaged `[mcp_servers.memories]` block is never overwritten.
- `--mcp-url` and local backend configuration cannot silently replace one another.
- Version detection failure selects the legacy hook set.
- Remote attribution has a neutral fallback and does not affect authentication.
- Update migrates only installer-owned legacy permission data.
- Uninstall remains retryable: ownership records are cleared only after all owned cleanup succeeds.

## Test strategy

Every behavior change follows red-green-refactor.

1. Shell hook tests reproduce activation, routed health, breaker isolation, deadline, 401, and short-budget behavior using temporary homes and fake curl processes.
2. Hook manifest tests assert expanded versus legacy event sets and the three-second SessionEnd limit.
3. Adapter tests validate local stdio, remote URL/OAuth, current TOML approvals, migration, idempotence, and conservative uninstall.
4. MCP initialize tests assert server instructions for both entry points through the shared factory.
5. Remote tests assert Codex, Claude, and neutral telemetry attribution without using attribution for authorization.
6. Plugin tests assert the published npm bootstrap and absence of deprecated checkout requirements.
7. Documentation tests or source assertions keep the lifecycle and coexistence claims synchronized with shipped assets.
8. Final validation runs the complete Node and Python suites plus npm pack inspection.

## Out of scope

- Backend data-model or search-ranking changes.
- Automatic import from Codex native Memories while `external_agent_memory_import` remains under development.
- Forcing native Codex Memories on or off.
- Windows hook implementation.
- Publishing a release, merging, or deploying the branch.
