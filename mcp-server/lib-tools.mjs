/**
 * Memories MCP tool registrations — shared factory
 *
 * buildServer({ url, apiKey, client, fetchImpl }) constructs a configured
 * McpServer with every memory_* tool registered. Extracted from index.js so
 * a future HTTP transport can reuse the exact same tools as the stdio
 * server. Backend base URL / API key / client tag come from ctx args
 * instead of module-level env reads; fetchImpl is optional (defaults to
 * global fetch) so tests can inject a stub.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { execFileSync } from "node:child_process";
import fs from "fs";
import path from "path";
import yaml from "js-yaml";
import { fileURLToPath } from "node:url";

// Default server version: read from package.json next to this module (same
// realpath-independent pattern as remote/server.mjs's PKG_VERSION) rather
// than hardcoding a string here that inevitably drifts from the real release
// version. Callers can still override via ctx.version.
const here = path.dirname(fileURLToPath(import.meta.url));
const PKG_VERSION = JSON.parse(fs.readFileSync(path.join(here, "package.json"), "utf8")).version;

export const MEMORIES_MCP_INSTRUCTIONS = [
  "Search exact project-scoped sources before broad search: use codex/{project}, claude-code/{project}, learning/{project}, and wip/{project} first. Hook candidates are pointers, not a substitute for active search; when a candidate names a source_prefix, search that exact prefix.",
  "Treat user-authored facts as evidence. Assistant text is context unless it clearly records a user-confirmed fact. Use memory_add for one clear fact after memory_is_novel; use memory_extract for rich conversations or changed decisions, and always provide a non-empty source for scoped extraction.",
  "Use memory_delete only for an explicit forget/delete request. Search or list first to verify the target ID, and do not remove unrelated sources.",
].join("\n\n");

// -- Pure helpers (no ctx/env dependency) ------------------------------------

function memoryId(memory) {
  return memory.id ?? memory.memory_id ?? "unknown";
}

function memoryDate(memory) {
  // Content chronology must not be rewritten by a pin/archive/metadata touch.
  // updated_at is only a last-resort compatibility fallback for records that
  // have no content-bearing date at all.
  return memory.document_at || memory.date || memory.created_at || memory.timestamp || memory.updated_at || "";
}

function evidenceScore(memory) {
  const value = memory?.similarity ?? memory?.rrf_score ?? 0;
  const score = Number(value);
  return Number.isFinite(score) ? score : 0;
}

function evidenceCompact(memory, relation) {
  return {
    id: memory?.id,
    source: memory?.source || "",
    date: memoryDate(memory || {}),
    text: memory?.text || "",
    relation,
    score: evidenceScore(memory),
    is_latest: Boolean(memory?.is_latest),
    archived: Boolean(memory?.archived),
  };
}

function evidenceFollowUps(query) {
  const clean = String(query || "").trim().replace(/\s+/g, " ");
  if (!clean) return [];
  const candidates = [clean];
  if (!/^latest\s/i.test(clean)) candidates.push(`latest ${clean}`);
  if (!/^current\s/i.test(clean)) candidates.push(`current ${clean}`);
  if (!/^what changed\b/i.test(clean)) candidates.push(`what changed about ${clean}`);
  return [...new Map(candidates.map((item) => [item.toLowerCase(), item])).values()];
}

function buildEvidencePacket(query, results) {
  if (!(results || []).length) {
    return {
      current_answer: null,
      supporting_memories: [],
      older_evidence: [],
      older_conflicting_memories: [],
      source_date_trail: [],
      confidence: { level: "missing", reasons: ["No memories were retrieved for this query."] },
      follow_up_queries: evidenceFollowUps(query),
    };
  }
  const preferRecency = /\b(latest|current|now|recent|changed|newest|today|yesterday)\b/i.test(query);
  const rank = (memory) => {
    const time = chronologicalValue(memory);
    const dated = Number.isFinite(time) ? 1 : 0;
    return preferRecency
      ? [dated, time, evidenceScore(memory), memory?.is_latest ? 1 : 0]
      : [evidenceScore(memory), dated, time, memory?.is_latest ? 1 : 0];
  };
  const ranked = [...results].sort((a, b) => {
    const left = rank(a);
    const right = rank(b);
    for (let i = 0; i < left.length; i += 1) {
      if (left[i] !== right[i]) return right[i] - left[i];
    }
    return 0;
  });
  const current = ranked[0];
  const currentTime = chronologicalValue(current);
  const supporting = [];
  const older = [];
  for (const memory of ranked.slice(1)) {
    const time = chronologicalValue(memory);
    if (Number.isFinite(currentTime) && Number.isFinite(time) && time < currentTime) {
      older.push(evidenceCompact(memory, "older"));
    } else if (memory.archived) {
      older.push(evidenceCompact(memory, "archived"));
    } else if (!Number.isFinite(currentTime) && Number.isFinite(time)) {
      older.push(evidenceCompact(memory, "dated_unranked"));
    } else {
      supporting.push(evidenceCompact(memory, "supporting"));
    }
  }
  const reasons = [
    memoryDate(current) ? "Current candidate has a source date." : "Current candidate has no source date.",
  ];
  if (older.length) reasons.push("Packet includes older evidence or separately dated evidence that may be superseded.");
  if (current.is_latest) reasons.push("Current candidate is explicitly marked is_latest.");
  const level = !memoryDate(current) ? "low" : older.length ? "medium" : "high";
  const currentCompact = evidenceCompact(current, "current");
  const trail = [currentCompact, ...supporting, ...older];
  return {
    current_answer: currentCompact,
    supporting_memories: supporting.slice(0, 5),
    older_evidence: older.slice(0, 5),
    older_conflicting_memories: older.slice(0, 5),
    source_date_trail: trail.slice(0, 10),
    confidence: { level, reasons },
    follow_up_queries: evidenceFollowUps(query),
  };
}

// Render a legible relevance tag for one search result.
// - Vector-only results carry `similarity` (cosine, 0-1) — absolute % as before.
// - Hybrid results carry `rrf_score` (Reciprocal Rank Fusion: weight * 1/(rank+60)
//   per signal, bounded near 1/60) — absolute % renders as 0-2% noise, so use
//   the backend's set-relative `relative_score` (1.0 = top of this result set).
// - Legacy backends without relative_score: omit the tag rather than show noise.
function relevanceTag(r) {
  if (typeof r.similarity === "number") return ` (${(r.similarity * 100).toFixed(0)}%)`;
  if (typeof r.relative_score === "number") return ` (rel ${(r.relative_score * 100).toFixed(0)}%)`;
  return "";
}

function usesRelativeScores(results) {
  return (results || []).some(
    (r) => typeof r.similarity !== "number" && typeof r.relative_score === "number"
  );
}

const REL_LEGEND = "rel % = relevance relative to the top result of this search, not an absolute match score";

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
  const variants = [
    clean,
    `${clean} user confirmed dated event evidence completed happened`,
  ];
  if (/\b(trip|trips|travel|vacation|visited|went|outing|hike|hikes)\b/i.test(clean)) {
    variants.push(`${clean} trip travel vacation day hike outing excursion just got back returned`);
  }
  return [...new Set(variants.filter(Boolean))];
}

// -- Collaborative project context ------------------------------------------

// Keep the client-side declaration deliberately smaller than the server's
// namespace policy.  A repository can opt in to the shared namespace, but it
// cannot grant itself access or choose a principal; both are resolved below
// from the authenticated backend.
const PROJECT_SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;
const PROJECT_DECLARATION_KEYS = new Set(["project_id", "shared_memory"]);
const PROJECT_SOURCE_RE = /^project\/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\/(decisions|knowledge|state|operations)$/;

function projectFailure(reason, diagnostic) {
  return { ok: false, reason, diagnostic };
}

/**
 * Parse the strict `.memories/project.yaml` declaration.
 *
 * This is intentionally a pure helper so hooks and future MCP tool routing
 * can share the same contract.  Only the two currently implemented fields are
 * accepted; an apparently useful future field must not silently activate a
 * mode whose behavior is not implemented yet.
 */
export function parseProjectDeclaration(source) {
  let value;
  try {
    value = yaml.load(String(source ?? ""));
  } catch (error) {
    return projectFailure("malformed", `project declaration is not valid YAML: ${error.message}`);
  }

  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return projectFailure("malformed", "project declaration must be a YAML mapping");
  }

  const keys = Object.keys(value);
  const unknown = keys.filter((key) => !PROJECT_DECLARATION_KEYS.has(key));
  if (unknown.length) {
    return projectFailure("unknown_field", `unknown project declaration field: ${unknown.sort().join(", ")}`);
  }
  const missing = [...PROJECT_DECLARATION_KEYS].filter((key) => !Object.prototype.hasOwnProperty.call(value, key));
  if (missing.length) {
    return projectFailure("missing_field", `missing project declaration field: ${missing.sort().join(", ")}`);
  }
  if (typeof value.project_id !== "string" || !PROJECT_SLUG_RE.test(value.project_id)) {
    return projectFailure("invalid_project_id", "project_id must be a lowercase path-safe slug");
  }
  if (value.shared_memory !== true) {
    return projectFailure("shared_memory_not_true", "shared_memory must be the YAML boolean true");
  }

  return { ok: true, projectId: value.project_id, sharedMemory: true };
}

/**
 * Resolve a checkout's main repository root.  `git rev-parse
 * --git-common-dir` points worktrees back at the main repository's `.git`, so
 * the declaration remains shared by the main checkout and every worktree.
 */
export function resolveProjectRoot(cwd = process.cwd()) {
  const candidate = path.resolve(String(cwd || process.cwd()));
  try {
    const common = execFileSync("git", ["-C", candidate, "rev-parse", "--git-common-dir"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (common) {
      const commonPath = path.isAbsolute(common) ? common : path.resolve(candidate, common);
      if (path.basename(commonPath) === ".git") {
        const root = path.dirname(commonPath);
        if (root && root !== path.parse(root).root) return root;
      }
    }
  } catch {
    // A non-git checkout keeps the current directory boundary.  Missing git
    // metadata must never make an unrelated parent declaration authoritative.
  }
  return candidate;
}

export function projectDeclarationPath(cwd = process.cwd()) {
  const root = resolveProjectRoot(cwd);
  const declaration = path.join(root, ".memories", "project.yaml");
  return fs.existsSync(declaration) ? declaration : null;
}

/** Load and strictly parse the declaration at a checkout boundary. */
export function loadProjectDeclaration(options = {}) {
  const cwd = typeof options === "string" ? options : options?.cwd || process.cwd();
  const declaration = projectDeclarationPath(cwd);
  if (!declaration) return projectFailure("missing", "no .memories/project.yaml at the repository boundary");
  try {
    return parseProjectDeclaration(fs.readFileSync(declaration, "utf8"));
  } catch (error) {
    return projectFailure("unreadable", `cannot read project declaration: ${error.message}`);
  }
}

function interpolateConfigValue(value) {
  const text = value || "";
  const match = text.match(/\$\{(\w+)\}/);
  return match ? (process.env[match[1]] || text) : text;
}

/**
 * Load backend configuration using the same precedence as buildServer's
 * legacy routing.  Keeping this as a helper lets project activation count the
 * configured backends without changing fan-out or scenario routing itself.
 */
export function loadBackendConfig({ cwd = process.cwd(), url, apiKey, skipFileConfig = false } = {}) {
  if (skipFileConfig) {
    return {
      backends: [{ name: "default", url: url || "http://localhost:8900", apiKey: apiKey || "", scenario: "" }],
      routing: {},
      configOrigin: "options",
      configured: url !== undefined || apiKey !== undefined,
    };
  }

  const explicitFile = process.env.MEMORIES_BACKENDS_FILE;
  const configPaths = explicitFile
    ? [explicitFile]
    : [
        path.join(cwd, ".memories", "backends.yaml"),
        path.join(process.env.HOME || "", ".config", "memories", "backends.yaml"),
      ];

  for (const configPath of configPaths) {
    if (!fs.existsSync(configPath)) continue;
    const raw = yaml.load(fs.readFileSync(configPath, "utf8"));
    const backends = Object.entries(raw.backends || {}).map(([name, cfg]) => ({
      name,
      url: interpolateConfigValue(cfg.url || ""),
      apiKey: interpolateConfigValue(cfg.api_key || ""),
      scenario: cfg.scenario || "",
    }));
    return { backends, routing: raw.routing || {}, configOrigin: configPath, configured: true };
  }

  return {
    backends: [{ name: "default", url: url || "http://localhost:8900", apiKey: apiKey || "", scenario: "" }],
    routing: {},
    configOrigin: url !== undefined || apiKey !== undefined ? "options" : "fallback",
    configured: url !== undefined || apiKey !== undefined,
  };
}

function strictBackendConfigFailure(reason, diagnostic) {
  return { backends: [], routing: {}, error: { reason, diagnostic } };
}

/**
 * Load only an explicitly configured backend set for collaborative mode.
 * Unlike legacy routing, this view never invents localhost when no config is
 * present: a repository declaration must not cause an unauthenticated probe.
 */
function loadStrictBackendConfig({ cwd = process.cwd(), url, apiKey, skipFileConfig = false } = {}) {
  if (skipFileConfig) {
    if (url === undefined && apiKey === undefined) {
      return strictBackendConfigFailure("no_backends", "no backend configuration is available");
    }
    return {
      backends: [{ name: "default", url: url || "", apiKey: apiKey || "", scenario: "" }],
      routing: {},
    };
  }

  const explicitFile = process.env.MEMORIES_BACKENDS_FILE;
  const configPaths = explicitFile
    ? [explicitFile]
    : [
        path.join(cwd, ".memories", "backends.yaml"),
        path.join(process.env.HOME || "", ".config", "memories", "backends.yaml"),
      ];
  const configPath = configPaths.find((candidate) => fs.existsSync(candidate));
  if (!configPath) {
    if (explicitFile) {
      return strictBackendConfigFailure("no_backends", "no backend configuration is available");
    }
    if (url !== undefined || apiKey !== undefined) {
      return {
        backends: [{ name: "default", url: url || "", apiKey: apiKey || "", scenario: "" }],
        routing: {},
      };
    }
    return strictBackendConfigFailure("no_backends", "no backend configuration is available");
  }

  let raw;
  try {
    raw = yaml.load(fs.readFileSync(configPath, "utf8"));
  } catch (error) {
    return strictBackendConfigFailure("backend_config_invalid", `backend configuration is not valid YAML: ${error.message}`);
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return strictBackendConfigFailure("backend_config_invalid", "backend configuration must be a YAML mapping");
  }
  if (!Object.prototype.hasOwnProperty.call(raw, "backends")) {
    return strictBackendConfigFailure("no_backends", "backend configuration does not define any backends");
  }
  if (!raw.backends || typeof raw.backends !== "object" || Array.isArray(raw.backends)) {
    return strictBackendConfigFailure("backend_config_invalid", "backend configuration backends must be a YAML mapping");
  }
  try {
    const backends = Object.entries(raw.backends).map(([name, cfg]) => {
      if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) {
        throw new Error(`backend ${name} must be a YAML mapping`);
      }
      return {
        name,
        url: interpolateConfigValue(cfg.url || ""),
        apiKey: interpolateConfigValue(cfg.api_key || ""),
        scenario: cfg.scenario || "",
      };
    });
    return { backends, routing: raw.routing || {} };
  } catch (error) {
    return strictBackendConfigFailure("backend_config_invalid", `backend configuration is invalid: ${error.message}`);
  }
}

function contextFailure(reason, diagnostic) {
  return {
    active: false,
    reason,
    diagnostic,
  };
}

/**
 * Keep legacy continuity narrow when collaborative mode is active.  The
 * authenticated key's prefixes are the authorization source; repository
 * configuration never grants a new prefix.  A legacy prefix is eligible only
 * when it names this project exactly in its final path segment.  Project and
 * person namespaces are routed explicitly before this list and must never be
 * treated as legacy continuity.  Family/wildcard prefixes are intentionally
 * excluded because they would widen recall beyond this project.
 */
export function deriveLegacyProjectPrefixes(projectId, authorizedPrefixes) {
  if (!Array.isArray(authorizedPrefixes)) return [];
  const result = [];
  const seen = new Set();
  for (const raw of authorizedPrefixes) {
    if (typeof raw !== "string") continue;
    const original = raw.trim();
    if (!original) continue;
    const prefix = original.replaceAll("{project}", projectId);
    if (!prefix || prefix.includes("*") || prefix.endsWith("/")) continue;
    const segments = prefix.split("/");
    if (segments.length < 2 || segments.at(-1) !== projectId) continue;
    if (segments[0] === "project" || segments[0] === "person") continue;
    if (seen.has(prefix)) continue;
    seen.add(prefix);
    result.push(prefix);
  }
  return result;
}

/**
 * Resolve collaborative mode for a checkout and one authenticated backend.
 * The `/api/keys/me` request is deliberately last: invalid declarations and
 * ambiguous backend sets must not probe a host or accidentally turn a
 * repository declaration into authorization.
 */
export async function resolveProjectContext(options = {}) {
  const {
    cwd = process.cwd(),
    url,
    apiKey,
    backends: suppliedBackends,
    backendConfig: suppliedBackendConfig,
    fetchImpl = globalThis.fetch,
    skipFileConfig = false,
    principalTimeoutMs = 2000,
    declaration: suppliedDeclaration,
  } = options;
  const declaration = suppliedDeclaration || loadProjectDeclaration(cwd);
  if (!declaration.ok) return contextFailure(declaration.reason, declaration.diagnostic);

  const config = suppliedBackendConfig
    || (suppliedBackends
    ? { backends: suppliedBackends }
    : loadStrictBackendConfig({ cwd: resolveProjectRoot(cwd), url, apiKey, skipFileConfig }));
  if (config.error) return contextFailure(config.error.reason, config.error.diagnostic);
  if (config.configured === false) {
    return contextFailure("no_backends", "no backend configuration is available");
  }
  const backends = Array.isArray(config.backends) ? config.backends : [];
  if (backends.length !== 1) {
    return contextFailure(
      backends.length === 0 ? "no_backends" : "multiple_backends",
      `collaborative project mode requires exactly one configured backend (found ${backends.length})`,
    );
  }

  const backend = backends[0] || {};
  const backendUrl = String(backend.url || "").replace(/\/+$/, "");
  if (!backendUrl || typeof fetchImpl !== "function") {
    return contextFailure("principal_unreachable", "the configured backend cannot be reached");
  }

  const headers = {};
  const backendKey = backend.apiKey ?? backend.api_key ?? "";
  if (backendKey) headers["X-API-Key"] = backendKey;
  let response;
  let identity;
  let lookupStage = "request";
  const timeoutMs = Number.isFinite(principalTimeoutMs) && principalTimeoutMs > 0
    ? principalTimeoutMs
    : 2000;
  const controller = new AbortController();
  let timeout;
  const deadline = new Promise((_resolve, reject) => {
    timeout = setTimeout(() => {
      controller.abort();
      reject(new Error("principal lookup timeout"));
    }, timeoutMs);
  });
  try {
    response = await Promise.race([
      fetchImpl(`${backendUrl}/api/keys/me`, {
        method: "GET",
        headers,
        redirect: "manual",
        signal: controller.signal,
      }),
      deadline,
    ]);
    if (!response?.ok || [301, 302, 303, 307, 308].includes(response?.status)) {
      return contextFailure("principal_unreachable", `authenticated principal lookup returned HTTP ${response?.status ?? "unknown"}`);
    }
    lookupStage = "body";
    identity = await Promise.race([response.json(), deadline]);
  } catch (error) {
    if (controller.signal.aborted) {
      return contextFailure(
        "principal_unreachable",
        `authenticated principal lookup timed out after ${timeoutMs}ms`,
      );
    }
    if (lookupStage === "body") {
      return contextFailure("principal_unreachable", `authenticated principal lookup returned invalid JSON: ${error.message}`);
    }
    return contextFailure("principal_unreachable", `authenticated principal lookup failed: ${error.message}`);
  } finally {
    clearTimeout(timeout);
  }
  if (identity?.type !== "managed") {
    const reason = identity?.type === "env" || identity?.type === "none" ? "env_principal" : "invalid_principal_type";
    return contextFailure(reason, "authenticated principal lookup did not return a managed principal");
  }
  const principalId = identity?.principal_id;
  if (!principalId) {
    return contextFailure("missing_principal", "authenticated backend response did not include principal_id");
  }
  if (typeof principalId !== "string" || !PROJECT_SLUG_RE.test(principalId)) {
    return contextFailure("invalid_principal", "authenticated principal_id must be a lowercase path-safe slug");
  }

  const prefixes = identity?.prefixes === undefined || identity?.prefixes === null
    ? []
    : identity.prefixes;
  if (!Array.isArray(prefixes) || prefixes.some((prefix) => typeof prefix !== "string")) {
    return contextFailure("invalid_prefixes", "authenticated principal lookup returned invalid source prefixes");
  }
  const legacySourcePrefixes = deriveLegacyProjectPrefixes(declaration.projectId, prefixes);

  return {
    active: true,
    reason: "active",
    projectId: declaration.projectId,
    project_id: declaration.projectId,
    principalId,
    principal_id: principalId,
    sharedMemory: true,
    shared_memory: true,
    backend: backend.name || "default",
    backendName: backend.name || "default",
    backendUrl,
    backendConfigOrigin: config.configOrigin || null,
    prefixes: [...prefixes],
    legacySourcePrefixes,
    legacy_source_prefixes: [...legacySourcePrefixes],
  };
}

// -- Server factory -----------------------------------------------------------

export function buildServer({ url, apiKey, client, fetchImpl, skipFileConfig = false, version, cwd = process.cwd() } = {}) {
  const fetchFn = fetchImpl || fetch;
  const projectDeclaration = loadProjectDeclaration(cwd);
  const collaborativeProjectPresent = projectDeclaration.reason !== "missing";

  // -- Config Loading ----------------------------------------------------------

  function loadBackends() {
    return loadBackendConfig({ cwd, url, apiKey, skipFileConfig });
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

  async function backendFetch(b, reqPath, options = {}) {
    const fullUrl = `${b.url}${reqPath}`;
    const headers = { "Content-Type": "application/json" };
    if (b.apiKey) headers["X-API-Key"] = b.apiKey;
    const clientTag = client || "mcp";
    const sessionId = process.env.MEMORIES_SESSION_ID || process.env.CODEX_THREAD_ID || "";
    const invocation = process.env.MEMORIES_INVOCATION || "mcp";
    headers["X-Memories-Client"] = clientTag;
    headers["X-Memories-Invocation"] = invocation;
    if (sessionId) headers["X-Memories-Session-Id"] = sessionId;
    // redirect:"manual" — following a 301/302 re-issues POST as GET (fetch spec),
    // which the POST-only backend rejects with an opaque 405. Fail loudly instead.
    const response = await fetchFn(fullUrl, { ...options, headers: { ...headers, ...options.headers }, redirect: "manual" });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location") || "unknown";
      throw new Error(
        `Memories API error: ${fullUrl} redirects (${response.status} -> ${location}). ` +
        `Refusing to follow: a redirect silently downgrades POST to GET. ` +
        `Point this backend's URL (MEMORIES_URL or backends.yaml) at the redirect target.`
      );
    }
    return response;
  }

  async function memoriesRequest(reqPath, options = {}, op = "search") {
    const backends = getBackendsForOp(op);

    if (backends.length === 1) {
      // Single backend — direct call (backward compat)
      const response = await backendFetch(backends[0], reqPath, options);
      if (!response.ok) {
        const body = await response.text();
        throw new Error(`Memories API error ${response.status}: ${body}`);
      }
      return response.json();
    }

    // Multi-backend — parallel fan-out
    const results = await Promise.allSettled(
      backends.map(async (b) => {
        const response = await backendFetch(b, reqPath, options);
        if (!response.ok) throw new Error(`${b.name}: HTTP ${response.status}`);
        const data = await response.json();
        return { backend: b.name, data };
      })
    );

    // Collect successful results
    const successes = results.filter(r => r.status === "fulfilled").map(r => r.value);
    if (successes.length === 0) {
      const reasons = results
        .filter(r => r.status === "rejected")
        .map(r => r.reason?.message || String(r.reason));
      throw new Error(`All backends failed: ${reasons.join("; ")}`);
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

  function projectProvenanceLabel(result, projectId) {
    if (!result || typeof result !== "object") return "";
    const source = String(result.source || "");
    if (!source.startsWith(`project/${projectId}/`)) return "";
    const clean = (value) => String(value)
      .replace(/[\u0000-\u001f\u007f]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80);
    const labels = [];
    if (result.author !== undefined && result.author !== null && String(result.author) !== "") {
      labels.push(`author=${clean(result.author)}`);
    }
    if (result.origin_client !== undefined && result.origin_client !== null && String(result.origin_client) !== "") {
      labels.push(`origin-client=${clean(result.origin_client)}`);
    }
    return labels.length ? `[${labels.join(", ")}]` : "";
  }

  async function projectSearchRequest(body, projectContext) {
    const prefixes = [
      `project/${projectContext.projectId}`,
      `person/${projectContext.principalId}/${projectContext.projectId}`,
      ...(projectContext.legacySourcePrefixes || []),
    ];
    const responses = await Promise.all(prefixes.map(async (prefix) => {
      const scopedBody = { ...body, source_prefix: prefix, source_boundary: true };
      const data = await memoriesRequest("/search", {
        method: "POST",
        body: JSON.stringify(scopedBody),
      }, "search");
      const results = (data.results || []).filter((result) => {
        const source = String(result?.source || "");
        return source === prefix || source.startsWith(`${prefix}/`);
      });
      return { ...data, results, count: results.length };
    }));

    const seen = new Set();
    const results = [];
    for (const data of responses) {
      for (const result of data.results || []) {
        const key = result?.id !== undefined && result?.id !== null
          ? `id:${result.id}:source:${result.source || ""}`
          : `text:${result?.text || ""}:source:${result?.source || ""}`;
        if (seen.has(key)) continue;
        seen.add(key);
        results.push(result);
      }
    }
    const capped = results.slice(0, body.k);
    return { results: capped, count: capped.length };
  }

  // -- Server ------------------------------------------------------------------

  const server = new McpServer({
    name: "memories",
    version: version || PKG_VERSION,
  }, {
    instructions: MEMORIES_MCP_INSTRUCTIONS,
  });

  // Expose a lazy, memoized context lookup for project-aware tool behavior.
  // Keeping the lookup lazy means legacy callers never incur a `/api/keys/me`
  // request.  Pass the already-loaded config object so project mode and normal
  // routing cannot resolve different worktree/main-repository backends.
  let projectContextPromise;
  server.resolveProjectContext = async () => {
    if (!projectContextPromise) {
      projectContextPromise = resolveProjectContext({
        cwd,
        url,
        apiKey,
        backendConfig: config,
        fetchImpl: fetchFn,
        skipFileConfig,
        declaration: projectDeclaration,
      });
    }
    const pending = projectContextPromise;
    try {
      const context = await pending;
      // A missing/invalid declaration is fixed for this server lifetime (the
      // declaration was also loaded once above).  Retry inactive resolution
      // only for a checkout that actually declared collaborative mode, where
      // backend identity or configuration can recover independently.
      if (!context.active && collaborativeProjectPresent && projectContextPromise === pending) {
        projectContextPromise = undefined;
      }
      return context;
    } catch (error) {
      if (projectContextPromise === pending) projectContextPromise = undefined;
      throw error;
    }
  };

  function unavailableProjectContextResult(context) {
    if (!collaborativeProjectPresent || context.active) return null;
    return {
      content: [{
        type: "text",
        text: `Collaborative project memory is unavailable until authenticated project identity resolves: ${context.diagnostic || context.reason || "unknown error"}`,
      }],
      isError: true,
    };
  }

  async function projectWriteUnavailable(source) {
    if (!String(source || "").startsWith("project/") || !collaborativeProjectPresent) {
      return null;
    }
    const projectContext = await server.resolveProjectContext();
    const unavailable = unavailableProjectContextResult(projectContext);
    if (unavailable) return unavailable;
    if (!source.startsWith(`project/${projectContext.projectId}/`)) {
      return {
        content: [{
          type: "text",
          text: `Project memory source must target the declared project: project/${projectContext.projectId}/<kind>`,
        }],
        isError: true,
      };
    }
    return null;
  }

  async function memoriesAddRequest(reqPath, body) {
    const unavailable = await projectWriteUnavailable(body?.source);
    if (unavailable) return { unavailable };
    const data = await memoriesRequest(reqPath, {
      method: "POST",
      body: JSON.stringify(body),
    }, "add");
    return { data };
  }

  // -- Tools -------------------------------------------------------------------

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

      const projectContext = await server.resolveProjectContext();
      const unavailable = unavailableProjectContextResult(projectContext);
      if (unavailable) return unavailable;
      const data = projectContext.active && source_prefix === undefined
        ? await projectSearchRequest(body, projectContext)
        : await memoriesRequest("/search", {
          method: "POST",
          body: JSON.stringify(body),
        }, "search");

      if (data.count === 0) {
        return { content: [{ type: "text", text: `No memories found for: "${query}"` }] };
      }

      const legend = usesRelativeScores(data.results) ? ` (${REL_LEGEND})` : "";

      if (compact) {
        const lines = data.results.map((r, i) => {
          const id = memoryId(r);
          const date = memoryDate(r);
          const dateText = date ? ` ${date}` : "";
          const provenance = projectContext.active ? projectProvenanceLabel(r, projectContext.projectId) : "";
          const source = `${provenance ? `${provenance} ` : ""}${r.source || "unknown-source"}`;
          return `[${i + 1}] id=${id}${relevanceTag(r)} ${source}${dateText}\n${snippet(r.text)}\nUse memory_get id=${id} for full text.`;
        });

        return {
          content: [{
            type: "text",
            text: `Found ${data.count} compact memories for "${query}"${legend}:\n\n${lines.join("\n\n")}`,
          }],
        };
      }

      const lines = data.results.map((r, i) => {
        const d = memoryDate(r);
        const dateTag = d ? ` [${String(d).slice(0, 10)}]` : "";
        const provenance = projectContext.active ? projectProvenanceLabel(r, projectContext.projectId) : "";
        const source = `${provenance ? `${provenance} ` : ""}${r.source || "unknown-source"}`;
        return `[${i + 1}] id=${memoryId(r)}${relevanceTag(r)}${dateTag} ${source}\n${r.text}`;
      });

      return {
        content: [{
          type: "text",
          text: `Found ${data.count} memories for "${query}"${legend}:\n\n${lines.join("\n\n---\n\n")}`,
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
      const projectContext = await server.resolveProjectContext();
      const unavailable = unavailableProjectContextResult(projectContext);
      if (unavailable) return unavailable;
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

        const data = projectContext.active && source_prefix === undefined
          ? await projectSearchRequest(body, projectContext)
          : await memoriesRequest("/search", {
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
        let pct = "";
        if (typeof r.similarity === "number") pct = ` score=${(r.similarity * 100).toFixed(0)}%`;
        else if (typeof r.relative_score === "number") pct = ` rel=${(r.relative_score * 100).toFixed(0)}%`;
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

      const projectContext = await server.resolveProjectContext();
      const unavailable = unavailableProjectContextResult(projectContext);
      if (unavailable) return unavailable;
      let data;
      if (projectContext.active && source_prefix === undefined) {
        const scoped = await projectSearchRequest(body, projectContext);
        data = { evidence_packet: buildEvidencePacket(query, scoped.results || []) };
      } else {
        data = await memoriesRequest("/search/evidence", {
          method: "POST",
          body: JSON.stringify(body),
        }, "search");
      }

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
    "Store a new memory. Memories persist across sessions and are searchable by meaning. Use for decisions, patterns, learnings, bug fixes, preferences. For a deliberate collaborative project write, use this existing memory_add tool exactly once with source project/<project>/<kind>, where kind must be exactly decisions, knowledge, state, or operations; first apply the durable-sharing test (another contributor will need this fact without the current session). ACLs remain server-authoritative.",
    {
      text: z.string().min(1).describe("The memory content to store"),
      source: z.string().min(1).superRefine((value, ctx) => {
        if (value.startsWith("project/") && !PROJECT_SOURCE_RE.test(value)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "project sources must be project/<project>/<decisions|knowledge|state|operations>",
          });
        }
      }).describe("Source identifier (e.g. 'project/shared-demo/decisions', 'bug-fix/redis')"),
      deduplicate: z.boolean().default(true).describe("Legacy flag; ignored when on_duplicate is set"),
      on_duplicate: z.enum(["supersede", "skip", "add"]).default("supersede").describe("supersede (default): a colliding similar memory is replaced — the old version is archived with a supersedes link, so corrections like 'weight is now 79kg' update instead of being dropped as duplicates. skip: keep the existing memory and report which id blocked the write. add: store unconditionally."),
      document_at: z.string().optional().describe("ISO 8601 date for when the content was created (e.g. session date). Enables temporal search."),
    },
    async ({ text, source, deduplicate = true, on_duplicate = "supersede", document_at }) => {
      const body = { text, source, on_duplicate };
      if (document_at) body.metadata = { document_at };
      const addResult = await memoriesAddRequest("/memory/add", body);
      if (addResult.unavailable) return addResult.unavailable;
      const data = addResult.data;

      let msg;
      if (data.action === "superseded") {
        msg = `Memory superseded: id=${data.id} replaces id=${data.superseded} (similarity ${data.similarity}); the old version is archived with a supersedes link.`;
      } else if (data.action === "skipped") {
        msg = `Skipped (${data.reason}): memory id=${data.blocked_by} already covers this (similarity ${data.similarity}). ${data.hint || ""}`.trim();
      } else if (data.action === "added" || data.id !== null) {
        msg = `Memory added (id: ${data.id}) from ${source}`;
      } else {
        msg = data.blocked_by !== undefined
          ? `Duplicate skipped — memory id=${data.blocked_by} already covers this. ${data.hint || ""}`.trim()
          : "Duplicate skipped — a very similar memory already exists.";
      }
      return { content: [{ type: "text", text: msg }] };
    }
  );

  server.tool(
    "memory_update",
    "Update/correct an existing memory by id: the new text REPLACES it. The old version is archived with a supersedes link (recoverable, visible in timelines), so corrections like 'weight changed from 78 to 79kg' keep their history. Use memory_search first to find the id; use this instead of memory_add when you know which memory is outdated.",
    {
      id: z.number().int().min(0).describe("Memory ID to update (the outdated memory)"),
      text: z.string().min(1).describe("The corrected/updated memory content"),
      source: z.string().optional().describe("Source for the replacement; defaults to the original memory's source"),
      document_at: z.string().optional().describe("ISO 8601 date for when the corrected fact became true"),
    },
    async ({ id, text, source, document_at }) => {
      const body = { text };
      if (source) body.source = source;
      if (document_at) body.metadata = { document_at };
      const data = await memoriesRequest(`/memory/${id}/supersede`, {
        method: "POST",
        body: JSON.stringify(body),
      }, "manage");
      return {
        content: [{
          type: "text",
          text: `Memory updated: id=${data.new_id} replaces id=${data.old_id}; the old version is archived with a supersedes link.`,
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
    "List memories that conflict with each other (paginated). Conflicts are flagged during extraction when contradictory facts are detected. Use to review contradictions; memory_supersede or the conflict drain resolves them.",
    {
      limit: z.number().int().min(1).max(200).default(20).describe("Max conflicts to return (default 20)"),
      offset: z.number().int().min(0).default(0).describe("Offset for pagination"),
    },
    async ({ limit = 20, offset = 0 }) => {
      const data = await memoriesRequest(`/memory/conflicts?limit=${limit}&offset=${offset}`, {}, "manage");

      if (!data.conflicts || data.conflicts.length === 0) {
        return { content: [{ type: "text", text: "No conflicts found." }] };
      }

      const lines = data.conflicts.map((c) => {
        const other = c.conflicting_memory;
        const otherText = other ? `${other.text.substring(0, 150)}` : "(deleted)";
        const review = c.needs_review ? ` [needs_review: ${c.needs_review}]` : "";
        return `[${c.id}] "${c.text.substring(0, 150)}" CONFLICTS WITH [${c.conflicts_with}] "${otherText}"${review}`;
      });

      const total = data.total ?? data.count;
      const header = data.has_more
        ? `Showing ${data.count} of ${total} conflict(s) (offset ${data.offset}; pass offset=${data.offset + data.count} for more):`
        : `${total} conflict(s) found:`;
      return { content: [{ type: "text", text: `${header}\n\n${lines.join("\n\n")}` }] };
    }
  );

  server.tool(
    "memory_extract",
    "Extract and store memories from conversation text using LLM-based AUDN (Add/Update/Delete/Noop/Conflict). Costs ~$0.001 per call. Use when decisions change, deferred work completes, or rich conversation contains multiple facts worth remembering. Automatic extraction remains private: in collaborative mode it writes only person/<principal>/<project>/knowledge and never infers project/.... For an intentional shared fact, use memory_add exactly once with one of the four project kinds after applying the durable-sharing test. Returns what was added, updated, deleted, conflicted, or skipped.",
    {
      messages: z.string().min(1).describe("Conversation text to extract memories from"),
      source: z.string().min(1).superRefine((value, ctx) => {
        if (value.startsWith("project/")) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "automatic extraction cannot write project/...; use memory_add exactly once with project/<project>/<decisions|knowledge|state|operations>",
          });
        }
      }).describe("Private extraction source; project/... writes must use memory_add exactly once"),
      context: z.enum(["stop", "pre_compact", "session_end"]).default("stop")
        .describe("Extraction intensity: 'stop' (standard), 'pre_compact' (aggressive), 'session_end'"),
      document_at: z.string().optional().describe("ISO 8601 date for when the conversation happened. All extracted memories inherit this timestamp."),
    },
    async ({ messages, source, context = "stop", document_at }) => {
      const projectContext = await server.resolveProjectContext();
      const unavailable = unavailableProjectContextResult(projectContext);
      if (unavailable) return unavailable;
      const effectiveSource = projectContext.active
        ? `person/${projectContext.principalId}/${projectContext.projectId}/knowledge`
        : source;
      // Submit extraction job
      const body = { messages, source: effectiveSource, context };
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
      source: z.string().min(1).superRefine((value, ctx) => {
        if (value.startsWith("project/") && !PROJECT_SOURCE_RE.test(value)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "project sources must be project/<project>/<decisions|knowledge|state|operations>",
          });
        }
      }).describe("Source identifier"),
      context: z.string().optional().describe("Optional context"),
    },
    async ({ text, source, context }) => {
      const body = { text, source };
      if (context) body.context = context;
      const addResult = await memoriesAddRequest("/memory/missed", body);
      if (addResult.unavailable) return addResult.unavailable;
      const data = addResult.data;
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

  return server;
}
