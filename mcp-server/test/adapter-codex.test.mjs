import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as adapter from '../cli/adapters/codex.mjs';
import { readJson } from '../cli/lib/json-file.mjs';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../assets');
const exists = (p) => access(p).then(() => true, () => false);

async function freshCtx() {
  const home = await mkdtemp(join(tmpdir(), 'mem-cx-'));
  return { home, assetsDir, url: 'http://localhost:8900', apiKey: 'k', dryRun: false, log: () => {} };
}

function rootPrefix(toml) {
  const lines = toml.split('\n');
  const firstSection = lines.findIndex((l) => /^\s*\[/.test(l));
  return lines.slice(0, firstSection === -1 ? lines.length : firstSection).join('\n');
}

test('install writes hooks, hooks.json, settings perms, config.toml blocks', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  assert.ok(await exists(join(ctx.home, '.codex/hooks/memory/memory-recall.sh')));
  assert.ok(await exists(join(ctx.home, '.codex/hooks/memory/memory-codex-notify.sh')));
  const hooksJson = await readJson(join(ctx.home, '.codex/hooks.json'));
  const flat = JSON.stringify(hooksJson);
  assert.ok(flat.includes(join(ctx.home, '.codex/hooks/memory/')));
  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.ok(settings.permissions.allow.includes('mcp__memories__memory_search'));
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(toml.includes('# BEGIN Memories Codex MCP'));
  assert.ok(toml.includes('command = "npx"'));
  assert.ok(toml.includes('MEMORIES_CLIENT = "codex"'));
  assert.ok(toml.includes('# BEGIN Memories Codex developer instructions'));
  assert.ok(toml.includes('memory_search'));
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must be in the TOML root table, before any [section]',
  );
});

test('install is idempotent on config.toml', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const snap = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  await adapter.install(ctx);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.equal(toml, snap);
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must still be in the TOML root table after a second install',
  );
});

test('install respects a pre-existing unmanaged [mcp_servers.memories]', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeFile(join(ctx.home, '.codex/config.toml'), '[mcp_servers.memories]\ncommand = "node"\n');
  await adapter.install(ctx);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(!toml.includes('# BEGIN Memories Codex MCP')); // no duplicate block
  assert.ok(toml.includes('command = "node"'));
  assert.ok(toml.includes('MEMORIES_CLIENT = "codex"')); // env key still ensured
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must land at root even when the file already starts with a [section] on line 1',
  );
});

test('uninstall removes blocks and hooks but keeps foreign toml', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeFile(join(ctx.home, '.codex/config.toml'), 'model = "gpt-5.5"\n');
  await adapter.install(ctx);
  await adapter.uninstall(ctx);
  assert.equal(await exists(join(ctx.home, '.codex/hooks/memory')), false);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(toml.includes('model = "gpt-5.5"'));
  assert.ok(!toml.includes('Memories Codex'));
});

test('uninstall clears the read-only allowlist it wrote', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const afterInstall = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.ok(afterInstall.permissions.allow.includes('mcp__memories__memory_search'));

  await adapter.uninstall(ctx);
  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.equal(settings.permissions, undefined);
});
