#!/usr/bin/env node

/**
 * Memories MCP Server
 *
 * Exposes the Memories service (localhost:8900) as MCP tools
 * for Claude Code, Claude Desktop, Codex, and any MCP client.
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { buildServer } from "./lib-tools.mjs";

// -- CLI dispatch --------------------------------------------------------------
// npm exec runs the bin whose name matches the package ("memories-mcp" -> this
// file), so `npx memories-mcp init` launches THIS file, not cli/index.mjs — the
// installer CLI is only reachable through the "memories" bin normally. Detect
// known CLI subcommands here and hand off before any server setup runs. No args
// (how MCP clients launch this) and unrecognized args fall through to the
// server, unchanged.
const CLI_COMMANDS = new Set(['init', 'doctor', 'update', 'uninstall', 'help', '--help', '-h']);
if (CLI_COMMANDS.has(process.argv[2])) {
  const { run } = await import('./cli/index.mjs');
  try {
    await run(process.argv.slice(2));
  } catch (err) {
    console.error(err.message);
    process.exitCode = 1;
  }
  process.exit(process.exitCode ?? 0);
}

// -- Server ------------------------------------------------------------------

const server = buildServer({
  url: process.env.MEMORIES_URL ?? "http://localhost:8900",
  apiKey: process.env.MEMORIES_API_KEY ?? "",
  client: process.env.MEMORIES_CLIENT ?? (process.env.CODEX_THREAD_ID ? "codex" : "mcp"),
});

// -- Start -------------------------------------------------------------------

const transport = new StdioServerTransport();
await server.connect(transport);
