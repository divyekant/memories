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
    const record = { method: req.method, url: req.url };
    requests.push(record);
    if (req.method === "GET" && req.url.startsWith("/memories/count")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ count: 0 }));
      return;
    }
    if (req.method === "POST" && req.url === "/search/evidence") {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        const parsed = JSON.parse(body || "{}");
        record.body = parsed;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          query: parsed.query,
          count: 0,
          results: [],
          evidence_packet: {
            current_answer: null,
            supporting_memories: [],
            older_conflicting_memories: [],
            source_date_trail: [],
            confidence: { level: "missing", reasons: ["No memories were retrieved for this query."] },
            follow_up_queries: [parsed.query],
          },
        }));
      });
      return;
    }
    if (req.method === "POST" && req.url === "/search") {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk;
      });
      req.on("end", () => {
        const parsed = JSON.parse(body || "{}");
        record.body = parsed;
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          count: 1,
          results: [{
            id: 42,
            source: "eval/mcp-smoke/decision",
            text: "user: deployment target is fly.io ".repeat(40),
            similarity: 0.91,
            document_at: "2023-05-20T00:00:00+00:00",
          }],
        }));
      });
      return;
    }
    if (req.method === "GET" && req.url === "/memory/42") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        id: 42,
        source: "eval/mcp-smoke/decision",
        text: "user: deployment target is fly.io ".repeat(40),
        document_at: "2023-05-20T00:00:00+00:00",
      }));
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

    for (const required of ["memory_search", "memory_get", "memory_add", "memory_extract", "memory_count", "memory_evidence", "memory_timeline"]) {
      assert(names.has(required), `missing MCP tool: ${required}`);
    }

    const result = await client.callTool({
      name: "memory_count",
      arguments: { source: "eval/mcp-smoke" },
    });
    const text = result.content.map((item) => item.text || "").join("\n");
    assert(text.includes('0 memories with source prefix "eval/mcp-smoke".'), "unexpected memory_count response");

    const evidence = await client.callTool({
      name: "memory_evidence",
      arguments: {
        query: "latest deployment target",
        source_prefix: "eval/mcp-smoke",
        reference_date: "2023-05-20T00:00:00+00:00",
      },
    });
    const evidenceText = evidence.content.map((item) => item.text || "").join("\n");
    assert(evidenceText.includes("Confidence: missing"), "unexpected memory_evidence response");
    assert(
      fakeApi.requests.some((req) => req.body?.reference_date === "2023-05-20T00:00:00+00:00"),
      `memory_evidence reference_date was not forwarded: ${JSON.stringify(fakeApi.requests)}`,
    );

    const compactSearch = await client.callTool({
      name: "memory_search",
      arguments: {
        query: "deployment target",
        source_prefix: "eval/mcp-smoke",
        compact: true,
      },
    });
    const compactText = compactSearch.content.map((item) => item.text || "").join("\n");
    assert(compactText.includes("Use memory_get id=42"), "compact search did not point to memory_get");
    assert(compactText.length < 600, `compact search returned too much text: ${compactText.length}`);

    const timeline = await client.callTool({
      name: "memory_timeline",
      arguments: {
        query: "deployment target",
        source_prefix: "eval/mcp-smoke",
        reference_date: "2023-05-20T00:00:00+00:00",
        user_facts_only: true,
      },
    });
    const timelineText = timeline.content.map((item) => item.text || "").join("\n");
    assert(timelineText.includes("Timeline for \"deployment target\""), "unexpected memory_timeline response");
    assert(timelineText.includes("2023-05-20T00:00:00+00:00"), "timeline omitted memory date");
    assert(
      fakeApi.requests.some((req) => req.body?.reference_date === "2023-05-20T00:00:00+00:00" && req.url === "/search"),
      `memory_timeline reference_date was not forwarded: ${JSON.stringify(fakeApi.requests)}`,
    );

    const fetched = await client.callTool({
      name: "memory_get",
      arguments: { id: 42 },
    });
    const fetchedText = fetched.content.map((item) => item.text || "").join("\n");
    assert(fetchedText.includes("[42] eval/mcp-smoke/decision"), "memory_get did not return requested memory");
    assert(fetchedText.length > compactText.length, "memory_get should return fuller detail than compact search");

    const writes = fakeApi.requests.filter(
      (req) => req.method !== "GET" && !["/search/evidence", "/search"].includes(req.url),
    );
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
