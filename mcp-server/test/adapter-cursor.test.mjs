import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cursor from '../cli/adapters/cursor.mjs';
import { readJson, writeJson } from '../cli/lib/json-file.mjs';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../assets');
const exists = (p) => access(p).then(() => true, () => false);

test('cursor install: claude hooks + ~/.cursor/mcp.json + action-required log', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cur-'));
  const logs = [];
  const ctx = { home, assetsDir, url: 'http://localhost:8900', apiKey: '', dryRun: false, log: (m) => logs.push(m) };
  await cursor.install(ctx);
  assert.ok(await exists(join(home, '.claude/hooks/memory/memory-recall.sh')));
  const mcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(mcp.mcpServers.memories.command, 'npx');
  assert.ok(logs.some((l) => l.includes('Third-party')));
});

test('cursor uninstall removes only ~/.cursor/mcp.json entry', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cur-'));
  const ctx = { home, assetsDir, url: 'u', apiKey: '', dryRun: false, log: () => {} };
  await cursor.install(ctx);
  await cursor.uninstall(ctx);
  const mcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(mcp.mcpServers?.memories, undefined);
  assert.ok(await exists(join(home, '.claude/hooks/memory/memory-recall.sh'))); // untouched
});

test('cursor uninstall does not delete an unrelated npx-launched MCP entry named memories', async () => {
  // Regression test: `m.command === 'npx'` alone was too broad — any npx-run
  // server registered under the "memories" key, ours or not, got deleted.
  const home = await mkdtemp(join(tmpdir(), 'mem-cur-'));
  const ctx = { home, assetsDir, url: 'u', apiKey: '', dryRun: false, log: () => {} };
  await writeJson(join(home, '.cursor/mcp.json'), {
    mcpServers: { memories: { command: 'npx', args: ['-y', 'company-memories-proxy'] } },
  });
  await cursor.uninstall(ctx);
  const mcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(mcp.mcpServers.memories.command, 'npx');
  assert.deepEqual(mcp.mcpServers.memories.args, ['-y', 'company-memories-proxy']);
});

test('cursor uninstall preserves a foreign mcpServers.memories entry', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cur-'));
  const ctx = { home, assetsDir, url: 'u', apiKey: '', dryRun: false, log: () => {} };
  await writeJson(join(home, '.cursor/mcp.json'), { mcpServers: { memories: { command: 'node', args: ['/somewhere/else.js'] } } });
  await cursor.uninstall(ctx);
  const mcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(mcp.mcpServers.memories.command, 'node');
});
