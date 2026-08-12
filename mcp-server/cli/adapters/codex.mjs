import { chmod, copyFile, mkdir, readFile, rm, writeFile, access } from 'node:fs/promises';
import { execFile as nodeExecFile } from 'node:child_process';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { readJson, writeJson, removePermissions, mergeHookSettings } from '../lib/json-file.mjs';
import { renderHooksJson, copyHookScripts, READONLY_MCP_TOOL_NAMES } from '../lib/hooks.mjs';
import { installStatePath, readRecordedPermissions, clearRecordedPermissions } from '../lib/install-state.mjs';
import { upsertMarkedBlock, insertMarkedBlockAtRoot, removeMarkedBlockStrict, validateMarkedBlock, hasTomlSection, hasTomlKey, tomlEscape } from '../lib/toml.mjs';

const MARKER_NOTIFY = 'Memories Codex notify';
const MARKER_MCP = 'Memories Codex MCP';
const MARKER_DEV = 'Memories Codex developer instructions';
const CODEX_EXPANDED_HOOK_VERSION = [0, 146, 0];
const CODEX_MANAGED_HOOKS = [
  'memory-recall.sh', 'memory-query.sh', 'memory-extract.sh',
  'memory-observe.sh', 'memory-guard.sh', 'memory-flush.sh',
  'memory-rehydrate.sh', 'memory-subagent-recall.sh',
  'memory-subagent-capture.sh', 'memory-commit.sh',
];
const execFile = promisify(nodeExecFile);

const DEVELOPER_INSTRUCTIONS = `developer_instructions = """
Use the Memories MCP tools as your memory layer with three responsibilities:

1. READ: Run memory_search before implementation-heavy responses, clarifying questions, or any turn that depends on prior decisions, prior sessions, project history, deferred work, conventions, or cross-session context. Hook-injected memories are useful hints, not a substitute for active search.
2. WRITE: Use memory_add for single clear facts (check memory_is_novel first). Use memory_extract for rich conversations, decision changes, or deferred work updates — it handles Add/Update/Delete/Noop automatically via AUDN. For scoped keys, always pass a non-empty source on memory_extract.
3. MAINTAIN: Use memory_delete for explicit forget requests. memory_extract handles most lifecycle updates automatically. For bulk cleanup with scoped keys, prefer prefix-based deletion patterns that stay inside authorized sources.

Source prefixes: replace {project} with the current working directory basename. Search exact project-scoped prefixes first: codex/{project}, claude-code/{project}, learning/{project}, and wip/{project}. If hook candidate pointers list a source, use that exact source_prefix. Do not use broad family prefixes like codex/, claude-code/, learning/, wip/, or unscoped search until the exact project prefixes have been tried. Use only authorized prefixes when scoped keys restrict access.
"""`;

const exists = (p) => access(p).then(() => true, () => false);

/**
 * Codex added the expanded lifecycle events in 0.146.0. Parse only the first
 * semantic version in the command output so distro suffixes and a leading
 * `codex-cli` label do not affect the numeric comparison.
 */
export function supportsExpandedHooks(versionText) {
  const match = String(versionText ?? '').match(/(\d+)\.(\d+)\.(\d+)/);
  if (!match) return false;
  const version = match.slice(1, 4).map(Number);
  for (let i = 0; i < CODEX_EXPANDED_HOOK_VERSION.length; i += 1) {
    if (version[i] !== CODEX_EXPANDED_HOOK_VERSION[i]) {
      return version[i] > CODEX_EXPANDED_HOOK_VERSION[i];
    }
  }
  return true;
}

async function detectCodexVersion(ctx) {
  if (ctx.codexVersion !== undefined) {
    return String(ctx.codexVersion ?? '');
  }
  const execImpl = ctx.execFileImpl ?? execFile;
  try {
    const result = await execImpl('codex', ['--version']);
    return typeof result === 'string' ? result : String(result?.stdout ?? '');
  } catch {
    return '';
  }
}

const LEGACY_HOOK_COMMANDS = {
  SessionStart: 'memory-recall.sh',
  UserPromptSubmit: 'memory-query.sh',
  Stop: 'memory-extract.sh',
  PostToolUse: 'memory-observe.sh',
  PreToolUse: 'memory-guard.sh',
};
const EXPANDED_HOOK_COMMANDS = {
  ...LEGACY_HOOK_COMMANDS,
  PreCompact: 'memory-flush.sh',
  PostCompact: 'memory-rehydrate.sh',
  SubagentStart: 'memory-subagent-recall.sh',
  SubagentStop: 'memory-subagent-capture.sh',
  SessionEnd: 'memory-commit.sh',
};

async function installedHookProfileAt(path, hooksDir) {
  try {
    const hooks = await readJson(path);
    if (!hooks.hooks || !hooksDir) return 'unknown';
    const owns = (event, filename) => {
      const command = join(hooksDir, filename);
      return (hooks.hooks[event] ?? []).some((entry) =>
        (entry.hooks ?? []).some((hook) => hook.type === 'command' && hook.command === command));
    };
    if (Object.entries(EXPANDED_HOOK_COMMANDS).every(([event, filename]) => owns(event, filename))) {
      return 'expanded';
    }
    if (Object.entries(LEGACY_HOOK_COMMANDS).every(([event, filename]) => owns(event, filename))) {
      return 'legacy';
    }
    return 'unknown';
  } catch {
    return 'unknown';
  }
}

function removeManagedHooks(settings, hooksDir) {
  if (!settings.hooks) return settings;
  const ownedCommands = new Set(CODEX_MANAGED_HOOKS.map((name) => join(hooksDir, name)));
  const hooks = {};
  for (const [event, entries] of Object.entries(settings.hooks)) {
    const kept = entries
      .map((entry) => ({ ...entry, hooks: (entry.hooks ?? []).filter((hook) => !ownedCommands.has(hook.command)) }))
      .filter((entry) => entry.hooks.length > 0);
    if (kept.length) hooks[event] = kept;
  }
  return { ...settings, hooks };
}

const paths = (ctx) => ({
  hooksSrc: join(ctx.assetsDir, 'codex/hooks'),
  hooksDest: join(ctx.home, '.codex/hooks/memory'),
  hooksJson: join(ctx.home, '.codex/hooks.json'),
  notifySrc: join(ctx.assetsDir, 'codex/memory-codex-notify.sh'),
  settings: join(ctx.home, '.codex/settings.json'),
  config: join(ctx.home, '.codex/config.toml'),
});

function mcpBlock(ctx) {
  const approvals = READONLY_MCP_TOOL_NAMES.map((tool) =>
    `[mcp_servers.memories.tools.${tool}]\napproval_mode = "approve"`,
  ).join('\n\n');
  return `[mcp_servers.memories]
command = "npx"
args = ["-y", "memories-mcp"]
default_tools_approval_mode = "prompt"

[mcp_servers.memories.env]
MEMORIES_URL = "${tomlEscape(ctx.url)}"
MEMORIES_API_KEY = "${tomlEscape(ctx.apiKey)}"
MEMORIES_CLIENT = "codex"

${approvals}`;
}

export async function install(ctx) {
  const p = paths(ctx);
  const statePath = installStatePath(ctx.home);
  const recordedRules = await readRecordedPermissions(statePath, 'codex');

  let toml = (await exists(p.config)) ? await readFile(p.config, 'utf8') : '';
  // Preflight every installer-owned marker before preparing hooks or changing
  // observable install state. Developer insertion is strict as well, but this
  // explicit pass ensures no malformed notify/MCP/developer marker can be
  // bypassed by an idempotence check below.
  validateMarkedBlock(toml, MARKER_NOTIFY);
  const mcpMarker = validateMarkedBlock(toml, MARKER_MCP);
  validateMarkedBlock(toml, MARKER_DEV);

  const versionText = await detectCodexVersion(ctx);
  const profile = supportsExpandedHooks(versionText) ? 'expanded' : 'legacy';
  const manifest = profile === 'expanded' ? 'hooks.json' : 'hooks.legacy.json';
  const hooksConfig = JSON.parse(await readFile(join(p.hooksSrc, manifest), 'utf8'));
  const rendered = renderHooksJson(hooksConfig, p.hooksDest);
  // Remove only commands from a prior Memories profile before merging. This
  // lets a downgrade from expanded to legacy remove stale lifecycle entries
  // while mergeHookSettings still preserves every foreign hook entry.
  const existingHooks = removeManagedHooks(await readJson(p.hooksJson), p.hooksDest);
  // An existing MCP section without any ownership marker belongs to the user.
  // Refresh only a marked block, or create one when no Memories server exists.
  // upsertMarkedBlock fails closed when the marker pair is incomplete/ambiguous.
  if (mcpMarker || !hasTomlSection(toml, 'mcp_servers.memories')) {
    toml = upsertMarkedBlock(toml, MARKER_MCP, mcpBlock(ctx));
  }

  if (!hasTomlKey(toml, 'developer_instructions')) {
    toml = insertMarkedBlockAtRoot(toml, MARKER_DEV, DEVELOPER_INSTRUCTIONS);
  }

  // All config validation and in-memory preparation completes before any
  // filesystem mutation. A malformed owned TOML block must not leave hooks or
  // foreign hook settings behind when the install fails closed.
  ctx.codexHookProfile = profile;
  await copyHookScripts(p.hooksSrc, p.hooksDest);
  await copyFile(p.notifySrc, join(p.hooksDest, 'memory-codex-notify.sh'));
  await chmod(join(p.hooksDest, 'memory-codex-notify.sh'), 0o755);
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeJson(p.hooksJson, mergeHookSettings(existingHooks, rendered));
  await writeFile(p.config, toml);

  // v5.12 wrote read-only Codex approvals to settings.json. Once the current
  // TOML policy is durable, remove exactly the rules that install-state says
  // that older run introduced, then clear provenance last for retry safety.
  if (recordedRules !== null && await exists(p.settings)) {
    const owned = new Set(recordedRules);
    await writeJson(p.settings, removePermissions(await readJson(p.settings), (rule) => owned.has(rule)));
  }
  await clearRecordedPermissions(statePath, 'codex');

  ctx.log(`Codex wired (hooks: ${p.hooksDest}, hook profile: ${profile})`);
}

export async function uninstall(ctx) {
  const p = paths(ctx);
  // Read before the removal below erases the evidence.
  const recordedRules = await readRecordedPermissions(installStatePath(ctx.home), 'codex');

  // Validate and prepare every installer-owned TOML block before touching any
  // other artifact. A malformed later marker must not partially remove earlier
  // blocks, hooks, settings rules, or the ownership record.
  let toml = null;
  if (await exists(p.config)) {
    toml = await readFile(p.config, 'utf8');
    for (const marker of [MARKER_NOTIFY, MARKER_MCP, MARKER_DEV]) {
      toml = removeMarkedBlockStrict(toml, marker);
    }
  }

  await rm(p.hooksDest, { recursive: true, force: true });

  if (await exists(p.hooksJson)) {
    const hooksJson = await readJson(p.hooksJson);
    if (hooksJson.hooks) {
      for (const [event, entries] of Object.entries(hooksJson.hooks)) {
        const kept = entries
          .map((e) => ({ ...e, hooks: (e.hooks ?? []).filter((h) => !String(h.command ?? '').includes('/hooks/memory/memory-')) }))
          .filter((e) => e.hooks.length > 0);
        if (kept.length) hooksJson.hooks[event] = kept; else delete hooksJson.hooks[event];
      }
    }
    await writeJson(p.hooksJson, hooksJson);
  }

  if (await exists(p.settings)) {
    // Only rules this install recorded; see the claude-code adapter for why
    // shape-matching is unsafe here.
    const owned = new Set(recordedRules ?? []);
    await writeJson(p.settings, removePermissions(await readJson(p.settings), (rule) => owned.has(rule)));
  }

  if (toml !== null) {
    await writeFile(p.config, toml);
  }

  // Last: a throw anywhere above must leave the record intact so a retry can
  // still identify what we own.
  await clearRecordedPermissions(installStatePath(ctx.home), 'codex');
  ctx.log('Codex integration removed');
}

export async function status(ctx) {
  const p = paths(ctx);
  const hooks = await exists(p.hooksDest);
  const toml = (await exists(p.config)) ? await readFile(p.config, 'utf8') : '';
  const mcp = hasTomlSection(toml, 'mcp_servers.memories');
  const profile = await installedHookProfileAt(p.hooksJson, p.hooksDest);
  return { installed: hooks && mcp, details: [`hooks: ${hooks}`, `mcp: ${mcp}`, `hook profile: ${profile}`] };
}
