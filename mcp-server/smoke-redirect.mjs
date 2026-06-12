#!/usr/bin/env node

// Regression: a redirecting MEMORIES_URL (e.g. http:// behind Cloudflare's
// 301 -> https) silently downgraded POST to GET via fetch redirect-following,
// producing opaque 405s on /search while GET endpoints kept working.
// The server must refuse redirects loudly instead of following them.

import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function startServer(handler) {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push({ method: req.method, url: req.url });
    handler(req, res);
  });
  return new Promise((resolve, reject) => {
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, requests, url: `http://127.0.0.1:${server.address().port}` });
    });
  });
}

async function callSearch(env) {
  const client = new Client({ name: "redirect-guard-smoke", version: "0.0.1" });
  const transport = new StdioClientTransport({
    command: "node",
    args: ["index.js"],
    env: { ...process.env, MEMORIES_API_KEY: "test-key", ...env },
  });
  try {
    await client.connect(transport);
    const result = await client.callTool({
      name: "memory_search",
      arguments: { query: "redirect guard", k: 1 },
    });
    return { isError: !!result.isError, text: result.content.map((item) => item.text || "").join("\n") };
  } finally {
    await client.close().catch(() => {});
  }
}

async function main() {
  // The "real" backend, FastAPI-like: /search accepts POST only.
  const target = await startServer((req, res) => {
    if (req.method === "POST" && req.url === "/search") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ count: 0, results: [] }));
      return;
    }
    res.writeHead(405, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: "Method Not Allowed" }));
  });

  // Edge proxy behavior: 301 every request to the target (http -> https analog).
  const redirector = await startServer((req, res) => {
    res.writeHead(301, { Location: `${target.url}${req.url}` });
    res.end();
  });

  // A 304 responder: 3xx but not a redirect — must NOT trip the redirect guard.
  const notModified = await startServer((req, res) => {
    res.writeHead(304);
    res.end();
  });

  const backendsYaml = path.join(os.tmpdir(), `smoke-redirect-backends-${process.pid}.yaml`);
  fs.writeFileSync(
    backendsYaml,
    `backends:\n  one:\n    url: ${redirector.url}\n  two:\n    url: ${redirector.url}\n`,
  );

  try {
    // Case 1: single backend behind a redirect fails loudly, nothing follows the redirect.
    const single = await callSearch({
      MEMORIES_URL: redirector.url,
      MEMORIES_BACKENDS_FILE: "__mcp_smoke_single_backend__",
    });
    assert(single.isError, `memory_search through a redirecting URL must fail loudly, got: ${single.text.slice(0, 200)}`);
    assert(/redirect/i.test(single.text), `error must name the redirect as the cause: ${single.text.slice(0, 200)}`);
    assert(single.text.includes(target.url), `error must show the redirect target so the user can fix the URL: ${single.text.slice(0, 200)}`);
    assert(
      target.requests.length === 0,
      `no request may be re-issued across the redirect (method downgrade), saw: ${JSON.stringify(target.requests)}`,
    );

    // Case 2: multi-backend fan-out must surface the redirect diagnosis, not "All backends failed".
    const multi = await callSearch({ MEMORIES_BACKENDS_FILE: backendsYaml });
    assert(multi.isError, `multi-backend search through redirecting URLs must fail, got: ${multi.text.slice(0, 200)}`);
    assert(/redirect/i.test(multi.text), `multi-backend error must carry the redirect cause: ${multi.text.slice(0, 300)}`);
    assert(multi.text.includes(target.url), `multi-backend error must show the redirect target: ${multi.text.slice(0, 300)}`);

    // Case 3: 304 is not a redirect — must surface as a plain API error, not the redirect guard.
    const cached = await callSearch({
      MEMORIES_URL: notModified.url,
      MEMORIES_BACKENDS_FILE: "__mcp_smoke_single_backend__",
    });
    assert(cached.isError, `304 from the backend must still be an error for a search call, got: ${cached.text.slice(0, 200)}`);
    assert(!/redirect/i.test(cached.text), `304 must not be reported as a redirect: ${cached.text.slice(0, 200)}`);
    assert(/Memories API error 304/.test(cached.text), `304 must surface as a plain API error: ${cached.text.slice(0, 200)}`);

    console.log("redirect_guard_smoke=ok");
  } finally {
    fs.rmSync(backendsYaml, { force: true });
    await new Promise((resolve) => notModified.server.close(resolve));
    await new Promise((resolve) => redirector.server.close(resolve));
    await new Promise((resolve) => target.server.close(resolve));
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
