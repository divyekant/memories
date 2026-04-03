/**
 * memories-mcp installer
 *
 * Sets up automatic memory hooks and MCP config for Claude Code, Codex,
 * Cursor, and OpenClaw — without requiring the repo to be cloned.
 *
 * Usage:
 *   npx memories-mcp install [--auto] [--claude] [--codex] [--cursor] [--openclaw] [--dry-run]
 *   npx memories-mcp uninstall [--claude] [--codex] [--cursor] [--openclaw]
 */

import fs from 'fs';
import path from 'path';
import os from 'os';
import readline from 'readline';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const HOOKS_SRC = path.join(__dirname, 'hooks');
const OPENCLAW_SKILL_SRC = path.join(__dirname, 'assets', 'openclaw-skill.md');
const HOME = os.homedir();

const RED    = '\x1b[31m';
const GREEN  = '\x1b[32m';
const YELLOW = '\x1b[33m';
const BLUE   = '\x1b[34m';
const NC     = '\x1b[0m';

const ok   = (msg) => process.stdout.write(`  ${GREEN}[OK]${NC}   ${msg}\n`);
const warn = (msg) => process.stdout.write(`  ${YELLOW}[WARN]${NC} ${msg}\n`);
const fail = (msg) => process.stdout.write(`  ${RED}[FAIL]${NC} ${msg}\n`);
const skip = (msg) => process.stdout.write(`  ${YELLOW}[SKIP]${NC} ${msg}\n`);
const note = (msg) => process.stdout.write(`  ${YELLOW}[NOTE]${NC} ${msg}\n`);

function ask(rl, question) {
  return new Promise(resolve => rl.question(question, resolve));
}

function readJson(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch { return {}; }
}

function writeJson(filePath, obj) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(obj, null, 2) + '\n');
}

function mergeDeep(target, source) {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
      result[key] = mergeDeep(target[key] || {}, source[key]);
    } else {
      result[key] = source[key];
    }
  }
  return result;
}

async function checkHealth(url) {
  try {
    const res = await fetch(`${url}/health`, { signal: AbortSignal.timeout(5000) });
    if (res.ok) return await res.json();
  } catch {}
  return null;
}

function renderHooksJson(hooksDir, codexOnly = false) {
  const raw = JSON.parse(fs.readFileSync(path.join(HOOKS_SRC, 'hooks.json'), 'utf8'));
  const codexEvents = new Set(['SessionStart', 'UserPromptSubmit', 'Stop', 'PreCompact', 'SessionEnd']);
  const filtered = {};
  for (const [event, matchers] of Object.entries(raw.hooks)) {
    if (codexOnly && !codexEvents.has(event)) continue;
    filtered[event] = matchers.map(matcher => ({
      ...matcher,
      hooks: matcher.hooks.map(hook => {
        if (hook.type !== 'command') return hook;
        // Replace the env-var-based path with the resolved install path
        const scriptName = path.basename(hook.command.replace(/^\$\{[^}]+\}\//, ''));
        return { ...hook, command: path.join(hooksDir, scriptName) };
      }),
    }));
  }
  return { hooks: filtered };
}

function installHooks(label, hooksDir, settingsFile, codexOnly = false) {
  fs.mkdirSync(hooksDir, { recursive: true });

  for (const file of fs.readdirSync(HOOKS_SRC)) {
    if (file === 'hooks.json') continue;
    const dst = path.join(hooksDir, file);
    fs.copyFileSync(path.join(HOOKS_SRC, file), dst);
    if (file.endsWith('.sh')) fs.chmodSync(dst, 0o755);
  }
  ok(`Installed ${label} hooks: ${hooksDir}`);

  fs.mkdirSync(path.dirname(settingsFile), { recursive: true });
  if (!fs.existsSync(settingsFile)) writeJson(settingsFile, {});

  const existing = readJson(settingsFile);
  const hooksPatch = renderHooksJson(hooksDir, codexOnly);
  writeJson(settingsFile, mergeDeep(existing, { hooks: hooksPatch.hooks }));
  ok(`Merged hook config into ${settingsFile}`);

  const readonlyTools = [
    'mcp__memories__memory_search',
    'mcp__memories__memory_list',
    'mcp__memories__memory_count',
    'mcp__memories__memory_stats',
    'mcp__memories__memory_is_novel',
    'mcp__memories__memory_is_useful',
    'mcp__memories__memory_conflicts',
  ];
  const withPerms = readJson(settingsFile);
  const existingAllow = withPerms.permissions?.allow || [];
  withPerms.permissions = {
    ...(withPerms.permissions || {}),
    allow: [...new Set([...existingAllow, ...readonlyTools])],
  };
  writeJson(settingsFile, withPerms);
  ok(`Merged read-only tool permissions into ${settingsFile}`);
}

function installMcpJson(label, settingsFile, memoriesUrl, memoriesApiKey) {
  fs.mkdirSync(path.dirname(settingsFile), { recursive: true });
  if (!fs.existsSync(settingsFile)) writeJson(settingsFile, {});

  const existing = readJson(settingsFile);
  if (existing.mcpServers?.memories) {
    skip(`${label} MCP 'memories' already configured in ${settingsFile}`);
    return;
  }

  writeJson(settingsFile, mergeDeep(existing, {
    mcpServers: {
      memories: {
        command: 'npx',
        args: ['-y', 'memories-mcp'],
        env: { MEMORIES_URL: memoriesUrl, MEMORIES_API_KEY: memoriesApiKey },
      },
    },
  }));
  ok(`Added ${label} MCP config in ${settingsFile}`);
}

function appendTomlBlock(filePath, marker, body) {
  let content = '';
  try { content = fs.readFileSync(filePath, 'utf8'); } catch {}
  if (content.includes(`# BEGIN ${marker}`)) return false;
  fs.appendFileSync(filePath, `\n# BEGIN ${marker}\n${body}\n# END ${marker}\n`);
  return true;
}

function removeTomlBlock(filePath, marker) {
  let content = '';
  try { content = fs.readFileSync(filePath, 'utf8'); } catch { return; }
  const start = `# BEGIN ${marker}`;
  const end = `# END ${marker}`;
  if (!content.includes(start)) return;
  const lines = content.split('\n');
  const out = [];
  let inBlock = false;
  for (const line of lines) {
    if (line === start) { inBlock = true; continue; }
    if (line === end)   { inBlock = false; continue; }
    if (!inBlock) out.push(line);
  }
  fs.writeFileSync(filePath, out.join('\n'));
  ok(`Removed ${marker} from ${filePath}`);
}

function writeEnvVar(envFile, name, value) {
  let content = '';
  try { content = fs.readFileSync(envFile, 'utf8'); } catch {}
  if (content.split('\n').some(l => l.startsWith(`${name}=`))) {
    skip(`${name} already in ${envFile}`);
    return;
  }
  fs.appendFileSync(envFile, `${name}="${value}"\n`);
  ok(`${name} → ${envFile}`);
}

function printHelp() {
  process.stdout.write(`
memories-mcp — set up automatic memory hooks and MCP config

Usage:
  npx memories-mcp install   [--auto] [--claude] [--codex] [--cursor] [--openclaw] [--dry-run]
  npx memories-mcp uninstall [--auto] [--claude] [--codex] [--cursor] [--openclaw]

Options:
  --auto       Auto-detect installed tools (default)
  --claude     Install Claude Code hooks + MCP
  --codex      Install Codex hooks + MCP + developer instructions
  --cursor     Install Cursor hooks + MCP
  --openclaw   Install OpenClaw skill
  --dry-run    Print detected targets and exit without writing
  -h, --help   Show this help

Environment:
  MEMORIES_URL      Service URL (default: http://localhost:8900)
  MEMORIES_API_KEY  API key if auth is enabled

Examples:
  npx memories-mcp install
  npx memories-mcp install --claude --codex --cursor
  npx memories-mcp uninstall --cursor
`);
}

export async function run(argv) {
  let targetClaude    = false;
  let targetCodex     = false;
  let targetCursor    = false;
  let targetOpenclaw  = false;
  let explicitTargets = false;
  let autoDetect      = true;
  let uninstall       = false;
  let dryRun          = false;

  for (const arg of argv) {
    switch (arg) {
      case 'install':   break;
      case 'uninstall': uninstall = true; break;
      case '--auto':    autoDetect = true; break;
      case '--claude':  targetClaude   = true; explicitTargets = true; autoDetect = false; break;
      case '--codex':   targetCodex    = true; explicitTargets = true; autoDetect = false; break;
      case '--cursor':  targetCursor   = true; explicitTargets = true; autoDetect = false; break;
      case '--openclaw':targetOpenclaw = true; explicitTargets = true; autoDetect = false; break;
      case '--uninstall': uninstall = true; break;
      case '--dry-run': dryRun = true; break;
      case '-h': case '--help': printHelp(); return;
      default:
        process.stderr.write(`Unknown argument: ${arg}\n`);
        printHelp();
        process.exit(1);
    }
  }

  if (autoDetect && !explicitTargets) {
    if (fs.existsSync(path.join(HOME, '.claude')))   targetClaude   = true;
    if (fs.existsSync(path.join(HOME, '.codex')))    targetCodex    = true;
    if (fs.existsSync(path.join(HOME, '.cursor')))   targetCursor   = true;
    if (fs.existsSync(path.join(HOME, '.openclaw'))) targetOpenclaw = true;
  }

  // First-time fallback: at least install Claude
  if (!targetClaude && !targetCodex && !targetCursor && !targetOpenclaw) {
    targetClaude = true;
  }

  const targets = [
    targetClaude   && 'claude',
    targetCodex    && 'codex',
    targetCursor   && 'cursor',
    targetOpenclaw && 'openclaw',
  ].filter(Boolean);

  if (dryRun) {
    process.stdout.write(`targets=${targets.join(',')}\n`);
    process.stdout.write(`mode=${uninstall ? 'uninstall' : 'install'}\n`);
    return;
  }

  process.stdout.write(`\n${BLUE}Memories — Automatic Memory Layer Setup${NC}\n`);
  process.stdout.write(`${BLUE}============================================${NC}\n`);
  process.stdout.write(`Targets: ${GREEN}${targets.join(', ')}${NC}\n\n`);

  // ── Uninstall ──────────────────────────────────────────────────────────────
  if (uninstall) {
    if (targetClaude) {
      try { fs.rmSync(path.join(HOME, '.claude', 'hooks', 'memory'), { recursive: true }); ok('Removed Claude hooks'); }
      catch { warn('Claude hooks dir not found, skipping'); }
      const f = path.join(HOME, '.claude', 'settings.json');
      if (fs.existsSync(f)) {
        const s = readJson(f);
        if (s.mcpServers?.memories) {
          delete s.mcpServers.memories;
          if (!Object.keys(s.mcpServers).length) delete s.mcpServers;
          writeJson(f, s);
          ok(`Removed memories MCP from ${f}`);
        }
      }
    }
    if (targetCodex) {
      try { fs.rmSync(path.join(HOME, '.codex', 'hooks', 'memory'), { recursive: true }); ok('Removed Codex hooks'); }
      catch { warn('Codex hooks dir not found, skipping'); }
      const cfg = path.join(HOME, '.codex', 'config.toml');
      removeTomlBlock(cfg, 'Memories Codex MCP');
      removeTomlBlock(cfg, 'Memories Codex developer instructions');
    }
    if (targetCursor) {
      const f = path.join(HOME, '.cursor', 'mcp.json');
      if (fs.existsSync(f)) {
        const s = readJson(f);
        if (s.mcpServers?.memories) {
          delete s.mcpServers.memories;
          if (!Object.keys(s.mcpServers).length) delete s.mcpServers;
          writeJson(f, s);
          ok(`Removed memories MCP from ${f}`);
        }
      }
    }
    if (targetOpenclaw) {
      try { fs.rmSync(path.join(HOME, '.openclaw', 'skills', 'memories'), { recursive: true }); ok('Removed OpenClaw skill'); }
      catch { warn('OpenClaw skill dir not found, skipping'); }
    }
    process.stdout.write(`\n${GREEN}Uninstall complete.${NC}\n`);
    return;
  }

  // ── Health check ───────────────────────────────────────────────────────────
  const memoriesUrl    = process.env.MEMORIES_URL     || 'http://localhost:8900';
  const memoriesApiKey = process.env.MEMORIES_API_KEY || '';

  process.stdout.write(`[1/4] Checking Memories service at ${BLUE}${memoriesUrl}${NC}...\n`);
  const health = await checkHealth(memoriesUrl);
  if (!health) {
    fail(`Memories service not reachable at ${memoriesUrl}`);
    process.stdout.write('       Start it with: docker compose up -d\n');
    process.exit(1);
  }
  ok(`Healthy (${health.total_memories ?? 0} memories)\n`);

  // ── Extraction provider ────────────────────────────────────────────────────
  const hasHookTargets = targetClaude || targetCodex || targetCursor;
  let extractProvider = '';
  let extractEnvVars  = {};

  if (hasHookTargets && process.stdin.isTTY) {
    process.stdout.write('[2/4] Extraction provider (for automatic learning):\n');
    process.stdout.write('  1. Anthropic (recommended, ~$0.001/turn, full AUDN)\n');
    process.stdout.write('  2. OpenAI (~$0.001/turn, full AUDN)\n');
    process.stdout.write('  3. Ollama (free, local)\n');
    process.stdout.write('  4. Skip (retrieval only)\n\n');

    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const choice = (await ask(rl, '  > ')).trim();

    if (choice === '1') {
      extractProvider = 'anthropic';
      const key = (await ask(rl, '  Anthropic API key: ')).trim();
      if (!key) { fail('API key required'); rl.close(); process.exit(1); }
      extractEnvVars['ANTHROPIC_API_KEY'] = key;
    } else if (choice === '2') {
      extractProvider = 'openai';
      const key = (await ask(rl, '  OpenAI API key: ')).trim();
      if (!key) { fail('API key required'); rl.close(); process.exit(1); }
      extractEnvVars['OPENAI_API_KEY'] = key;
    } else if (choice === '3') {
      extractProvider = 'ollama';
    } else {
      warn('Extraction disabled (retrieval-only mode)');
    }
    rl.close();
  } else if (hasHookTargets) {
    process.stdout.write('[2/4] Non-interactive: skipping extraction provider prompt.\n');
  }
  process.stdout.write('\n');

  // ── Install targets ────────────────────────────────────────────────────────
  process.stdout.write('[3/4] Installing...\n');

  if (targetClaude) {
    installHooks('Claude', path.join(HOME, '.claude', 'hooks', 'memory'), path.join(HOME, '.claude', 'settings.json'));
    installMcpJson('Claude', path.join(HOME, '.claude', 'settings.json'), memoriesUrl, memoriesApiKey);
  }

  if (targetCodex) {
    const codexHooksDir = path.join(HOME, '.codex', 'hooks', 'memory');
    const codexSettings = path.join(HOME, '.codex', 'settings.json');
    const codexConfig   = path.join(HOME, '.codex', 'config.toml');

    installHooks('Codex', codexHooksDir, codexSettings, true);

    fs.mkdirSync(path.dirname(codexConfig), { recursive: true });
    if (!fs.existsSync(codexConfig)) fs.writeFileSync(codexConfig, '');

    const mcpBlock =
`[mcp_servers.memories]
command = "npx"
args = ["-y", "memories-mcp"]

[mcp_servers.memories.env]
MEMORIES_URL = "${memoriesUrl}"
MEMORIES_API_KEY = "${memoriesApiKey}"`;

    if (appendTomlBlock(codexConfig, 'Memories Codex MCP', mcpBlock)) {
      ok(`Added Codex MCP config in ${codexConfig}`);
    } else {
      skip('Codex MCP already configured');
    }

    const devInstr =
`developer_instructions = """
Use the Memories MCP tools as your memory layer with three responsibilities:

1. READ: Run memory_search before implementation-heavy responses or clarifying questions.
2. WRITE: Use memory_add for single clear facts (check memory_is_novel first). Use memory_extract for rich conversations, decision changes, or deferred work updates — it handles Add/Update/Delete/Noop automatically via AUDN.
3. MAINTAIN: Use memory_delete for explicit forget requests. memory_extract handles most lifecycle updates automatically.

Source prefixes: codex/{project} for decisions, learning/{project} for fixes, wip/{project} for deferred work.
"""`;

    if (appendTomlBlock(codexConfig, 'Memories Codex developer instructions', devInstr)) {
      ok(`Added Codex developer instructions in ${codexConfig}`);
    } else {
      skip('Codex developer instructions already configured');
    }
  }

  if (targetCursor) {
    // Cursor reads Claude Code's ~/.claude/settings.json via Third-party skills
    installHooks('Cursor', path.join(HOME, '.claude', 'hooks', 'memory'), path.join(HOME, '.claude', 'settings.json'));
    installMcpJson('Cursor', path.join(HOME, '.cursor', 'mcp.json'), memoriesUrl, memoriesApiKey);
    process.stdout.write(`\n  ${YELLOW}[ACTION REQUIRED]${NC} Enable third-party hooks in Cursor:\n`);
    process.stdout.write('  Settings → Features → Third-party skills → toggle ON\n');
    process.stdout.write('  Then restart Cursor.\n');
  }

  if (targetOpenclaw) {
    const skillDir = path.join(HOME, '.openclaw', 'skills', 'memories');
    fs.mkdirSync(skillDir, { recursive: true });
    fs.copyFileSync(OPENCLAW_SKILL_SRC, path.join(skillDir, 'SKILL.md'));
    ok(`Installed OpenClaw skill: ${skillDir}/SKILL.md`);
  }

  // ── Write env files ────────────────────────────────────────────────────────
  process.stdout.write('\n[4/4] Writing configuration...\n');

  const envDir  = path.join(HOME, '.config', 'memories');
  const envFile = path.join(envDir, 'env');
  fs.mkdirSync(envDir, { recursive: true });
  if (!fs.existsSync(envFile)) fs.writeFileSync(envFile, '');

  writeEnvVar(envFile, 'MEMORIES_URL', memoriesUrl);
  if (memoriesApiKey) writeEnvVar(envFile, 'MEMORIES_API_KEY', memoriesApiKey);
  if (extractProvider) writeEnvVar(envFile, 'EXTRACT_PROVIDER', extractProvider);
  for (const [k, v] of Object.entries(extractEnvVars)) writeEnvVar(envFile, k, v);

  // ── Summary ────────────────────────────────────────────────────────────────
  process.stdout.write(`\n${GREEN}Done.${NC}\n`);
  process.stdout.write(`Installed: ${BLUE}${targets.join(', ')}${NC}\n`);
  process.stdout.write(`Hook env:  ${BLUE}${envFile}${NC}\n`);

  if (extractProvider) {
    process.stdout.write(`\n`);
    note(`Set EXTRACT_PROVIDER=${extractProvider} in your docker-compose environment`);
    note(`(or .env file next to docker-compose.yml) to enable extraction in the service.`);
    for (const k of Object.keys(extractEnvVars)) {
      note(`Also set ${k} in the same file.`);
    }
  }
}
