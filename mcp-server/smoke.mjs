#!/usr/bin/env node

import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function startFakeMemoriesApi() {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push({ method: req.method, url: req.url });
    if (req.method === "GET" && req.url.startsWith("/memories/count")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ count: 0 }));
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "unexpected request" }));
  });

  return new Promise((resolve, reject) => {
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve({
        server,
        requests,
        url: `http://127.0.0.1:${address.port}`,
      });
    });
  });
}

async function main() {
  const fakeApi = await startFakeMemoriesApi();
  const client = new Client({ name: "generic-mcp-smoke", version: "0.0.1" });
  const transport = new StdioClientTransport({
    command: "node",
    args: ["index.js"],
    env: {
      ...process.env,
      MEMORIES_URL: fakeApi.url,
      MEMORIES_API_KEY: "test-key",
      MEMORIES_BACKENDS_FILE: "__mcp_smoke_single_backend__",
    },
  });

  try {
    await client.connect(transport);
    const tools = await client.listTools();
    const names = new Set(tools.tools.map((tool) => tool.name));

    for (const required of ["memory_search", "memory_add", "memory_extract", "memory_count"]) {
      assert(names.has(required), `missing MCP tool: ${required}`);
    }

    const result = await client.callTool({
      name: "memory_count",
      arguments: { source: "eval/mcp-smoke" },
    });
    const text = result.content.map((item) => item.text || "").join("\n");
    assert(text.includes('0 memories with source prefix "eval/mcp-smoke".'), "unexpected memory_count response");

    const writes = fakeApi.requests.filter((req) => req.method !== "GET");
    assert(writes.length === 0, `smoke test made write requests: ${JSON.stringify(writes)}`);
    assert(
      fakeApi.requests.some((req) => req.url.startsWith("/memories/count?source=eval%2Fmcp-smoke")),
      `memory_count request not observed: ${JSON.stringify(fakeApi.requests)}`,
    );

    console.log("generic_mcp_stdio_smoke=ok");
  } finally {
    await client.close().catch(() => {});
    await new Promise((resolve) => fakeApi.server.close(resolve));
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
