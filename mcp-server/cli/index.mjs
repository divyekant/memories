#!/usr/bin/env node
import os from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { detectAgents } from './detect.mjs';
import { checkHealth, bootstrapBackend } from './backend.mjs';
import { ask, askChoice } from './prompts.mjs';
import * as claudeCode from './adapters/claude-code.mjs';
import * as codex from './adapters/codex.mjs';
import * as cursor from './adapters/cursor.mjs';
import * as generic from './adapters/generic.mjs';

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

const VALID_FLAGS = ['--claude', '--codex', '--cursor', '--generic', '--dry-run', '--yes', '--url', '--api-key', '-h', '--help'];

const HELP_TEXT = `memories — installer/manager CLI for the Memories MCP plugin

Usage:
  memories init [--claude] [--codex] [--cursor] [--generic] [--url <u>] [--api-key <k>] [--dry-run] [--yes]
  memories update [same flags as init]
  memories doctor [--claude] [--codex] [--cursor] [--generic]
  memories uninstall [--claude] [--codex] [--cursor] [--generic] [--yes]
  memories --help

Flags:
  --claude, --codex, --cursor, --generic   Restrict to these targets (default: auto-detect)
  --url <u>                                Backend URL (default: $MEMORIES_URL or http://localhost:8900)
  --api-key <k>                            Backend API key (default: $MEMORIES_API_KEY or none)
  --dry-run                                Print the plan and exit before any writes
  --yes                                    Non-interactive: accept all defaults, skip prompts
  -h, --help                                Show this help`;

export function parseArgs(argv) {
  const args = [...argv];
  let command = 'help';
  if (args.length && !args[0].startsWith('-')) {
    command = args.shift();
  }

  const result = { command, targets: [], dryRun: false, yes: false, url: undefined, apiKey: undefined };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a in FLAG_TARGETS) {
      result.targets.push(FLAG_TARGETS[a]);
    } else if (a === '--dry-run') {
      result.dryRun = true;
    } else if (a === '--yes') {
      result.yes = true;
    } else if (a === '--url') {
      result.url = args[++i];
    } else if (a === '--api-key') {
      result.apiKey = args[++i];
    } else if (a === '-h' || a === '--help') {
      result.command = 'help';
    } else {
      throw new Error(`Unknown flag: ${a}. Valid flags: ${VALID_FLAGS.join(', ')}`);
    }
  }

  return result;
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

async function resolveExtraction(ctx) {
  const choice = await askChoice(
    'Backend is unreachable. Choose an extraction provider to bootstrap it locally now (or skip):',
    [
      { label: 'Anthropic (recommended, ~$0.001/turn, full AUDN)', value: 'anthropic' },
      { label: 'OpenAI (~$0.001/turn, full AUDN)', value: 'openai' },
      { label: 'Ollama (free, local, extraction only)', value: 'ollama' },
      { label: 'Skip (retrieval only)', value: 'skip' },
    ],
    { def: 'skip' },
  );

  if (choice === 'anthropic') {
    const keyVal = await ask('Anthropic API key', { def: '' });
    return { provider: 'anthropic', keyVar: 'ANTHROPIC_API_KEY', keyVal };
  }
  if (choice === 'openai') {
    const keyVal = await ask('OpenAI API key', { def: '' });
    return { provider: 'openai', keyVar: 'OPENAI_API_KEY', keyVal };
  }
  if (choice === 'ollama') return { provider: 'ollama' };
  return undefined; // skip
}

async function runInitOrUpdate(parsed, ctx, restrictedTargets) {
  const targets = await resolveTargets(parsed, ctx, restrictedTargets);

  if (parsed.dryRun) {
    ctx.log(`mode=${parsed.command}`);
    ctx.log(`targets=${targets.join(', ')}`);
    return;
  }

  const url = parsed.url ?? process.env.MEMORIES_URL ?? (parsed.yes ? DEFAULT_URL : await ask('Memories backend URL', { def: DEFAULT_URL }));
  const apiKey = parsed.apiKey ?? process.env.MEMORIES_API_KEY ?? (parsed.yes ? '' : await ask('Memories API key (blank for none)', { def: '' }));
  ctx.url = url;
  ctx.apiKey = apiKey;

  const health = await checkHealth(url, { fetchImpl: ctx.fetchImpl });
  if (!health.ok) {
    if (parsed.yes) {
      ctx.log(`Backend unreachable at ${url} (${health.error}) — continuing; clients will work once it is up.`);
    } else {
      const extract = await resolveExtraction(ctx);
      const result = await bootstrapBackend({ ...ctx, extract });
      if (result.ok) ctx.log(`Backend bootstrapped and healthy (${result.totalMemories} memories).`);
      else ctx.log(`Backend bootstrap did not become healthy: ${result.error}`);
    }
  }

  for (const t of targets) {
    await ADAPTERS[t].install(ctx);
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

  for (const t of targets) {
    await ADAPTERS[t].uninstall(ctx);
  }
  ctx.log(`Uninstall complete for: ${targets.join(', ')}`);
}

export async function run(argv, ctxOverrides = {}) {
  const parsed = parseArgs(argv);

  const home = ctxOverrides.home ?? os.homedir();
  const assetsDir = ctxOverrides.assetsDir ?? join(dirname(fileURLToPath(import.meta.url)), '../assets');
  const log = ctxOverrides.log ?? console.log;
  const ctx = { ...ctxOverrides, home, assetsDir, log, dryRun: parsed.dryRun };

  if (parsed.command === 'help') {
    log(HELP_TEXT);
    return;
  }

  if (!VALID_COMMANDS.includes(parsed.command)) {
    throw new Error(`Unknown command: ${parsed.command}. Valid commands: init, doctor, update, uninstall, help`);
  }

  let restrictedTargets = null;
  if (process.platform === 'win32') {
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

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) main();
