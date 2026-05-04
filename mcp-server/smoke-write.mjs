#!/usr/bin/env node

import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function readJsonBody(req, record, callback) {
  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
  });
  req.on("end", () => {
    const parsed = JSON.parse(body || "{}");
    record.body = parsed;
    callback(parsed);
  });
}

function startFakeMemoriesApi() {
  const requests = [];
  const server = http.createServer((req, res) => {
    const record = { method: req.method, url: req.url };
    requests.push(record);

    if (req.method === "POST" && req.url === "/memory/add") {
      readJsonBody(req, record, () => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ id: 77, source: record.body.source }));
      });
      return;
    }

    if (req.method === "POST" && req.url === "/memory/extract") {
      readJsonBody(req, record, () => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ job_id: "job-1" }));
      });
      return;
    }

    if (req.method === "GET" && req.url === "/memory/extract/job-1") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        status: "completed",
        result: {
          extracted_count: 1,
          stored_count: 1,
          updated_count: 0,
          deleted_count: 0,
          actions: [{ action: "add", text: "Active-search write smoke captured." }],
        },
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
  const client = new Client({ name: "generic-mcp-write-smoke", version: "0.0.1" });
  const transport = new StdioClientTransport({
    command: "node",
    args: ["index.js"],
    env: {
      ...process.env,
      MEMORIES_URL: fakeApi.url,
      MEMORIES_API_KEY: "test-key",
      MEMORIES_BACKENDS_FILE: "__mcp_write_smoke_single_backend__",
    },
  });

  const source = "codex/memories/mcp-write-smoke/written-by-codex";
  try {
    await client.connect(transport);

    const addResult = await client.callTool({
      name: "memory_add",
      arguments: {
        text: "Active-search write smoke captured. Written by Codex.",
        source,
        deduplicate: false,
        document_at: "2026-05-04T00:00:00Z",
      },
    });
    const addText = addResult.content.map((item) => item.text || "").join("\n");
    assert(addText.includes("Memory added (id: 77)"), `unexpected memory_add response: ${addText}`);

    const extractResult = await client.callTool({
      name: "memory_extract",
      arguments: {
        messages: "User: store the active-search write smoke decision.\nAssistant: Written by Codex.",
        source,
        context: "stop",
        document_at: "2026-05-04T00:00:00Z",
      },
    });
    const extractText = extractResult.content.map((item) => item.text || "").join("\n");
    assert(extractText.includes("Extracted 1 facts: 1 added"), `unexpected memory_extract response: ${extractText}`);

    const addRequest = fakeApi.requests.find((req) => req.url === "/memory/add");
    assert(addRequest?.body?.source === source, `memory_add used wrong source: ${JSON.stringify(addRequest)}`);
    assert(addRequest?.body?.metadata?.document_at === "2026-05-04T00:00:00Z", "memory_add omitted document_at metadata");

    const extractRequest = fakeApi.requests.find((req) => req.url === "/memory/extract");
    assert(extractRequest?.body?.source === source, `memory_extract used wrong source: ${JSON.stringify(extractRequest)}`);
    assert(extractRequest?.body?.document_at === "2026-05-04T00:00:00Z", "memory_extract omitted document_at");
    assert(
      fakeApi.requests.some((req) => req.url === "/memory/extract/job-1"),
      `memory_extract poll was not observed: ${JSON.stringify(fakeApi.requests)}`,
    );

    console.log("generic_mcp_write_smoke=ok");
  } finally {
    await client.close().catch(() => {});
    await new Promise((resolve) => fakeApi.server.close(resolve));
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
