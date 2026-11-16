// Gate G6: drive the stdio MCP server (mcp-server/index.js) against a real
// Memories server, with no change to the MCP server. Calls each of its tools
// once and prints one JSON line for each call: {step, tool, isError, text}.
// parity/clients.py runs this script against two servers and compares them.
//
// Usage: node server/scripts/g6_mcp.mjs <base url> <api key>
// Run it from the repository root. Needs mcp-server/node_modules (npm ci).
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(join(root, "mcp-server/package.json"));
const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { StdioClientTransport } = require("@modelcontextprotocol/sdk/client/stdio.js");

const [url, key] = process.argv.slice(2);
if (!url || !key) {
  console.error("usage: g6_mcp.mjs <base url> <api key>");
  process.exit(2);
}

const SRC = "claude-code/g6mcp";
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [join(root, "mcp-server/index.js")],
  cwd: process.env.G6_CWD || root,
  env: {
    ...process.env,
    MEMORIES_URL: url,
    MEMORIES_API_KEY: key,
    MEMORIES_BACKENDS_FILE: "__g6_single_backend__",
  },
});
const client = new Client({ name: "g6-mcp", version: "0.0.1" });
await client.connect(transport);

async function call(step, tool, args) {
  const res = await client.callTool({ name: tool, arguments: args });
  const text = (res.content || []).map((c) => c.text || "").join("\n");
  console.log(JSON.stringify({ step, tool, isError: Boolean(res.isError), text: text.slice(0, 4000) }));
  return text;
}

function firstId(text) {
  const m = text.match(/"id":\s*(\d+)/) || text.match(/\bid[:= #]+(\d+)/i) || text.match(/#(\d+)/);
  return m ? Number(m[1]) : 0;
}

try {
  const tools = (await client.listTools()).tools.map((t) => t.name).sort();
  console.log(JSON.stringify({ step: "list-tools", tool: "", isError: false, text: tools.join(",") }));

  const a = firstId(await call("add-a", "memory_add", { text: "The g6 deploy target is the blue cluster in zone four.", source: SRC }));
  const b = firstId(await call("add-b", "memory_add", { text: "Release notes for g6 go into the changelog before each tag.", source: SRC }));
  await call("add-dup", "memory_add", { text: "The g6 deploy target is the blue cluster in zone four.", source: SRC });
  await call("search", "memory_search", { query: "g6 deploy target cluster", source_prefix: SRC, k: 3 });
  await call("search-vector", "memory_search", { query: "g6 deploy target cluster", source_prefix: SRC, k: 3, hybrid: false });
  await call("timeline", "memory_timeline", { query: "g6 release notes", source_prefix: SRC });
  await call("evidence", "memory_evidence", { query: "where does g6 deploy", source_prefix: SRC });
  await call("get", "memory_get", { id: a });
  await call("get-missing", "memory_get", { id: 999999 });
  await call("update", "memory_update", { id: b, text: "Release notes for g6 go into CHANGELOG.md before each tag." });
  await call("list", "memory_list", { source: SRC });
  await call("count", "memory_count", { source: SRC });
  await call("stats", "memory_stats", {});
  await call("is-novel-no", "memory_is_novel", { text: "The g6 deploy target is the blue cluster in zone four." });
  await call("is-novel-yes", "memory_is_novel", { text: "Penguins of the g6 zoo eat sardines on Fridays." });
  await call("is-useful", "memory_is_useful", { memory_id: a, query: "g6 deploy", signal: "useful" });
  await call("conflicts", "memory_conflicts", {});
  await call("missed", "memory_missed", { text: "The g6 build uses cargo nextest.", source: SRC, context: "g6 test" });
  await call("deferred", "memory_deferred", { project: "g6mcp" });
  await call("extract", "memory_extract", { messages: "user: The g6 cache lives in /var/cache/g6.\nassistant: Noted.", source: SRC });
  const c = firstId(await call("add-c", "memory_add", { text: "Temporary g6 record one.", source: `${SRC}/tmp`, deduplicate: false }));
  const d = firstId(await call("add-d", "memory_add", { text: "Temporary g6 record two.", source: `${SRC}/tmp`, deduplicate: false }));
  await call("add-e", "memory_add", { text: "Temporary g6 record three.", source: `${SRC}/old`, deduplicate: false });
  // Python v5.19.0 gives the next add the ID after the highest ID that is left, so a delete
  // of the newest record makes the two servers give different IDs. This record stays.
  await call("add-anchor", "memory_add", { text: "The g6 anchor record stays.", source: `${SRC}/anchor` });
  await call("delete", "memory_delete", { id: b });
  await call("delete-batch", "memory_delete_batch", { ids: [c, d] });
  await call("delete-by-source", "memory_delete_by_source", { source: `${SRC}/old` });
  await call("count-end", "memory_count", { source: SRC });
} finally {
  await client.close();
}
