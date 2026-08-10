#!/usr/bin/env node

/**
 * Remote MCP server: an Express HTTP entry point wiring together
 * StreamableHTTPServerTransport (SDK, stateless mode), the single-user
 * OAuth 2.1 provider (./oauth.mjs), and the shared memory_* tools
 * (../lib-tools.mjs). `createApp(cfg)` builds the express app (exported
 * for tests); `main()` reads env and listens when this file is run
 * directly.
 */

import { realpathSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import express from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { buildServer } from "../lib-tools.mjs";
import { createOAuth } from "./oauth.mjs";
import { createStore } from "./store.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const PKG_VERSION = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8")).version;

// -- Origin allowlist (browser clients only — non-browser clients send no
//    Origin header at all and are allowed through unconditionally) ----------

function isAllowedOrigin(originHeader) {
  let url;
  try {
    url = new URL(originHeader);
  } catch {
    return false;
  }
  if (url.protocol !== "https:") return false;
  const host = url.hostname;
  return (
    host === "claude.ai" ||
    host.endsWith(".claude.ai") ||
    host === "claude.com" ||
    host.endsWith(".claude.com") ||
    host === "localhost" ||
    host === "127.0.0.1"
  );
}

function originGuard(req, res, next) {
  const origin = req.headers.origin;
  if (!origin) return next();
  if (isAllowedOrigin(origin)) return next();
  res.status(403).json({ error: "forbidden_origin" });
}

// -- Naive in-memory rate limiter (per-IP sliding window) --------------------
// Single-process deployment; no need for a shared store. Bounded two ways so
// it can't become the attack surface itself:
//   1. A single flooding IP never grows its own array past `limit` — once at
//      capacity we reject without pushing, so the array (and the per-request
//      Array.filter cost) stays capped instead of growing unbounded with
//      every rejected request.
//   2. An attacker rotating source IPs to dodge the per-IP bucket can't grow
//      the Map without bound either: a periodic sweep evicts IPs whose whole
//      window has aged out, and a hard cap on distinct tracked IPs rejects
//      requests from *new* IPs once at capacity (fails closed rather than
//      growing the Map further).
// `createRateLimiter` is exported so tests can drive it directly (synchronous
// calls, sweepIntervalMs: 0 to disable the real timer) instead of needing 21
// real HTTP round-trips per scenario.

export function createRateLimiter({ limit = 20, windowMs = 60_000, sweepIntervalMs = 60_000, maxKeys = 10_000 } = {}) {
  const hits = new Map();

  function sweep() {
    const now = Date.now();
    for (const [ip, timestamps] of hits) {
      const fresh = timestamps.filter((t) => now - t < windowMs);
      if (fresh.length === 0) hits.delete(ip);
      else hits.set(ip, fresh);
    }
  }

  let timer = null;
  if (sweepIntervalMs > 0) {
    timer = setInterval(sweep, sweepIntervalMs);
    timer.unref?.();
  }

  function rateLimit(req, res, next) {
    const ip = req.ip || req.socket?.remoteAddress || "unknown";
    const now = Date.now();
    const existing = hits.get(ip);
    const recent = existing ? existing.filter((t) => now - t < windowMs) : [];

    if (recent.length >= limit) {
      // Already at capacity — reject WITHOUT pushing, so a sustained flood
      // from one IP keeps its array pinned at `limit` instead of growing
      // (and re-filtering) on every rejected request too.
      hits.set(ip, recent);
      return res.status(429).json({ error: "rate_limited" });
    }
    if (!existing && hits.size >= maxKeys) {
      // Unknown IP arriving while we're already tracking the max distinct
      // keys (e.g. an attacker rotating source IPs). Fail closed rather than
      // let the Map grow without bound.
      return res.status(429).json({ error: "rate_limited" });
    }

    recent.push(now);
    hits.set(ip, recent);
    next();
  }

  rateLimit.sweep = sweep;
  rateLimit.stop = () => { if (timer) clearInterval(timer); };
  rateLimit.size = () => hits.size;
  rateLimit.countFor = (ip) => (hits.get(ip) || []).length;

  return rateLimit;
}

// -- Bearer auth middleware (oauth mode only) --------------------------------

function bearerAuth(oauth, issuer) {
  return (req, res, next) => {
    const header = req.headers.authorization || "";
    const match = /^Bearer\s+(.+)$/i.exec(header);
    const result = oauth.verifyAccess(match ? match[1] : null);
    if (!result.ok) {
      res.setHeader("WWW-Authenticate", `Bearer resource_metadata="${issuer}/.well-known/oauth-protected-resource"`);
      return res.status(401).json({ error: "invalid_token" });
    }
    next();
  };
}

// -- App ----------------------------------------------------------------------

export function createApp(cfg = {}) {
  const {
    issuer,
    authMode = "oauth",
    passwordHash,
    tokenSecret,
    storeDir,
    memoriesUrl,
    memoriesApiKey,
    fetchImpl,
    trustProxy = false,
  } = cfg;

  const store = createStore(storeDir);
  const oauth = createOAuth({ issuer, passwordHash, tokenSecret, store });
  const rateLimit = createRateLimiter();

  const app = express();
  app.disable("x-powered-by");
  // Off by default — req.ip is the raw socket address, so the naive per-IP
  // rate limiter above buckets correctly with no config. Behind a reverse
  // proxy (e.g. local Caddy) every client shares that socket address, which
  // collapses the per-IP bucket into one global bucket — set
  // REMOTE_MCP_TRUST_PROXY=loopback so Express derives req.ip from
  // X-Forwarded-For instead. 'loopback' trusts exactly one hop: the XFF value
  // is only honored when the directly-connecting peer is itself loopback (the
  // local proxy). Never set this to `true` (trusts every hop in the chain)
  // unless every hop in front of this process is a proxy you control — an
  // internet-facing setup with `true` lets any client spoof X-Forwarded-For
  // to pick its own rate-limit bucket.
  if (trustProxy) app.set("trust proxy", trustProxy);
  app.use(express.json({ limit: "1mb" }));
  app.use(express.urlencoded({ extended: false, limit: "1mb" }));

  // -- Health ------------------------------------------------------------
  app.get("/healthz", (req, res) => {
    res.json({ status: "ok", service: "memories-remote-mcp", version: PKG_VERSION });
  });

  // -- OAuth: well-known metadata, DCR, authorize, token ------------------
  app.get("/.well-known/oauth-authorization-server", (req, res) => {
    res.json(oauth.metadataAS());
  });
  app.get("/.well-known/oauth-protected-resource", (req, res) => {
    res.json(oauth.metadataPR());
  });

  app.post("/register", async (req, res) => {
    const r = await oauth.register(req.body);
    res.status(r.status).json(r.body);
  });

  app.get("/authorize", rateLimit, async (req, res) => {
    const r = await oauth.authorizePage(req.query);
    res.status(r.status).type("html").send(r.body);
  });

  app.post("/authorize", rateLimit, async (req, res) => {
    const r = await oauth.handleAuthorize(req.body);
    if (r.redirect) return res.redirect(302, r.redirect);
    res.status(r.status).type("html").send(r.body);
  });

  app.post("/token", rateLimit, async (req, res) => {
    const r = await oauth.token(req.body);
    res.status(r.status).json(r.body);
  });

  // -- MCP: stateless StreamableHTTPServerTransport, fresh server per POST -
  const mcpGuards = [originGuard];
  if (authMode === "oauth") mcpGuards.push(bearerAuth(oauth, issuer));

  app.post("/mcp", ...mcpGuards, async (req, res) => {
    const server = buildServer({ url: memoriesUrl, apiKey: memoriesApiKey, client: "claude-web", fetchImpl });
    try {
      // enableJsonResponse: true — plain JSON response instead of SSE. This is a
      // stateless, single-shot request/response exchange with no server-push
      // notifications to stream, and JSON plays far better with reverse
      // proxies/load balancers that buffer or time out long-lived SSE bodies.
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
      res.on("close", () => {
        transport.close();
        server.close();
      });
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (err) {
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: { code: -32603, message: "Internal server error" },
          id: null,
        });
      }
    }
  });

  // Stateless mode has no session to GET (resume) or DELETE (terminate) —
  // 405 per SDK guidance for the stateless pattern.
  const methodNotAllowed = (req, res) => {
    res.status(405).json({
      jsonrpc: "2.0",
      error: { code: -32000, message: "Method not allowed." },
      id: null,
    });
  };
  app.get("/mcp", ...mcpGuards, methodNotAllowed);
  app.delete("/mcp", ...mcpGuards, methodNotAllowed);

  return app;
}

// -- main -----------------------------------------------------------------

function main() {
  const authMode = process.env.REMOTE_MCP_AUTH || "oauth";
  const cfg = {
    issuer: process.env.REMOTE_MCP_ISSUER,
    port: Number(process.env.REMOTE_MCP_PORT) || 8910,
    authMode,
    passwordHash: process.env.REMOTE_MCP_PASSWORD_HASH,
    tokenSecret: process.env.REMOTE_MCP_TOKEN_SECRET,
    storeDir: process.env.REMOTE_MCP_STORE_DIR || join(homedir(), ".memories", "remote-oauth-store"),
    memoriesUrl: process.env.MEMORIES_URL || "http://localhost:8900",
    memoriesApiKey: process.env.MEMORIES_API_KEY || "",
    trustProxy: process.env.REMOTE_MCP_TRUST_PROXY || false,
  };

  if (authMode === "oauth" && (!cfg.passwordHash || !cfg.tokenSecret)) {
    console.error(
      "REMOTE_MCP_PASSWORD_HASH and REMOTE_MCP_TOKEN_SECRET are required when REMOTE_MCP_AUTH=oauth. " +
      "Set REMOTE_MCP_AUTH=none for local/dev testing without auth."
    );
    process.exit(1);
  }

  const app = createApp(cfg);
  app.listen(cfg.port, () => {
    console.log(`memories-remote-mcp listening on :${cfg.port} (auth=${authMode})`);
  });
}

// npm/npx invoke bin scripts through a symlink under node_modules/.bin, so
// process.argv[1] is the symlink path, not this file's real path — a plain
// === comparison against fileURLToPath(import.meta.url) never matches.
// Resolve through realpath first (same pattern as cli/index.mjs).
const selfPath = fileURLToPath(import.meta.url);
const isMain = (() => {
  try {
    return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === selfPath;
  } catch {
    return false;
  }
})();

if (isMain) main();
