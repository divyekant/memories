const CLIENT_PREFIXES = ["opencode", "claude-code", "codex", "learning", "wip"];

function basename(value) {
  return String(value || "")
    .split(/[\\/]/)
    .filter(Boolean)
    .pop();
}

function projectName(options = {}) {
  if (typeof options.project === "string" && options.project) return options.project;
  if (options.project && typeof options.project === "object") {
    if (options.project.name) return String(options.project.name);
    if (options.project.worktree) return basename(options.project.worktree);
  }
  if (options.worktree) return basename(options.worktree);
  if (options.directory) return basename(options.directory);
  if (typeof process !== "undefined" && process.cwd) {
    return basename(process.cwd()) || "project";
  }
  return "project";
}

function defaultSourcePrefixes(project) {
  return CLIENT_PREFIXES.map((prefix) => `${prefix}/${project}`);
}

function defaultExtractSource(project) {
  return `opencode/${project}`;
}

function sourcePrefixQuality(sourcePrefix, project) {
  const value = sourcePrefix || "";
  if (CLIENT_PREFIXES.some((prefix) => value === `${prefix}/${project}` || value.startsWith(`${prefix}/${project}/`))) {
    return "exact_project";
  }
  if (value === "" || CLIENT_PREFIXES.some((prefix) => value === prefix || value === `${prefix}/`)) {
    return "broad_or_unscoped";
  }
  return "other";
}

function resultText(result) {
  return result.text || result.content || result.memory || result.summary || "";
}

function renderRecallContext({ project, results = [], activeSearchRequired = false } = {}) {
  const prefixes = defaultSourcePrefixes(project || "project");
  const lines = ["OpenCode Memories Recall Context"];

  if (activeSearchRequired) {
    lines.push(
      "MANDATORY FIRST ACTION: before answering questions about prior decisions, architecture, conventions, deferred work, past bugs, project history, or resumed topics, actively call memory_search with an exact project source_prefix."
    );
  }

  lines.push("Candidate exact project searches:");
  for (const prefix of prefixes) {
    lines.push(`- Call memory_search with source_prefix="${prefix}".`);
  }
  lines.push("memory_get is not a substitute for memory_search; use memory_get only after memory_search returns a specific memory id.");

  if (results.length > 0) {
    lines.push("Recent recalled memories:");
    for (const result of results) {
      const source = result.source ? ` [${result.source}]` : "";
      const id = result.id === undefined ? "" : `#${result.id} `;
      lines.push(`- ${id}${resultText(result)}${source}`);
    }
  }

  return lines.join("\n");
}

function timestamp(options = {}) {
  if (typeof options.now === "function") return options.now();
  return new Date().toISOString();
}

function homeDir(options = {}) {
  if (options.home) return String(options.home);
  const processEnv = typeof process !== "undefined" ? process.env : {};
  return processEnv.HOME || "";
}

function configPath(options = {}, filename) {
  const path = require("node:path");
  return path.join(homeDir(options), ".config", "memories", filename);
}

function readEnvFile(options = {}) {
  if (options.envFile === false) return {};
  const envFile = options.envFile || configPath(options, "env");
  const fs = options.fsImpl || require("node:fs");
  if (!fs.existsSync(envFile)) return {};
  const env = {};
  for (const line of fs.readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$/);
    if (!match) continue;
    env[match[1]] = match[2].replace(/^['"]|['"]$/g, "");
  }
  return env;
}

function metricsDisabled(value) {
  return ["0", "false", "no", "off"].includes(String(value || "").toLowerCase());
}

function activeSearchLogPath(options = {}) {
  if (options.activeSearchLog === false) return "";
  const env = readEnvFile(options);
  const processEnv = typeof process !== "undefined" ? process.env : {};
  if (metricsDisabled(env.MEMORIES_ACTIVE_SEARCH_METRICS || processEnv.MEMORIES_ACTIVE_SEARCH_METRICS)) return "";
  if (typeof options.activeSearchLog === "function") return options.activeSearchLog;
  if (options.activeSearchLog) return options.activeSearchLog;

  return env.MEMORIES_ACTIVE_SEARCH_LOG || processEnv.MEMORIES_ACTIVE_SEARCH_LOG || configPath(options, "active-search.jsonl");
}

function appendTelemetry(record, options = {}) {
  const activeSearchLog = activeSearchLogPath(options);
  if (!activeSearchLog) return;
  if (typeof activeSearchLog === "function") {
    activeSearchLog(record);
    return;
  }

  const fs = options.fsImpl || require("node:fs");
  const path = require("node:path");
  fs.mkdirSync(path.dirname(activeSearchLog), { recursive: true });
  fs.appendFileSync(activeSearchLog, `${JSON.stringify(record)}\n`);
}

function isMemoryToolName(toolName) {
  const name = String(toolName || "");
  return /^memory_/.test(name) || /^mcp__memories__memory_/.test(name) || /^memories[._]memory_/.test(name);
}

async function observeToolCall(toolName, args = {}, sessionID, options = {}) {
  if (!isMemoryToolName(toolName)) return undefined;
  const project = projectName(options);
  const sourcePrefix = args.source_prefix || args.sourcePrefix || "";
  const record = {
    client: "opencode",
    event: "tool_call",
    tool_name: toolName,
    source_prefix: sourcePrefix,
    source_prefix_quality: sourcePrefixQuality(sourcePrefix, project),
    session_id: sessionID || args.sessionID || args.session_id || "",
    project,
    ts: timestamp(options),
  };
  appendTelemetry(record, options);
  return record;
}

function memoriesConfig(options = {}) {
  const env = readEnvFile(options);
  const processEnv = typeof process !== "undefined" ? process.env : {};
  return {
    url: options.memoriesUrl || env.MEMORIES_URL || processEnv.MEMORIES_URL || "",
    apiKey: options.memoriesApiKey || env.MEMORIES_API_KEY || processEnv.MEMORIES_API_KEY || "",
  };
}

function lastUserText(input = {}) {
  const messages = Array.isArray(input.messages) ? input.messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const text = messageUserText(messages[index]);
    if (text) return text;
  }
  return "";
}

function partText(part) {
  if (typeof part === "string") return part;
  if (!part || typeof part !== "object") return "";
  return String(part.text || part.content || "");
}

function messageUserText(message) {
  if (!message || typeof message !== "object") return "";
  const role = message.role || (message.info && message.info.role);
  if (role !== "user") return "";
  if (message.content) return String(message.content).trim();
  if (Array.isArray(message.parts)) return message.parts.map(partText).join(" ").trim();
  return "";
}

async function queryFromSessionMessages(input = {}, options = {}) {
  const client = options.client;
  if (!input.sessionID || !client || !client.session || typeof client.session.messages !== "function") return "";
  try {
    const response = await client.session.messages({
      path: { id: input.sessionID },
      query: { directory: options.directory },
    });
    const messages = Array.isArray(response) ? response : Array.isArray(response && response.data) ? response.data : [];
    return lastUserText({ messages });
  } catch (error) {
    return "";
  }
}

async function recallQuery(input = {}, options = {}) {
  return lastUserText(input) || (await queryFromSessionMessages(input, options));
}

function normalizeResults(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.results)) return payload.results;
  if (Array.isArray(payload.memories)) return payload.memories;
  return [];
}

function fetchTimeoutMs(options = {}) {
  const value = options.fetchTimeoutMs ?? 1200;
  return Number.isFinite(Number(value)) ? Number(value) : 1200;
}

async function fetchPrefixResults(fetchImpl, config, sourcePrefix, query, options = {}) {
  const timeoutMs = fetchTimeoutMs(options);
  const controller = typeof AbortController === "function" ? new AbortController() : undefined;
  const timeout = controller && timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    const response = await fetchImpl(`${config.url.replace(/\/$/, "")}/search`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(config.apiKey ? { "X-API-Key": config.apiKey } : {}),
      },
      ...(controller ? { signal: controller.signal } : {}),
      body: JSON.stringify({ query, source_prefix: sourcePrefix, k: 3 }),
    });
    if (!response || response.ok === false) return [];
    return normalizeResults(await response.json());
  } catch (error) {
    return [];
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

async function fetchRecallResults(input = {}, options = {}) {
  const fetchImpl = options.fetchImpl || (typeof fetch === "function" ? fetch : undefined);
  const config = memoriesConfig(options);
  if (!fetchImpl || !config.url) return [];

  const project = projectName(options);
  const query = await recallQuery(input, options);
  if (!query) return [];
  const batches = await Promise.all(defaultSourcePrefixes(project).map((sourcePrefix) => fetchPrefixResults(fetchImpl, config, sourcePrefix, query, options)));
  return batches.flat();
}

function sessionID(input = {}) {
  return input.sessionID || input.sessionId || input.session_id || "";
}

function ensureSystemArray(output = {}) {
  if (Array.isArray(output.system)) return output.system;
  if (output.system === undefined || output.system === null) {
    output.system = [];
    return output.system;
  }
  output.system = [String(output.system)];
  return output.system;
}

function server(input = {}, options = {}) {
  const hookOptions = { ...input, ...options };
  return {
    "experimental.chat.system.transform": async (hookInput = {}, hookOutput = {}) => {
      const project = projectName(hookOptions);
      const results = await fetchRecallResults(hookInput, hookOptions);
      const context = renderRecallContext({ project, results, activeSearchRequired: true });
      ensureSystemArray(hookOutput).push(context);
    },
    "tool.execute.after": async (hookInput = {}) => {
      const toolName = hookInput.tool || hookInput.toolName || hookInput.tool_name || hookInput.name || "";
      if (!isMemoryToolName(toolName)) return;
      const args = hookInput.args || hookInput.arguments || hookInput.input || {};
      await observeToolCall(toolName, args, sessionID(hookInput), hookOptions);
    },
  };
}

server.__test = {
  projectName,
  defaultSourcePrefixes,
  defaultExtractSource,
  sourcePrefixQuality,
  renderRecallContext,
  isMemoryToolName,
  observeToolCall,
  fetchRecallResults,
};

module.exports = { id: "memories", server };
