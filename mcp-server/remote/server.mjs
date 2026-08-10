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
// Single-process deployment; no need for a shared store. Not reset between
// requests within the window — old hits just age out of the filter.

function createRateLimiter({ limit = 20, windowMs = 60_000 } = {}) {
  const hits = new Map();
  return function rateLimit(req, res, next) {
    const ip = req.ip || req.socket?.remoteAddress || "unknown";
    const now = Date.now();
    const recent = (hits.get(ip) || []).filter((t) => now - t < windowMs);
    recent.push(now);
    hits.set(ip, recent);
    if (recent.length > limit) {
      return res.status(429).json({ error: "rate_limited" });
    }
    next();
  };
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
  } = cfg;

  const store = createStore(storeDir);
  const oauth = createOAuth({ issuer, passwordHash, tokenSecret, store });
  const rateLimit = createRateLimiter();

  const app = express();
  app.disable("x-powered-by");
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
