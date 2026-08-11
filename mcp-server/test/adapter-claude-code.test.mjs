import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, access, mkdir, symlink, lstat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as adapter from '../cli/adapters/claude-code.mjs';
import { readJson, writeJson } from '../cli/lib/json-file.mjs';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../assets');
const exists = (p) => access(p).then(() => true, () => false);

async function freshCtx() {
  const home = await mkdtemp(join(tmpdir(), 'mem-cc-'));
  return { home, assetsDir, url: 'http://localhost:8900', apiKey: 'test-key', dryRun: false, log: () => {} };
}

test('install wires hooks, settings, skills, CLAUDE.md, env', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  assert.ok(await exists(join(ctx.home, '.claude/hooks/memory/memory-recall.sh')));
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'npx');
  assert.ok(settings.permissions.allow.includes('mcp__memories__memory_search'));
  const stopCmd = settings.hooks.Stop[0].hooks[0].command;
  assert.equal(stopCmd, join(ctx.home, '.claude/hooks/memory/memory-extract.sh'));
  assert.ok(await exists(join(ctx.home, '.claude/skills/memories/SKILL.md')));
  assert.ok(await exists(join(ctx.home, '.claude/skills/memories-setup/SKILL.md')));
  const claudeMd = await readFile(join(ctx.home, '.claude/CLAUDE.md'), 'utf8');
  assert.ok(claudeMd.includes('# BEGIN Memories Claude rules'));
  const env = await readFile(join(ctx.home, '.config/memories/env'), 'utf8');
  assert.ok(env.includes('MEMORIES_URL="http://localhost:8900"'));
  assert.ok(env.includes('MEMORIES_API_KEY="test-key"'));
});

test('install with ctx.mcpNames pre-approves read-only tools for every named server, deduped', async () => {
  const ctx = await freshCtx();
  ctx.mcpNames = ['memories', 'Remote_Memories', 'memories']; // duplicate on purpose
  await adapter.install(ctx);
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  const allow = settings.permissions.allow;
  assert.ok(allow.includes('mcp__memories__memory_search'));
  assert.ok(allow.includes('mcp__Remote_Memories__memory_search'));
  assert.ok(allow.includes('mcp__Remote_Memories__memory_conflicts'));
  // 7 default + 7 for Remote_Memories, no duplicates from the repeated name
  assert.equal(allow.filter((t) => t.startsWith('mcp__memories__') || t.startsWith('mcp__Remote_Memories__')).length, 14);
  assert.equal(new Set(allow).size, allow.length);
});

test('install without ctx.mcpNames falls back to the default "memories" server only', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  const allow = settings.permissions.allow;
  assert.equal(allow.length, 7);
  assert.ok(allow.every((t) => t.startsWith('mcp__memories__')));
});

test('install is idempotent — second run changes nothing', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const snap = await readFile(join(ctx.home, '.claude/settings.json'), 'utf8');
  const md = await readFile(join(ctx.home, '.claude/CLAUDE.md'), 'utf8');
  await adapter.install(ctx);
  assert.equal(await readFile(join(ctx.home, '.claude/settings.json'), 'utf8'), snap);
  assert.equal(await readFile(join(ctx.home, '.claude/CLAUDE.md'), 'utf8'), md);
});

test('install preserves an existing foreign mcpServers.memories entry', async () => {
  const ctx = await freshCtx();
  await writeJson(join(ctx.home, '.claude/settings.json'), { mcpServers: { memories: { command: 'node', args: ['/old/path.js'] } } });
  await adapter.install(ctx);
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'node'); // skip-if-exists preserved
});

test('install preserves a foreign Stop hook alongside the memories one', async () => {
  const ctx = await freshCtx();
  const foreign = { matcher: '', hooks: [{ type: 'command', command: '/user/own-hook.sh' }] };
  await writeJson(join(ctx.home, '.claude/settings.json'), { hooks: { Stop: [foreign] } });
  await adapter.install(ctx);
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  assert.ok(settings.hooks.Stop.some((e) => e.hooks[0].command === '/user/own-hook.sh'));
  assert.ok(settings.hooks.Stop.some((e) => e.hooks[0].command === join(ctx.home, '.claude/hooks/memory/memory-extract.sh')));
});

test('uninstall does not delete an unrelated npx-launched MCP entry named memories', async () => {
  // Regression test: `m.command === 'npx'` alone was too broad — any npx-run
  // server registered under the "memories" key, ours or not, got deleted.
  const ctx = await freshCtx();
  await writeJson(join(ctx.home, '.claude/settings.json'), {
    mcpServers: { memories: { command: 'npx', args: ['-y', 'company-memories-proxy'] } },
  });
  await adapter.uninstall(ctx);
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'npx');
  assert.deepEqual(settings.mcpServers.memories.args, ['-y', 'company-memories-proxy']);
});

test('status false on fresh home, true after install', async () => {
  const ctx = await freshCtx();
  assert.equal((await adapter.status(ctx)).installed, false);
  await adapter.install(ctx);
  assert.equal((await adapter.status(ctx)).installed, true);
});

test('uninstall reverses install but keeps foreign settings keys', async () => {
  const ctx = await freshCtx();
  await writeJson(join(ctx.home, '.claude/settings.json'), { model: 'opus' });
  await adapter.install(ctx);
  await adapter.uninstall(ctx);
  assert.equal(await exists(join(ctx.home, '.claude/hooks/memory')), false);
  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  assert.equal(settings.model, 'opus');
  assert.equal(settings.mcpServers?.memories, undefined);
  const hookCmds = JSON.stringify(settings.hooks ?? {});
  assert.ok(!hookCmds.includes('/hooks/memory/memory-'));
  assert.equal(await exists(join(ctx.home, '.claude/skills/memories')), false);
  const md = await readFile(join(ctx.home, '.claude/CLAUDE.md'), 'utf8');
  assert.ok(!md.includes('BEGIN Memories Claude rules'));
});

test('install copies skill content even when the asset files are symlinks', async () => {
  const ctx = await freshCtx();
  const linkRoot = await mkdtemp(join(tmpdir(), 'mem-link-'));
  await mkdir(join(linkRoot, 'claude-code/skills/memories'), { recursive: true });
  await mkdir(join(linkRoot, 'claude-code/skills/setup'), { recursive: true });
  await symlink(join(assetsDir, 'claude-code/skills/memories/SKILL.md'), join(linkRoot, 'claude-code/skills/memories/SKILL.md'));
  await symlink(join(assetsDir, 'claude-code/skills/setup/SKILL.md'), join(linkRoot, 'claude-code/skills/setup/SKILL.md'));
  await symlink(join(assetsDir, 'claude-code/hooks'), join(linkRoot, 'claude-code/hooks'));
  await symlink(join(assetsDir, 'claude-code/CLAUDE.md'), join(linkRoot, 'claude-code/CLAUDE.md'));
  await adapter.install({ ...ctx, assetsDir: linkRoot });
  const st = await lstat(join(ctx.home, '.claude/skills/memories/SKILL.md'));
  assert.equal(st.isSymbolicLink(), false);
  assert.ok((await readFile(join(ctx.home, '.claude/skills/memories/SKILL.md'), 'utf8')).length > 0);
});

test('uninstall clears read-only rules for every server name a past init wrote', async () => {
  const ctx = await freshCtx();
  await writeJson(join(ctx.home, '.claude/settings.json'), {
    permissions: { allow: ['Bash(ls)', 'mcp__memories__memory_delete'], deny: ['Bash(rm -rf /)'] },
  });
  // A past `init --mcp-name Remote_Memories` — uninstall is given no such flag.
  await adapter.install({ ...ctx, mcpNames: ['memories', 'Remote_Memories'] });

  const afterInstall = await readJson(join(ctx.home, '.claude/settings.json'));
  assert.ok(afterInstall.permissions.allow.includes('mcp__Remote_Memories__memory_search'));

  await adapter.uninstall(ctx);

  const settings = await readJson(join(ctx.home, '.claude/settings.json'));
  const allow = settings.permissions?.allow ?? [];
  assert.deepEqual(
    allow.filter((r) => r.startsWith('mcp__')),
    ['mcp__memories__memory_delete'], // pre-existing user rule, not ours to remove
  );
  assert.ok(allow.includes('Bash(ls)'));
  assert.deepEqual(settings.permissions.deny, ['Bash(rm -rf /)']);
});
