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
