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
import fs from "fs";
import path from "path";
import yaml from "js-yaml";

// -- Config Loading ----------------------------------------------------------

function loadBackends() {
  // Resolution: MEMORIES_BACKENDS_FILE -> project -> global -> env fallback
  // If MEMORIES_BACKENDS_FILE is explicitly set (even to a nonexistent path),
  // skip project/global config resolution — this allows callers (like the eval
  // harness) to force single-backend mode by setting the env var.
  const explicitFile = process.env.MEMORIES_BACKENDS_FILE;
  const configPaths = explicitFile
    ? [explicitFile]  // Only check the explicit path, skip project/global
    : [
        path.join(process.cwd(), ".memories", "backends.yaml"),
        path.join(process.env.HOME || "", ".config", "memories", "backends.yaml"),
      ];

  for (const p of configPaths) {
    if (fs.existsSync(p)) {
      const raw = yaml.load(fs.readFileSync(p, "utf8"));
      const interp = (v) => { const m = (v || "").match(/\$\{(\w+)\}/); return m ? (process.env[m[1]] || v) : v; };
      const backends = Object.entries(raw.backends || {}).map(([name, cfg]) => {
        return { name, url: interp(cfg.url || ""), apiKey: interp(cfg.api_key || ""), scenario: cfg.scenario || "" };
      });
      const routing = raw.routing || {};
      return { backends, routing };
    }
  }

  // Fallback to env vars
  return {
    backends: [{
      name: "default",
      url: process.env.MEMORIES_URL || "http://localhost:8900",
      apiKey: process.env.MEMORIES_API_KEY || "",
      scenario: "",
    }],
    routing: {},
  };
}

const config = loadBackends();

function getBackendsForOp(op) {
  // Explicit routing first
  if (config.routing[op]) {
    const names = config.routing[op];
    return config.backends.filter(b => names.includes(b.name));
  }
  // Single backend
  if (config.backends.length === 1) return config.backends;
  // Scenario-based
  switch (op) {
    case "search": return config.backends;
    case "extract": return config.backends.filter(b => b.scenario === "dev" || b.scenario === "personal");
    case "add": return config.backends;
    case "feedback": return config.backends.filter(b => b.scenario === "dev" || b.scenario === "personal");
    default: return [config.backends[0]];
  }
}

// -- HTTP helper -------------------------------------------------------------

async function memoriesRequest(reqPath, options = {}, op = "search") {
  const backends = getBackendsForOp(op);

  if (backends.length === 1) {
    // Single backend — direct call (backward compat)
    const b = backends[0];
    const url = `${b.url}${reqPath}`;
    const headers = { "Content-Type": "application/json" };
    if (b.apiKey) headers["X-API-Key"] = b.apiKey;
    const response = await fetch(url, { ...options, headers: { ...headers, ...options.headers } });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Memories API error ${response.status}: ${body}`);
    }
    return response.json();
  }

  // Multi-backend — parallel fan-out
  const results = await Promise.allSettled(
    backends.map(async (b) => {
      const url = `${b.url}${reqPath}`;
      const headers = { "Content-Type": "application/json" };
      if (b.apiKey) headers["X-API-Key"] = b.apiKey;
      const response = await fetch(url, { ...options, headers: { ...headers, ...options.headers } });
      if (!response.ok) throw new Error(`${b.name}: HTTP ${response.status}`);
      const data = await response.json();
      return { backend: b.name, data };
    })
  );

  // Collect successful results
  const successes = results.filter(r => r.status === "fulfilled").map(r => r.value);
  if (successes.length === 0) {
    throw new Error("All backends failed");
  }

  // For non-search operations, return first success
  if (op !== "search") return successes[0].data;

  // For search: merge results
  const allResults = [];
  for (const s of successes) {
    for (const r of (s.data.results || [])) {
      allResults.push({ ...r, _backend: s.backend });
    }
  }

  // Sort by score FIRST so dedup (first-seen wins) keeps the highest-scoring result
  allResults.sort((a, b) => (b.similarity ?? b.rrf_score ?? 0) - (a.similarity ?? a.rrf_score ?? 0));

  // Dedup by exact text match
  const seen = new Set();
  const deduped = allResults.filter(r => {
    const key = r.text || "";
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return { results: deduped, count: deduped.length };
}

// -- Server ------------------------------------------------------------------

const server = new McpServer({
  name: "memories",
  version: "5.0.1",
});

// -- Tools -------------------------------------------------------------------

function memoryId(memory) {
  return memory.id ?? memory.memory_id ?? "unknown";
}

function memoryDate(memory) {
  return memory.document_at || memory.date || memory.created_at || "";
}

function snippet(text, maxChars = 220) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= maxChars) return clean;
  return `${clean.slice(0, maxChars).trim()}...`;
}

function chronologicalValue(memory) {
  const date = memoryDate(memory);
  const parsed = Date.parse(date);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

function hasUserFact(memory) {
  const text = String(memory?.text || "");
  if (!text.trim()) return false;
  if (/^\s*user\s*:/im.test(text)) return true;
  if (/^\s*assistant\s*:/im.test(text)) return false;
  return true;
}

function timelineQueryVariants(query) {
  const clean = String(query || "").trim();
  const variants = [clean];
  if (/\b(trip|trips|travel|vacation|visited|went|outing|hike|hikes)\b/i.test(clean)) {
    variants.push(`${clean} day hike outing excursion just got back returned`);
    variants.push(`${clean} day hike outing excursion family just got back`);
  }
  return [...new Set(variants.filter(Boolean))];
}

server.tool(
  "memory_search",
  "Search memories using semantic similarity. Use hybrid mode for best results (combines meaning + keyword matching). Returns the most relevant memories. When memories contain conversation transcript text, treat user: lines as user-stated facts; assistant: lines may be suggestions, plans, or examples unless a user message confirms they happened. For temporal ordering, prefer direct dated user evidence over vague incidental recency mentions, deduplicate repeated mentions of the same event, and include user-confirmed outings such as day hikes when the query asks about trips.",
  {
    query: z.string().describe("Natural language search query"),
    k: z.number().int().min(1).max(50).default(5).describe("Number of results to return"),
    hybrid: z.boolean().default(true).describe("Use hybrid BM25+vector search (recommended)"),
    threshold: z.number().min(0).max(1).optional().describe("Minimum similarity score (0-1)"),
    source_prefix: z.string().optional().describe("Filter by source prefix (e.g. 'claude-code/myproject' or 'eval/longmemeval/q42')"),
    feedback_weight: z.number().min(0).max(1).default(0.1).describe("Weight for feedback-based ranking (0=disabled, default 0.1)"),
    confidence_weight: z.number().min(0).max(1).default(0).describe("Weight for confidence-based ranking (0=disabled)"),
    graph_weight: z.number().min(0).max(1).default(0.1).describe("Weight for graph-based link expansion (0=disabled, default 0.1). Linked memories get bonus score."),
    since: z.string().optional().describe("Filter memories at or after this ISO date (e.g. 2023-01-01T00:00:00Z)"),
    until: z.string().optional().describe("Filter memories at or before this ISO date"),
    reference_date: z.string().optional().describe("Reference date for relative temporal queries such as today, yesterday, or past three months"),
    include_archived: z.boolean().default(false).describe("Include archived/superseded memories (needed for version history queries)"),
    compact: z.boolean().default(false).describe("Return compact snippets with IDs. Use memory_get on a selected ID for full text."),
  },
  async ({ query, k = 5, hybrid = true, threshold, source_prefix, feedback_weight, confidence_weight, graph_weight, since, until, reference_date, include_archived, compact = false }) => {
    const body = { query, k, hybrid };
    if (threshold !== undefined) body.threshold = threshold;
    if (source_prefix) body.source_prefix = source_prefix;
    if (feedback_weight !== undefined) body.feedback_weight = feedback_weight;
    if (confidence_weight !== undefined && confidence_weight > 0) body.confidence_weight = confidence_weight;
    if (graph_weight !== undefined) body.graph_weight = graph_weight;
    if (since) body.since = since;
    if (until) body.until = until;
    if (reference_date) body.reference_date = reference_date;
    if (include_archived) body.include_archived = true;

    const data = await memoriesRequest("/search", {
      method: "POST",
      body: JSON.stringify(body),
    }, "search");

    if (data.count === 0) {
      return { content: [{ type: "text", text: `No memories found for: "${query}"` }] };
    }

    if (compact) {
      const lines = data.results.map((r, i) => {
        const score = r.similarity ?? r.rrf_score;
        const pct = score !== undefined ? ` (${(score * 100).toFixed(0)}%)` : "";
        const id = memoryId(r);
        const date = memoryDate(r);
        const dateText = date ? ` ${date}` : "";
        return `[${i + 1}] id=${id}${pct} ${r.source || "unknown-source"}${dateText}\n${snippet(r.text)}\nUse memory_get id=${id} for full text.`;
      });

      return {
        content: [{
          type: "text",
          text: `Found ${data.count} compact memories for "${query}":\n\n${lines.join("\n\n")}`,
        }],
      };
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
  "memory_timeline",
  "Search memories and return compact results sorted chronologically. Use for temporal ordering, date math, and multi-event questions. Treat user: lines as user-stated facts; assistant: lines may be suggestions or plans unless a user message confirms they happened. Prefer direct dated user evidence over vague incidental recency mentions.",
  {
    query: z.string().describe("Natural language search query"),
    k: z.number().int().min(1).max(50).default(20).describe("Number of results to consider"),
    hybrid: z.boolean().default(true).describe("Use hybrid BM25+vector search"),
    threshold: z.number().min(0).max(1).optional().describe("Minimum similarity score (0-1)"),
    source_prefix: z.string().optional().describe("Filter by source prefix"),
    feedback_weight: z.number().min(0).max(1).default(0.1).describe("Weight for feedback-based ranking signal"),
    confidence_weight: z.number().min(0).max(1).default(0).describe("Weight for confidence-based ranking"),
    graph_weight: z.number().min(0).max(1).default(0.1).describe("Weight for graph-based link expansion"),
    since: z.string().optional().describe("Filter memories at or after this ISO date"),
    until: z.string().optional().describe("Filter memories at or before this ISO date"),
    reference_date: z.string().optional().describe("Reference date for relative temporal queries such as today, yesterday, or past three months"),
    include_archived: z.boolean().default(false).describe("Include archived/superseded memories"),
    user_facts_only: z.boolean().default(false).describe("Keep only results containing user: transcript facts. Use for questions about what the user did, took, bought, visited, or decided."),
  },
  async ({ query, k = 20, hybrid = true, threshold, source_prefix, feedback_weight, confidence_weight, graph_weight, since, until, reference_date, include_archived, user_facts_only = false }) => {
    const seen = new Set();
    const merged = [];
    const searches = timelineQueryVariants(query).map(async (variant) => {
      const body = { query: variant, k, hybrid };
      if (threshold !== undefined) body.threshold = threshold;
      if (source_prefix) body.source_prefix = source_prefix;
      if (feedback_weight !== undefined) body.feedback_weight = feedback_weight;
      if (confidence_weight !== undefined && confidence_weight > 0) body.confidence_weight = confidence_weight;
      if (graph_weight !== undefined) body.graph_weight = graph_weight;
      if (since) body.since = since;
      if (until) body.until = until;
      if (reference_date) body.reference_date = reference_date;
      if (include_archived) body.include_archived = true;

      const data = await memoriesRequest("/search", {
        method: "POST",
        body: JSON.stringify(body),
      }, "search");
      return data.results || [];
    });
    for (const results of await Promise.all(searches)) {
      for (const result of results) {
        if (user_facts_only && !hasUserFact(result)) continue;
        const key = `${memoryId(result)}:${result.source || ""}:${memoryDate(result)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        merged.push(result);
      }
    }
    const results = merged.sort((a, b) => chronologicalValue(a) - chronologicalValue(b));

    if (!results.length) {
      return { content: [{ type: "text", text: `No timeline memories found for: "${query}"` }] };
    }

    const lines = results.map((r, i) => {
      const id = memoryId(r);
      const date = memoryDate(r) || "unknown-date";
      const score = r.similarity ?? r.rrf_score;
      const pct = score !== undefined ? ` score=${(score * 100).toFixed(0)}%` : "";
      const fact = hasUserFact(r) ? "user-fact" : "assistant-or-mixed";
      return `[${i + 1}] ${date} id=${id}${pct} fact=${fact} ${r.source || "unknown-source"}\n${snippet(r.text, 360)}`;
    });

    return {
      content: [{
        type: "text",
        text: `Timeline for "${query}" (chronological; verify user-stated facts before answering):\n\n${lines.join("\n\n")}`,
      }],
    };
  }
);

server.tool(
  "memory_get",
  "Fetch one memory by ID. Use after compact memory_search results when you need the full text and metadata for a selected memory.",
  {
    id: z.number().int().min(0).describe("Memory ID to fetch"),
  },
  async ({ id }) => {
    const data = await memoriesRequest(`/memory/${id}`, {}, "manage");
    const date = memoryDate(data);
    const lines = [
      `[${data.id ?? id}] ${data.source || "unknown-source"}${date ? ` ${date}` : ""}`,
      "",
      data.text || "",
    ];
    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

server.tool(
  "memory_evidence",
  "Search memories and return an agent-facing evidence packet with the current candidate, older evidence, source/date trail, confidence, and follow-up queries. Use for latest/current/temporal questions where a flat hit list is easy to misread. When memories contain conversation transcript text, treat user: lines as user-stated facts; assistant: lines may be suggestions, plans, or examples unless a user message confirms they happened. For temporal ordering, prefer direct dated user evidence over vague incidental recency mentions, deduplicate repeated mentions of the same event, and include user-confirmed outings such as day hikes when the query asks about trips.",
  {
    query: z.string().describe("Natural language search query"),
    k: z.number().int().min(1).max(50).default(8).describe("Number of results to consider"),
    hybrid: z.boolean().default(true).describe("Use hybrid BM25+vector search"),
    threshold: z.number().min(0).max(1).optional().describe("Minimum similarity score (0-1)"),
    source_prefix: z.string().optional().describe("Filter by source prefix"),
    feedback_weight: z.number().min(0).max(1).default(0.1).describe("Weight for feedback-based ranking signal"),
    confidence_weight: z.number().min(0).max(1).default(0).describe("Weight for confidence-based ranking"),
    graph_weight: z.number().min(0).max(1).default(0.1).describe("Weight for graph-based link expansion"),
    since: z.string().optional().describe("Filter memories at or after this ISO date"),
    until: z.string().optional().describe("Filter memories at or before this ISO date"),
    reference_date: z.string().optional().describe("Reference date for relative temporal queries such as today, yesterday, or past three months"),
    include_archived: z.boolean().default(false).describe("Include archived/superseded memories"),
  },
  async ({ query, k = 8, hybrid = true, threshold, source_prefix, feedback_weight, confidence_weight, graph_weight, since, until, reference_date, include_archived }) => {
    const body = { query, k, hybrid };
    if (threshold !== undefined) body.threshold = threshold;
    if (source_prefix) body.source_prefix = source_prefix;
    if (feedback_weight !== undefined) body.feedback_weight = feedback_weight;
    if (confidence_weight !== undefined && confidence_weight > 0) body.confidence_weight = confidence_weight;
    if (graph_weight !== undefined) body.graph_weight = graph_weight;
    if (since) body.since = since;
    if (until) body.until = until;
    if (reference_date) body.reference_date = reference_date;
    if (include_archived) body.include_archived = true;

    const data = await memoriesRequest("/search/evidence", {
      method: "POST",
      body: JSON.stringify(body),
    }, "search");

    const packet = data.evidence_packet || {};
    const lines = [];
    lines.push(`Evidence packet for "${query}"`);
    lines.push(`Confidence: ${packet.confidence?.level || "unknown"}`);
    for (const reason of packet.confidence?.reasons || []) {
      lines.push(`- ${reason}`);
    }

    if (packet.current_answer) {
      const current = packet.current_answer;
      lines.push("");
      lines.push("Current candidate:");
      lines.push(`[${current.id}] ${current.source} ${current.date || ""}`);
      lines.push(current.text || "");
    } else {
      lines.push("");
      lines.push("Current candidate: none");
    }

    const olderEvidence = packet.older_evidence || packet.older_conflicting_memories || [];
    if (olderEvidence.length) {
      lines.push("");
      lines.push("Older evidence:");
      for (const item of olderEvidence) {
        lines.push(`[${item.id}] ${item.source} ${item.date || ""}`);
        lines.push(item.text || "");
      }
    }

    if (packet.source_date_trail?.length) {
      lines.push("");
      lines.push("Source/date trail:");
      for (const item of packet.source_date_trail) {
        lines.push(`[${item.relation || "memory"}] ${item.source} ${item.date || ""}`);
      }
    }

    if (packet.follow_up_queries?.length) {
      lines.push("");
      lines.push("Follow-up queries:");
      for (const q of packet.follow_up_queries) lines.push(`- ${q}`);
    }

    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

server.tool(
  "memory_add",
  "Store a new memory. Memories persist across sessions and are searchable by meaning. Use for decisions, patterns, learnings, bug fixes, preferences.",
  {
    text: z.string().min(1).describe("The memory content to store"),
    source: z.string().min(1).describe("Source identifier (e.g. 'project/decisions.md', 'bug-fix/redis')"),
    deduplicate: z.boolean().default(true).describe("Skip if a very similar memory already exists"),
    document_at: z.string().optional().describe("ISO 8601 date for when the content was created (e.g. session date). Enables temporal search."),
  },
  async ({ text, source, deduplicate = true, document_at }) => {
    const body = { text, source, deduplicate };
    if (document_at) body.metadata = { document_at };
    const data = await memoriesRequest("/memory/add", {
      method: "POST",
      body: JSON.stringify(body),
    }, "add");

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
    const data = await memoriesRequest(`/memory/${id}`, { method: "DELETE" }, "manage");
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
    await memoriesRequest("/snapshots", { method: "POST" }, "manage");

    const data = await memoriesRequest("/memory/delete-batch", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }, "manage");
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

    const data = await memoriesRequest(url, {}, "manage");

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
    await memoriesRequest("/snapshots", { method: "POST" }, "manage");

    const data = await memoriesRequest(`/memories?source=${encodeURIComponent(source)}`, {
      method: "DELETE",
    }, "manage");
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

    const data = await memoriesRequest(url, {}, "manage");
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
    const data = await memoriesRequest("/stats", {}, "manage");
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
    }, "manage");

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
    }, "feedback");
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
    const data = await memoriesRequest("/memory/conflicts", {}, "manage");

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
    document_at: z.string().optional().describe("ISO 8601 date for when the conversation happened. All extracted memories inherit this timestamp."),
  },
  async ({ messages, source, context = "stop", document_at }) => {
    // Submit extraction job
    const body = { messages, source, context };
    if (document_at) body.document_at = document_at;
    const submitData = await memoriesRequest("/memory/extract", {
      method: "POST",
      body: JSON.stringify(body),
    }, "extract");

    const jobId = submitData.job_id;

    // Poll until terminal state (exponential backoff: 200ms → 2s, 30s timeout)
    let delay = 200;
    const maxDelay = 2000;
    const deadline = Date.now() + 30_000;
    let jobState;

    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, delay));
      jobState = await memoriesRequest(`/memory/extract/${jobId}`, {}, "extract");
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
        else if (a.action === "fallback_add") lines.push(`  + FALLBACK_ADD: ${a.text}`);
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
    }, "add");
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
    }, "search");
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
