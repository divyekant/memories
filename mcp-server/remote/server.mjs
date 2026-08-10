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

// -- Config validation --------------------------------------------------------
// Fails closed: an unrecognized authMode must never silently behave like
// "no auth" (or like oauth). Anything other than the two known modes throws
// so the caller (createApp, main()) refuses to start rather than guessing.

export function validateAuthMode(mode) {
  if (mode !== "oauth" && mode !== "none") {
    throw new Error(
      `Invalid REMOTE_MCP_AUTH="${mode}" — must be "oauth" or "none". Refusing to start rather than guess.`
    );
  }
}

// Issuer scheme/host check for oauth mode. https: is required except when the
// issuer's hostname is localhost/127.0.0.1 (local dev/testing), or the
// operator explicitly opts out via REMOTE_MCP_ALLOW_INSECURE_ISSUER=1. An
// http issuer on a real hostname would leak the bearer token and the
// password-login form over plaintext.
export function validateIssuerScheme(issuerStr, { allowInsecure = false } = {}) {
  let url;
  try {
    url = new URL(issuerStr);
  } catch {
    throw new Error(`REMOTE_MCP_ISSUER is not a valid absolute URL: "${issuerStr}"`);
  }
  if (url.protocol === "https:") return;
  const isLocal = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol === "http:" && isLocal) return;
  if (allowInsecure) return;
  throw new Error(
    `REMOTE_MCP_ISSUER="${issuerStr}" must use https: unless the hostname is localhost/127.0.0.1. ` +
    `Set REMOTE_MCP_ALLOW_INSECURE_ISSUER=1 to override (not recommended for a real deployment).`
  );
}

// -- Bearer auth middleware (oauth mode only) --------------------------------

// Wraps an async Express route handler so a rejected promise reaches
// Express's error pipeline via `next(err)`. Express 4 only forwards thrown
// *synchronous* errors automatically — an async handler that rejects (e.g. a
// prototype-polluted client_id producing `undefined.includes(...)` deep
// inside oauth.mjs) instead produces an unhandled promise rejection, which
// crashes the whole process and takes every other in-flight request down
// with it. Every async route below is wrapped in this.
const ah = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

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

// Sentinel strings that mean "disable trust proxy" when REMOTE_MCP_TRUST_PROXY
// is set explicitly rather than left unset. 'false' looks like the obvious
// way to turn a boolean-ish setting off, but env vars are always strings —
// passing the string "false" straight to `app.set("trust proxy", ...)`
// makes Express hand it to proxy-addr, which only recognizes preset names
// ('loopback', 'linklocal', 'uniquelocal') or IP/CIDR values and throws on
// anything else, crashing startup. 'off' is the documented spelling; '' is
// accepted too since that's what an emptied env var resolves to.
const TRUST_PROXY_DISABLE_VALUES = new Set(['off', 'false', '']);

function resolveTrustProxy(trustProxy) {
  if (typeof trustProxy === 'string' && TRUST_PROXY_DISABLE_VALUES.has(trustProxy.trim().toLowerCase())) {
    return false;
  }
  return trustProxy;
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

  validateAuthMode(authMode);
  if (authMode === "none") {
    console.warn(
      "[remote-mcp] AUTH DISABLED (REMOTE_MCP_AUTH=none) — do not expose this to the internet"
    );
  }
  if (authMode === "oauth" && !issuer) {
    throw new Error("REMOTE_MCP_ISSUER is required when REMOTE_MCP_AUTH=oauth.");
  }
  // Mirrors main()'s pre-flight check: tokenSecret signs/verifies every
  // access token (createHmac('sha256', tokenSecret, ...) in oauth.mjs).
  // createHmac throws on a missing/empty key, which — reached only the
  // first time a token is issued or checked, not at startup — would surface
  // as a confusing runtime 500 well after the process looked like it came
  // up fine. Failing fast here catches a misconfigured direct createApp()
  // call (main() already guards its own env-var path) at construction time
  // instead.
  if (authMode === "oauth" && !tokenSecret) {
    throw new Error("tokenSecret is required when REMOTE_MCP_AUTH=oauth.");
  }

  const store = createStore(storeDir);
  const oauth = createOAuth({ issuer, passwordHash, tokenSecret, store });
  const rateLimit = createRateLimiter();
  // /mcp is the authenticated tool-call channel — a legitimate session can
  // burst many rapid tool calls, so it gets a much more generous cap than
  // the unauthenticated OAuth endpoints. This exists purely as
  // defense-in-depth (a leaked bearer token shouldn't buy unthrottled
  // access to the backend), not to throttle normal interactive usage.
  const mcpRateLimit = createRateLimiter({ limit: 120 });

  const app = express();
  app.disable("x-powered-by");
  // Off by default — req.ip is the raw socket address, so the naive per-IP
  // rate limiter above buckets correctly with no config. Behind a reverse
  // proxy (e.g. local Caddy, or the docker/tunnel topology this project
  // ships) every client shares that socket address, which collapses the
  // per-IP bucket into one global bucket. The direct peer in that topology is
  // a container-bridge IP (e.g. 172.17.x.x), NOT loopback — 'loopback' never
  // matches there, so set REMOTE_MCP_TRUST_PROXY=uniquelocal instead: it's an
  // Express trust-proxy preset covering private/link-local/unique-local
  // ranges (RFC 1918 + friends), which is exactly the bridge network a
  // container's proxy hop lives on. Never set this to `true` (trusts every
  // hop in the chain) unless every hop in front of this process is a proxy
  // you control — an internet-facing setup with `true` lets any client spoof
  // X-Forwarded-For to pick its own rate-limit bucket.
  const resolvedTrustProxy = resolveTrustProxy(trustProxy);
  if (resolvedTrustProxy) app.set("trust proxy", resolvedTrustProxy);
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

  app.post("/register", rateLimit, ah(async (req, res) => {
    const r = await oauth.register(req.body);
    res.status(r.status).json(r.body);
  }));

  app.get("/authorize", rateLimit, ah(async (req, res) => {
    const r = await oauth.authorizePage(req.query);
    res.status(r.status).type("html").send(r.body);
  }));

  app.post("/authorize", rateLimit, ah(async (req, res) => {
    const r = await oauth.handleAuthorize(req.body);
    if (r.redirect) return res.redirect(302, r.redirect);
    res.status(r.status).type("html").send(r.body);
  }));

  app.post("/token", rateLimit, ah(async (req, res) => {
    const r = await oauth.token(req.body);
    // RFC 6749 §5.1 — token responses carry bearer material and must never
    // be cached (by the client, or by any intermediary). Applied to both
    // success and error bodies rather than branching on r.status: an
    // error response can still leak grant-shaped information and there's
    // no upside to caching it either.
    res.set({ "Cache-Control": "no-store", "Pragma": "no-cache" });
    res.status(r.status).json(r.body);
  }));

  // -- MCP: stateless StreamableHTTPServerTransport, fresh server per POST -
  // mcpRateLimit first — cheapest check, rejects a flood before spending
  // cycles on origin/auth checks for it.
  const mcpGuards = [mcpRateLimit, originGuard];
  if (authMode === "oauth") mcpGuards.push(bearerAuth(oauth, issuer));

  app.post("/mcp", ...mcpGuards, ah(async (req, res) => {
    const server = buildServer({
      url: memoriesUrl,
      apiKey: memoriesApiKey,
      client: "claude-web",
      fetchImpl,
      // The remote entry point's backend is fully specified by env
      // (MEMORIES_URL/MEMORIES_API_KEY) — never let a stray
      // .memories/backends.yaml or ~/.config/memories/backends.yaml on the
      // host silently redirect it to a different backend.
      skipFileConfig: true,
    });
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
  }));

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

  // Terminal error middleware — catches anything `ah()` forwards via
  // next(err) (or that Express itself catches from a synchronous throw).
  // Generic message only: an unhandled error's `.message`/`.stack` can
  // contain file paths or internal state, and this runs for every route
  // including unauthenticated ones, so nothing error-specific gets echoed
  // back to the caller. Logged server-side for operators instead.
  app.use((err, req, res, next) => {
    console.error("[remote-mcp] unhandled error", err);
    if (res.headersSent) return next(err);
    res.status(500).json({ error: "internal_server_error" });
  });

  return app;
}

// -- main -----------------------------------------------------------------

function main() {
  const authMode = process.env.REMOTE_MCP_AUTH || "oauth";

  try {
    validateAuthMode(authMode);
  } catch (err) {
    console.error(`[remote-mcp] ${err.message}`);
    process.exit(1);
  }

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

  if (authMode === "oauth") {
    if (!cfg.passwordHash || !cfg.tokenSecret) {
      console.error(
        "REMOTE_MCP_PASSWORD_HASH and REMOTE_MCP_TOKEN_SECRET are required when REMOTE_MCP_AUTH=oauth. " +
        "Set REMOTE_MCP_AUTH=none for local/dev testing without auth."
      );
      process.exit(1);
    }
    if (!cfg.issuer) {
      console.error("[remote-mcp] REMOTE_MCP_ISSUER is required when REMOTE_MCP_AUTH=oauth.");
      process.exit(1);
    }
    try {
      validateIssuerScheme(cfg.issuer, {
        allowInsecure: process.env.REMOTE_MCP_ALLOW_INSECURE_ISSUER === "1",
      });
    } catch (err) {
      console.error(`[remote-mcp] ${err.message}`);
      process.exit(1);
    }
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
