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
  version: "3.1.0",
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
    feedback_weight: z.number().min(0).max(1).default(0.1).describe("Weight for feedback-based ranking (0=disabled, default 0.1)"),
    confidence_weight: z.number().min(0).max(1).default(0).describe("Weight for confidence-based ranking (0=disabled)"),
  },
  async ({ query, k = 5, hybrid = true, threshold, feedback_weight, confidence_weight }) => {
    const body = { query, k, hybrid };
    if (threshold !== undefined) body.threshold = threshold;
    if (feedback_weight !== undefined) body.feedback_weight = feedback_weight;
    if (confidence_weight !== undefined && confidence_weight > 0) body.confidence_weight = confidence_weight;

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
  "memory_delete_batch",
  "Delete multiple memories by ID in one operation.",
  {
    ids: z.array(z.number().int().min(0)).min(1).max(1000).describe("Memory IDs to delete"),
  },
  async ({ ids }) => {
    // Auto-snapshot before bulk delete (no opt-out for agents)
    await memoriesRequest("/snapshots", { method: "POST" });

    const data = await memoriesRequest("/memory/delete-batch", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    return {
      content: [{
        type: "text",
        text: `Deleted ${data.deleted_count} memories. IDs: ${data.deleted_ids.join(", ") || "none"}`
          + (data.missing_ids?.length ? ` (missing: ${data.missing_ids.join(", ")})` : ""),
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
    source: z.string().optional().describe("Filter by source prefix (e.g. 'project/decisions' matches 'project/decisions/2024.md')"),
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
  "memory_delete_by_source",
  "Delete all memories whose source starts with a given prefix. Returns count of deleted. Use for bulk cleanup of an entire source/project.",
  {
    source: z.string().min(1).describe("Source prefix to match (e.g. 'old-project/' deletes all memories from that project)"),
  },
  async ({ source }) => {
    // Auto-snapshot before bulk delete (no opt-out for agents)
    await memoriesRequest("/snapshots", { method: "POST" });

    const data = await memoriesRequest(`/memories?source=${encodeURIComponent(source)}`, {
      method: "DELETE",
    });
    return {
      content: [{
        type: "text",
        text: `Deleted ${data.count} memories with source prefix "${source}".`,
      }],
    };
  }
);

server.tool(
  "memory_count",
  "Count memories, optionally filtered by source prefix. Lightweight check without listing all memories.",
  {
    source: z.string().optional().describe("Source prefix filter (e.g. 'project/docs')"),
  },
  async ({ source }) => {
    let url = "/memories/count";
    if (source) url += `?source=${encodeURIComponent(source)}`;

    const data = await memoriesRequest(url);
    const label = source ? `memories with source prefix "${source}"` : "total memories";
    return {
      content: [{
        type: "text",
        text: `${data.count} ${label}.`,
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

server.tool(
  "memory_is_useful",
  "Submit relevance feedback for a memory retrieved via search. Call after using a memory to signal whether it was helpful. Helps improve future search quality.",
  {
    memory_id: z.number().int().describe("ID of the memory to rate"),
    query: z.string().optional().describe("The search query that surfaced this memory"),
    signal: z.enum(["useful", "not_useful"]).describe("Whether the memory was helpful"),
  },
  async ({ memory_id, query = "", signal }) => {
    await memoriesRequest("/search/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ memory_id, query, signal }),
    });
    return {
      content: [{ type: "text", text: `Feedback recorded: memory ${memory_id} marked as ${signal}` }],
    };
  }
);

server.tool(
  "memory_conflicts",
  "List memories that conflict with each other. Conflicts are flagged during extraction when contradictory facts are detected. Use to review and resolve contradictions.",
  {},
  async () => {
    const data = await memoriesRequest("/memory/conflicts");

    if (!data.conflicts || data.conflicts.length === 0) {
      return { content: [{ type: "text", text: "No conflicts found." }] };
    }

    const lines = data.conflicts.map((c) => {
      const other = c.conflicting_memory;
      const otherText = other ? `${other.text.substring(0, 150)}` : "(deleted)";
      return `[${c.id}] "${c.text.substring(0, 150)}" CONFLICTS WITH [${c.conflicts_with}] "${otherText}"`;
    });

    return {
      content: [{
        type: "text",
        text: `${data.count} conflict(s) found:\n\n${lines.join("\n\n")}`,
      }],
    };
  }
);

server.tool(
  "memory_extract",
  "Extract and store memories from conversation text using LLM-based AUDN (Add/Update/Delete/Noop/Conflict). Costs ~$0.001 per call. Use when decisions change, deferred work completes, or rich conversation contains multiple facts worth remembering. Returns what was added, updated, deleted, conflicted, or skipped.",
  {
    messages: z.string().min(1).describe("Conversation text to extract memories from"),
    source: z.string().min(1).describe("Source identifier (e.g. 'claude-code/myapp')"),
    context: z.enum(["stop", "pre_compact", "session_end"]).default("stop")
      .describe("Extraction intensity: 'stop' (standard), 'pre_compact' (aggressive), 'session_end'"),
  },
  async ({ messages, source, context = "stop" }) => {
    // Submit extraction job
    const submitData = await memoriesRequest("/memory/extract", {
      method: "POST",
      body: JSON.stringify({ messages, source, context }),
    });

    const jobId = submitData.job_id;

    // Poll until terminal state (exponential backoff: 200ms → 2s, 30s timeout)
    let delay = 200;
    const maxDelay = 2000;
    const deadline = Date.now() + 30_000;
    let jobState;

    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, delay));
      jobState = await memoriesRequest(`/memory/extract/${jobId}`);
      if (jobState.status === "completed" || jobState.status === "failed") break;
      delay = Math.min(delay * 2, maxDelay);
    }

    if (!jobState || (jobState.status !== "completed" && jobState.status !== "failed")) {
      return {
        content: [{ type: "text", text: `Extraction timed out (job ${jobId}). Check /memory/extract/${jobId} later.` }],
      };
    }

    if (jobState.status === "failed") {
      const err = jobState.result?.error || jobState.result?.error_message || "unknown error";
      return {
        content: [{ type: "text", text: `Extraction failed: ${err}` }],
      };
    }

    // Format results
    const r = jobState.result;
    const noopCount = Math.max(0, r.extracted_count - r.stored_count - r.updated_count - r.deleted_count);
    const lines = [];
    lines.push(`Extracted ${r.extracted_count} facts: ${r.stored_count} added, ${r.updated_count} updated, ${r.deleted_count} deleted, ${noopCount} skipped`);

    if (r.actions?.length) {
      lines.push("");
      for (const a of r.actions) {
        if (a.action === "add") lines.push(`  + ADD: ${a.text}`);
        else if (a.action === "update") lines.push(`  ~ UPDATE (was #${a.old_id}): ${a.text}`);
        else if (a.action === "delete") lines.push(`  - DELETE #${a.old_id}`);
        else if (a.action === "noop") lines.push(`  = SKIP (exists #${a.existing_id}): ${a.text}`);
        else if (a.action === "error") lines.push(`  ! ERROR: ${a.text} — ${a.error}`);
      }
    }

    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

server.tool(
  "memory_missed",
  "Flag a memory that should have been captured by extraction but wasn't.",
  {
    text: z.string().min(1).describe("The fact that should have been remembered"),
    source: z.string().min(1).describe("Source identifier"),
    context: z.string().optional().describe("Optional context"),
  },
  async ({ text, source, context }) => {
    const body = { text, source };
    if (context) body.context = context;
    const data = await memoriesRequest("/memory/missed", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return {
      content: [{
        type: "text",
        text: `Memory stored (id: ${data.id}) from ${data.source}. Missed count: ${data.missed_count}`,
      }],
    };
  }
);

server.tool(
  "memory_deferred",
  "List deferred/WIP memories for a project. Surfaces incomplete threads from wip/{project} source prefix.",
  {
    project: z.string().min(1).describe("Project name to search wip/ prefix for"),
    k: z.number().int().min(1).max(20).default(5).describe("Number of results"),
  },
  async ({ project, k = 5 }) => {
    const data = await memoriesRequest("/search", {
      method: "POST",
      body: JSON.stringify({
        query: "deferred incomplete blocked todo revisit wip",
        k,
        hybrid: true,
        source_prefix: `wip/${project}`,
      }),
    });
    if (data.count === 0) {
      return { content: [{ type: "text", text: `No deferred work found for project "${project}"` }] };
    }
    const lines = data.results.map((r, i) => `[${i + 1}] ${r.source}\n${r.text}`);
    return {
      content: [{ type: "text", text: `${data.count} deferred item(s) for "${project}":\n\n${lines.join("\n\n---\n\n")}` }],
    };
  }
);

// -- Start -------------------------------------------------------------------

const transport = new StdioServerTransport();
await server.connect(transport);
