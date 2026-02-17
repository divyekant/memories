#!/usr/bin/env node

/**
 * Memories MCP Server
 *
 * Exposes the Memories service (localhost:8900) as MCP tools
 * for Claude Code, Claude Desktop, Codex, and any MCP client.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const MEMORIES_URL = process.env.MEMORIES_URL || "http://localhost:8900";
const API_KEY = process.env.MEMORIES_API_KEY || "";

// -- HTTP helper -------------------------------------------------------------

async function memoriesRequest(path, options = {}) {
  const url = `${MEMORIES_URL}${path}`;
  const headers = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const response = await fetch(url, { ...options, headers: { ...headers, ...options.headers } });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Memories API error ${response.status}: ${body}`);
  }

  return response.json();
}

// -- Server ------------------------------------------------------------------

const server = new McpServer({
  name: "memories",
  version: "2.0.0",
});

// -- Tools -------------------------------------------------------------------

server.tool(
  "memory_search",
  "Search memories using semantic similarity. Use hybrid mode for best results (combines meaning + keyword matching). Returns the most relevant memories.",
  {
    query: z.string().describe("Natural language search query"),
    k: z.number().int().min(1).max(50).default(5).describe("Number of results to return"),
    hybrid: z.boolean().default(true).describe("Use hybrid BM25+vector search (recommended)"),
    threshold: z.number().min(0).max(1).optional().describe("Minimum similarity score (0-1)"),
  },
  async ({ query, k = 5, hybrid = true, threshold }) => {
    const body = { query, k, hybrid };
    if (threshold !== undefined) body.threshold = threshold;

    const data = await memoriesRequest("/search", {
      method: "POST",
      body: JSON.stringify(body),
    });

    if (data.count === 0) {
      return { content: [{ type: "text", text: `No memories found for: "${query}"` }] };
    }

    const lines = data.results.map((r, i) => {
      const score = r.similarity ?? r.rrf_score;
      const pct = (score * 100).toFixed(0);
      return `[${i + 1}] (${pct}%) ${r.source}\n${r.text}`;
    });

    return {
      content: [{
        type: "text",
        text: `Found ${data.count} memories for "${query}":\n\n${lines.join("\n\n---\n\n")}`,
      }],
    };
  }
);

server.tool(
  "memory_add",
  "Store a new memory. Memories persist across sessions and are searchable by meaning. Use for decisions, patterns, learnings, bug fixes, preferences.",
  {
    text: z.string().min(1).describe("The memory content to store"),
    source: z.string().min(1).describe("Source identifier (e.g. 'project/decisions.md', 'bug-fix/redis')"),
    deduplicate: z.boolean().default(true).describe("Skip if a very similar memory already exists"),
  },
  async ({ text, source, deduplicate = true }) => {
    const data = await memoriesRequest("/memory/add", {
      method: "POST",
      body: JSON.stringify({ text, source, deduplicate }),
    });

    return {
      content: [{
        type: "text",
        text: data.id !== null
          ? `Memory added (id: ${data.id}) from ${source}`
          : "Duplicate skipped — a very similar memory already exists.",
      }],
    };
  }
);

server.tool(
  "memory_delete",
  "Delete a specific memory by its ID. Use memory_list or memory_search first to find the ID.",
  {
    id: z.number().int().min(0).describe("Memory ID to delete"),
  },
  async ({ id }) => {
    const data = await memoriesRequest(`/memory/${id}`, { method: "DELETE" });
    return {
      content: [{
        type: "text",
        text: `Deleted memory ${id}: "${data.deleted_text}"`,
      }],
    };
  }
);

server.tool(
  "memory_list",
  "Browse stored memories with pagination. Use to see what's in the memory index or filter by source.",
  {
    offset: z.number().int().min(0).default(0).describe("Start position"),
    limit: z.number().int().min(1).max(50).default(20).describe("Number of memories to return"),
    source: z.string().optional().describe("Filter by source (substring match)"),
  },
  async ({ offset = 0, limit = 20, source }) => {
    let url = `/memories?offset=${offset}&limit=${limit}`;
    if (source) url += `&source=${encodeURIComponent(source)}`;

    const data = await memoriesRequest(url);

    if (data.total === 0) {
      return { content: [{ type: "text", text: "No memories found." }] };
    }

    const lines = data.memories.map((m) =>
      `[${m.id}] ${m.source} — ${m.text.substring(0, 150)}${m.text.length > 150 ? "..." : ""}`
    );

    return {
      content: [{
        type: "text",
        text: `Memories (${data.offset + 1}-${data.offset + data.memories.length} of ${data.total}):\n\n${lines.join("\n\n")}`,
      }],
    };
  }
);

server.tool(
  "memory_stats",
  "Get statistics about the memory index — total count, model, last updated.",
  {},
  async () => {
    const data = await memoriesRequest("/stats");
    return {
      content: [{
        type: "text",
        text: [
          `Total memories: ${data.total_memories}`,
          `Model: ${data.model}`,
          `Dimensions: ${data.dimension}`,
          `Index size: ${(data.index_size_bytes / 1024).toFixed(0)}KB`,
          `Backups: ${data.backup_count}`,
          `Last updated: ${data.last_updated || "never"}`,
        ].join("\n"),
      }],
    };
  }
);

server.tool(
  "memory_is_novel",
  "Check if information is already known before adding it. Returns whether the text is novel or if a similar memory exists.",
  {
    text: z.string().min(1).describe("Text to check for novelty"),
    threshold: z.number().min(0).max(1).default(0.88).describe("Similarity threshold (higher = stricter)"),
  },
  async ({ text, threshold = 0.88 }) => {
    const data = await memoriesRequest("/memory/is-novel", {
      method: "POST",
      body: JSON.stringify({ text, threshold }),
    });

    if (data.is_novel) {
      return { content: [{ type: "text", text: "Novel — no similar memory exists. Safe to add." }] };
    }

    const m = data.most_similar;
    const pct = (m.similarity * 100).toFixed(0);
    return {
      content: [{
        type: "text",
        text: `Not novel — similar memory exists (${pct}% match):\n[${m.id}] ${m.source}: ${m.text.substring(0, 200)}`,
      }],
    };
  }
);

// -- Start -------------------------------------------------------------------

const transport = new StdioServerTransport();
await server.connect(transport);
