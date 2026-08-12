#!/usr/bin/env node
import os from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile, access } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { detectAgents } from './detect.mjs';
import { checkHealth, bootstrapBackend } from './backend.mjs';
import { ask, askChoice } from './prompts.mjs';
import { readState, writeState, statePath } from './lib/state.mjs';
import { readJson } from './lib/json-file.mjs';
import * as claudeCode from './adapters/claude-code.mjs';
import * as codex from './adapters/codex.mjs';
import * as cursor from './adapters/cursor.mjs';
import * as generic from './adapters/generic.mjs';

const exists = (p) => access(p).then(() => true, () => false);

const ADAPTERS = { 'claude-code': claudeCode, codex, cursor, generic };
const DETECTABLE_TARGETS = ['claude-code', 'codex', 'cursor'];
const VALID_COMMANDS = ['init', 'doctor', 'update', 'uninstall', 'help'];
const DEFAULT_URL = 'http://localhost:8900';

const FLAG_TARGETS = {
  '--claude': 'claude-code',
  '--codex': 'codex',
  '--cursor': 'cursor',
  '--generic': 'generic',
};

const VALID_FLAGS = ['--claude', '--codex', '--cursor', '--generic', '--dry-run', '--yes', '--url', '--mcp-url', '--api-key', '--mcp-name', '-h', '--help'];

const HELP_TEXT = `memories — installer/manager CLI for the Memories MCP plugin

Usage:
  memories init [--claude] [--codex] [--cursor] [--generic] [--url <u>] [--mcp-url <u>] [--api-key <k>] [--mcp-name <name>]... [--dry-run] [--yes]
  memories update [same flags as init]
  memories doctor [--claude] [--codex] [--cursor] [--generic]
  memories uninstall [--claude] [--codex] [--cursor] [--generic]
  memories --help

Flags:
  --claude, --codex, --cursor, --generic   Restrict to these targets (default: auto-detect)
  --url <u>                                Backend URL (default: $MEMORIES_URL or http://localhost:8900)
  --mcp-url <u>                             Direct remote MCP URL (Codex only; uses OAuth)
  --api-key <k>                            Backend API key (default: $MEMORIES_API_KEY or none)
  --mcp-name <name>                        Additional MCP server name to pre-approve read-only tools
                                            for (claude-code/cursor only). Repeatable. Use when your
                                            memory MCP server is registered under a name other than
                                            "memories" — a claude.ai connector, or a manual rename.
                                            There is no wildcard for the server segment, so names not
                                            known at install time (e.g. a UUID-named connector) still
                                            need this flag or a manual permissions.allow entry.
  --dry-run                                Print the plan and exit before any writes
  --yes                                    Non-interactive: accept all defaults, skip prompts
  -h, --help                                Show this help`;

export function parseArgs(argv) {
  const args = [...argv];
  let command = 'help';
  if (args.length && !args[0].startsWith('-')) {
    command = args.shift();
  }

  const result = { command, targets: [], dryRun: false, yes: false, url: undefined, mcpUrl: undefined, apiKey: undefined, mcpNames: [] };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a in FLAG_TARGETS) {
      result.targets.push(FLAG_TARGETS[a]);
    } else if (a === '--dry-run') {
      result.dryRun = true;
    } else if (a === '--yes') {
      result.yes = true;
    } else if (a === '--url') {
      const next = args[i + 1];
      if (next === undefined || next.startsWith('--')) throw new Error('Missing value for --url');
      result.url = args[++i];
    } else if (a === '--mcp-url') {
      const next = args[i + 1];
      if (next === undefined || next.startsWith('--')) throw new Error('Missing value for --mcp-url');
      result.mcpUrl = args[++i];
    } else if (a === '--api-key') {
      const next = args[i + 1];
      if (next === undefined || next.startsWith('--')) throw new Error('Missing value for --api-key');
      result.apiKey = args[++i];
    } else if (a === '--mcp-name') {
      const next = args[i + 1];
      if (next === undefined || next.startsWith('--')) throw new Error('Missing value for --mcp-name');
      result.mcpNames.push(args[++i]);
    } else if (a === '-h' || a === '--help') {
      result.command = 'help';
    } else {
      throw new Error(`Unknown flag: ${a}. Valid flags: ${VALID_FLAGS.join(', ')}`);
    }
  }

  return result;
}

function validateRemoteMcpOptions(parsed, targets) {
  if (parsed.mcpUrl === undefined) return;
  if (parsed.url !== undefined) {
    throw new Error('--mcp-url cannot be combined with --url');
  }
  if (parsed.apiKey !== undefined) {
    throw new Error('--mcp-url cannot be combined with --api-key');
  }
  if (!targets.length || targets.some((target) => target !== 'codex')) {
    throw new Error('--mcp-url is only supported with --codex');
  }
}

export function validateRemoteMcpUrl(value) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error('--mcp-url must be an absolute HTTPS URL');
  }
  // Raw whitespace/control characters can break the TOML basic string even
  // though URL() may accept and normalize some of them. Reject before any
  // prompt, log, health check, or installer mutation can occur.
  if (/\s|[\u0000-\u001f\u007f-\u009f]/u.test(value)) {
    throw new Error('--mcp-url must not contain whitespace or control characters');
  }

  let remoteUrl;
  try {
    remoteUrl = new URL(value);
  } catch {
    throw new Error('--mcp-url must be an absolute HTTPS URL');
  }
  if (!value.startsWith('https://')) {
    throw new Error('--mcp-url must use canonical HTTPS URL syntax beginning with https://');
  }
  if (!remoteUrl.hostname) {
    throw new Error('--mcp-url must be an absolute HTTPS URL with a host');
  }
  if (remoteUrl.protocol !== 'https:') {
    throw new Error('--mcp-url must use https:// for remote OAuth MCP');
  }
  if (remoteUrl.href !== value) {
    throw new Error('--mcp-url must use canonical HTTPS URL syntax');
  }
  if (remoteUrl.username || remoteUrl.password) {
    throw new Error('--mcp-url must not include credentials');
  }
  // URL.hash is empty for a bare trailing '#', so inspect the original input
  // as well as the parsed URL to reject all fragments.
  if (remoteUrl.hash || value.includes('#')) {
    throw new Error('--mcp-url must not include a fragment');
  }
}

async function autoDetectTargets(home) {
  const detected = await detectAgents(home);
  const found = DETECTABLE_TARGETS.filter((t) => detected[t]);
  return found.length ? found : ['generic'];
}

async function resolveTargets(parsed, ctx, restrictedTargets) {
  if (restrictedTargets) return restrictedTargets;
  if (parsed.targets.length) return parsed.targets;
  return autoDetectTargets(ctx.home);
}

// Detects a pre-existing ~/.claude-side install that predates state.json
// (e.g. from install.sh or a marketplace plugin install). Deliberately does
// NOT use adapters['claude-code'].status(), which requires BOTH hooks and an
// mcpServers entry — a marketplace-plugin install can have the MCP entry
// wired without our hooks dir, so any one of these signals must suffice.
async function claudeSidePresent(ctx) {
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  if (settings.mcpServers?.memories) return true;
  if (await exists(join(ctx.home, '.claude/hooks/memory'))) return true;
  if (await exists(join(ctx.home, '.claude/skills/memories'))) return true;
  try {
    const md = await readFile(join(ctx.home, '.claude/CLAUDE.md'), 'utf8');
    if (md.includes(`# BEGIN ${claudeCode.MARKER}`)) return true;
  } catch {
    // no CLAUDE.md — not present
  }
  return false;
}

async function resolveExtraction(ctx) {
  const choice = await ctx.askChoiceImpl(
    'Choose an extraction provider to bootstrap it locally now (or skip):',
    [
      { label: 'Anthropic (recommended, ~$0.001/turn, full AUDN)', value: 'anthropic' },
      { label: 'OpenAI (~$0.001/turn, full AUDN)', value: 'openai' },
      { label: 'Ollama (free, local, extraction only)', value: 'ollama' },
      { label: 'Skip (retrieval only)', value: 'skip' },
    ],
    { def: 'skip' },
  );

  if (choice === 'anthropic') {
    const keyVal = await ctx.askImpl('Anthropic API key', { def: '' });
    return { provider: 'anthropic', keyVar: 'ANTHROPIC_API_KEY', keyVal };
  }
  if (choice === 'openai') {
    const keyVal = await ctx.askImpl('OpenAI API key', { def: '' });
    return { provider: 'openai', keyVar: 'OPENAI_API_KEY', keyVal };
  }
  if (choice === 'ollama') return { provider: 'ollama' };
  return undefined; // skip
}

// Interactive (non---yes) health-failure path: OFFER bootstrap, never force it.
// Declining prints exact manual steps and falls through to adapter wiring.
// bootstrapBackend itself is wrapped in try/catch — a Docker failure must not
// abort the command, matching the resilience of the --yes path.
async function offerBackendBootstrap(ctx) {
  const composePath = join(ctx.home, '.config/memories/docker-compose.yml');
  const provision = await ctx.askImpl('Provision the backend now with Docker? [Y/n]', { def: 'Y' });

  if (!/^y/i.test(String(provision).trim())) {
    ctx.log('Skipping automatic bootstrap. To provision manually:');
    ctx.log(`  1. Compose file: ${composePath} (created by \`memories init\`, or copy assets/backend/docker-compose.standalone.yml there)`);
    ctx.log(`  2. docker compose -f ${composePath} up -d`);
    ctx.log('  3. Re-run `memories doctor` to confirm the backend is healthy.');
    return;
  }

  const extract = await resolveExtraction(ctx);
  try {
    const result = await bootstrapBackend({ ...ctx, extract });
    if (result.ok) ctx.log(`Backend bootstrapped and healthy (${result.totalMemories} memories).`);
    else ctx.log(`Backend bootstrap did not become healthy: ${result.error}`);
  } catch (err) {
    ctx.log(`Backend bootstrap failed: ${err.message} — continuing without it; clients will work once the backend is up.`);
  }
}

async function runInitOrUpdate(parsed, ctx, restrictedTargets) {
  const targets = await resolveTargets(parsed, ctx, restrictedTargets);
  validateRemoteMcpOptions(parsed, targets);

  if (parsed.dryRun) {
    ctx.log(`mode=${parsed.command}`);
    ctx.log(`targets=${targets.join(', ')}`);
    return;
  }

  // Default 'memories' plus any --mcp-name overrides, deduped — consumed by
  // the claude-code/cursor adapters when writing the read-only allowlist.
  ctx.mcpNames = [...new Set(['memories', ...parsed.mcpNames])];

  if (parsed.mcpUrl !== undefined) {
    // A direct remote MCP URL is already the client-facing endpoint. It uses
    // OAuth at that endpoint, so there is no local REST backend to probe or
    // bootstrap and no backend API key to copy into Codex configuration.
    ctx.mcpUrl = parsed.mcpUrl;
    ctx.url = undefined;
    ctx.apiKey = '';
  } else {
    const url = parsed.url ?? process.env.MEMORIES_URL ?? (parsed.yes ? DEFAULT_URL : await ctx.askImpl('Memories backend URL', { def: DEFAULT_URL }));
    const apiKey = parsed.apiKey ?? process.env.MEMORIES_API_KEY ?? (parsed.yes ? '' : await ctx.askImpl('Memories API key (blank for none)', { def: '' }));
    ctx.url = url;
    ctx.apiKey = apiKey;

    const health = await checkHealth(url, { fetchImpl: ctx.fetchImpl });
    if (!health.ok) {
      if (parsed.yes) {
        ctx.log(`Backend unreachable at ${url} (${health.error}) — continuing; clients will work once it is up.`);
      } else {
        await offerBackendBootstrap(ctx);
      }
    } else if (parsed.yes) {
      ctx.log(`Backend healthy (${health.totalMemories} memories).`);
    }
  }

  for (const t of targets) {
    if (t === 'cursor') {
      // Adopt a pre-existing claude-code install into ownership state BEFORE
      // cursor's delegation writes anything — otherwise a legacy install.sh
      // / marketplace-plugin claude-code setup (no state.json yet) looks
      // indistinguishable from wiring cursor just created itself, and a
      // later `uninstall --cursor` would conclude cursor owns it and delete
      // it out from under the user.
      //
      // Gate on 'cursor' NOT already being tracked too: once cursor has run
      // once, the claude-side files it delegated into existence are
      // indistinguishable on disk from a genuinely pre-existing install — on
      // a second `init`/`update --cursor`, presence detection would fire
      // again and adopt 'claude-code' into state, making a later
      // `uninstall --cursor` treat the shared wiring as independently
      // claude-owned and leave it behind (reintroducing the original bug
      // for anyone who runs `update` even once).
      const preState = await readState(ctx.home);
      if (
        !preState.installedTargets.includes('claude-code')
        && !preState.installedTargets.includes('cursor')
        && await claudeSidePresent(ctx)
      ) {
        preState.installedTargets.push('claude-code');
        await writeState(ctx.home, preState);
      }
    }
    await ADAPTERS[t].install(ctx);
    const state = await readState(ctx.home);
    if (!state.installedTargets.includes(t)) state.installedTargets.push(t);
    await writeState(ctx.home, state);
  }
  if (parsed.mcpUrl !== undefined) {
    ctx.log('Run `codex mcp login memories` to authenticate the remote Memories MCP server.');
  }
  ctx.log(`${parsed.command === 'init' ? 'Init' : 'Update'} complete for: ${targets.join(', ')}`);
}

async function runDoctor(parsed, ctx, restrictedTargets) {
  const targets = await resolveTargets(parsed, ctx, restrictedTargets);

  for (const t of targets) {
    if (t === 'generic') {
      ctx.log('generic: no local state (by design)');
      continue;
    }
    const s = await ADAPTERS[t].status(ctx);
    ctx.log(`${t}: ${s.installed ? 'installed' : 'not installed'} — ${s.details.join(', ')}`);
  }

  const url = parsed.url ?? process.env.MEMORIES_URL ?? DEFAULT_URL;
  const health = await checkHealth(url, { fetchImpl: ctx.fetchImpl });
  ctx.log(`backend (${url}): ${health.ok ? `healthy (${health.totalMemories} memories)` : `unreachable (${health.error})`}`);

  const pkgVersion = (await readFile(join(ctx.assetsDir, 'backend/BACKEND_VERSION'), 'utf8')).trim();
  let deployedVersion = 'unknown';
  try {
    const fetchImpl = ctx.fetchImpl ?? globalThis.fetch;
    const res = await fetchImpl(`${url}/health`, { signal: AbortSignal.timeout(3000) });
    const body = await res.json();
    deployedVersion = body.version ?? 'unknown';
  } catch {
    // leave as 'unknown' — already reported unreachable above
  }
  const mismatch = deployedVersion !== 'unknown' && deployedVersion !== pkgVersion;
  ctx.log(`package backend version: ${pkgVersion}, deployed: ${deployedVersion}${mismatch ? ' (mismatch — consider `memories update`)' : ''}`);
}

async function runUninstall(parsed, ctx, restrictedTargets) {
  const targets = await resolveTargets(parsed, ctx, restrictedTargets);

  if (parsed.dryRun) {
    ctx.log('mode=uninstall');
    ctx.log(`targets=${targets.join(', ')}`);
    return;
  }

  // cursor.install delegates to claude-code.install to wire the shared
  // ~/.claude side (hooks/skills/CLAUDE.md/MCP entry), but cursor.uninstall
  // only ever touched ~/.cursor/mcp.json — so `uninstall --cursor` used to
  // leave all of that shared wiring behind. Use the ownership state file to
  // tell whether cursor was the one that put it there (nothing else tracked
  // 'claude-code'), and if so, tear it down too — but never when claude-code
  // is *also* being uninstalled in this same run (it'll clean itself up).
  const stateFileExisted = await exists(statePath(ctx.home));
  const stateBefore = await readState(ctx.home);
  const sharedOwnedByClaude = stateBefore.installedTargets.includes('claude-code');

  for (const t of targets) {
    await ADAPTERS[t].uninstall(ctx);
    const state = await readState(ctx.home);
    state.installedTargets = state.installedTargets.filter((x) => x !== t);
    await writeState(ctx.home, state);

    if (t === 'cursor') {
      const cursorWasTracked = stateBefore.installedTargets.includes('cursor');
      const claudeAlsoBeingUninstalled = targets.includes('claude-code');
      if (stateFileExisted) {
        if (cursorWasTracked && !sharedOwnedByClaude && !claudeAlsoBeingUninstalled) {
          await ADAPTERS['claude-code'].uninstall(ctx);
          const state2 = await readState(ctx.home);
          state2.installedTargets = state2.installedTargets.filter((x) => x !== 'claude-code');
          await writeState(ctx.home, state2);
          ctx.log('Cursor owned the shared Claude-side wiring (hooks/skills/CLAUDE.md/MCP entry) — removed it too.');
        }
      } else {
        // Pre-upgrade install with no state file: stay conservative, only
        // touch the cursor entry, and tell the user how to finish the job.
        ctx.log('Shared Claude-side wiring left in place — remove with `memories uninstall --claude` if unwanted.');
      }
    }
  }
  ctx.log(`Uninstall complete for: ${targets.join(', ')}`);
}

export async function run(argv, ctxOverrides = {}) {
  const parsed = parseArgs(argv);

  const home = ctxOverrides.home ?? os.homedir();
  const assetsDir = ctxOverrides.assetsDir ?? join(dirname(fileURLToPath(import.meta.url)), '../assets');
  const log = ctxOverrides.log ?? console.log;
  const ctx = { ...ctxOverrides, home, assetsDir, log, dryRun: parsed.dryRun };
  // Injectable so tests can simulate the interactive (non---yes) prompt path
  // without touching stdin; default to the real readline-backed prompts.
  ctx.askImpl = ctx.askImpl ?? ask;
  ctx.askChoiceImpl = ctx.askChoiceImpl ?? askChoice;

  if (parsed.command === 'help') {
    log(HELP_TEXT);
    return;
  }

  if (!VALID_COMMANDS.includes(parsed.command)) {
    throw new Error(`Unknown command: ${parsed.command}. Valid commands: init, doctor, update, uninstall, help`);
  }

  // Validate the raw remote endpoint before platform restrictions can log or
  // target resolution can perform any other work. Target/flag combinations
  // remain checked later, after the target set is resolved.
  if (parsed.mcpUrl !== undefined) validateRemoteMcpUrl(parsed.mcpUrl);

  if (parsed.mcpUrl !== undefined && parsed.command !== 'init' && parsed.command !== 'update') {
    throw new Error('--mcp-url is only supported with init/update --codex');
  }

  let restrictedTargets = null;
  const platform = ctxOverrides.platform ?? process.platform;
  if (platform === 'win32') {
    restrictedTargets = ['generic'];
    log('Windows detected — only the generic (manual) target is supported here; wire other clients by hand using `memories doctor` output.');
  }

  if (parsed.command === 'init' || parsed.command === 'update') {
    await runInitOrUpdate(parsed, ctx, restrictedTargets);
  } else if (parsed.command === 'doctor') {
    await runDoctor(parsed, ctx, restrictedTargets);
  } else if (parsed.command === 'uninstall') {
    await runUninstall(parsed, ctx, restrictedTargets);
  }
}

async function main() {
  try {
    await run(process.argv.slice(2));
  } catch (err) {
    console.error(`\x1b[31m${err.message}\x1b[0m`);
    process.exitCode = 1;
  }
}

// npm/npx invoke bin scripts through a symlink under node_modules/.bin, so
// process.argv[1] is the symlink path, not this file's real path — a plain
// === comparison against fileURLToPath(import.meta.url) never matches and
// `npx memories-mcp init` silently no-ops. Resolve through realpath first.
const selfPath = fileURLToPath(import.meta.url);
const isMain = (() => {
  try {
    return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === selfPath;
  } catch {
    return false;
  }
})();
if (isMain) main();
