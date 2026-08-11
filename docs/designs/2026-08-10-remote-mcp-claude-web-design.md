# Remote MCP for claude.ai (Claude web) — Design

**Date:** 2026-08-10
**Status:** Approved (dk: "sure goahead")
**Research basis:** RESEARCH/claude-connector-auth/findings.md (14 sources, 2026-08-10)

## Problem

claude.ai web (and iOS) can only use memories through a **custom connector**: a publicly reachable Streamable HTTP MCP endpoint. Our MCP server is stdio-only. Docs claim authless connectors work; recent firsthand reports show the connector flow force-attempts OAuth discovery and fails without it. No static API-key option exists on individual plans.

## Goal

A self-hosted remote MCP endpoint at `https://mcp.divyekant.com/mcp` (droplet, behind the existing Caddy + Cloudflare tunnel) exposing the SAME tool surface as the stdio server, with a minimal self-contained OAuth 2.1 layer that satisfies claude.ai: authorization-code + PKCE + Dynamic Client Registration + well-known metadata. Single user (dk). No external IdP.

## Architecture

```
mcp-server/
  index.js            # stdio entry (bin) — thin: builds server from shared module + stdio transport
  lib-tools.mjs       # NEW: buildServer(ctx) — McpServer + all server.tool(...) registrations (extracted verbatim from index.js)
  remote/
    server.mjs        # HTTP entry: express + StreamableHTTPServerTransport + auth router; PORT env (default 8910)
    oauth.mjs         # single-user OAuth 2.1 provider (SDK server/auth interfaces)
    login.mjs         # minimal login/consent page (single password)
```

- **Tool surface**: one source of truth. `index.js` keeps its bin/dispatch behavior (CLI command dispatch from v5.8.0 must be preserved) and connects the shared server to stdio. `remote/server.mjs` connects the same shared server factory to Streamable HTTP (stateless mode; one server instance per request or SDK-recommended pattern).
- **OAuth (the make-or-break)**: implement the SDK's OAuth server provider interface — endpoints claude.ai requires:
  - `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource` (metadata)
  - `POST /register` (DCR, RFC 7591) — accept dynamic clients, restrict redirect URIs to `claude.ai`, `claude.com`, `localhost` (Claude Code loopback)
  - `GET /authorize` — login page: single password (`REMOTE_MCP_PASSWORD` env, bcrypt-hashed compare), then consent → authorization code (PKCE S256 required)
  - `POST /token` — code exchange + refresh grant (`application/x-www-form-urlencoded`)
  - Access tokens: HMAC-signed (secret `REMOTE_MCP_TOKEN_SECRET`), 1h expiry; refresh tokens rotated, file-backed store at `/data/remote-mcp/` (survives restarts); clean 401 with `WWW-Authenticate` on invalid/expired (claude.ai refreshes reactively on 401).
  - `client_credentials` NOT offered (claude.ai doesn't support it; user-consent flow is required once).
- **Auth mode flag**: `REMOTE_MCP_AUTH=oauth` (default) | `none` (authless probe/debug — used once to test whether claude.ai accepts authless now; never the shipped default).
- **Backend**: tools talk to the existing REST API exactly as the stdio server does (`MEMORIES_URL=http://memories:8000` inside the compose network, `MEMORIES_API_KEY` from env). `MEMORIES_CLIENT=claude-web` for telemetry.

## Deployment (droplet)

- New compose service `remote-mcp` in the repo's docker-compose.yml (+ cloud override): builds from `mcp-server/` (small node image), `127.0.0.1:8910:8910`, joins `web_tunnel` network, `/data/remote-mcp` volume for the token store.
- Routing: `mcp.divyekant.com` → Caddy → `remote-mcp:8910`. DNS + tunnel route via existing Cloudflare setup (`cf` helper / tunnel config on droplet).
- Hardening: Origin-header validation (SDK option), redirect-URI allowlist, rate limit on `/authorize` + `/token` (express middleware, simple in-memory bucket), optional Caddy allowlist for Anthropic egress `160.79.104.0/21` on `/mcp` (NOT on OAuth endpoints — the browser login must work from dk's devices).

## Testing

- node:test: OAuth metadata endpoints shape; DCR (valid/invalid redirect); full PKCE happy path (authorize → code → token → authed /mcp call); wrong password; wrong verifier; expired/invalid token → 401 + WWW-Authenticate; refresh rotation; authless mode gate.
- Transport: initialize + tools/list + one tools/call over Streamable HTTP against a mocked backend (fetch injected).
- Existing 65 tests must stay green (index.js refactor is the risk — the extraction must preserve bin dispatch + stdio behavior; smoke.mjs gates it).

## Out of scope

- Multi-user, external IdPs, MCP tunnels (research preview), `static_headers` beta, ChatGPT connector variant (same endpoint should work later), directory listing.

## Success criteria

dk adds `https://mcp.divyekant.com/mcp` in claude.ai → Settings → Connectors, logs in once, and Claude web can call `memory_search` against the production backend (39,920 memories).
