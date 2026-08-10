import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readJson, writeJson, mergeHookSettings, addPermissions, registerMcp } from '../cli/lib/json-file.mjs';

test('readJson returns fallback for missing file', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-'));
  assert.deepEqual(await readJson(join(dir, 'nope.json'), {}), {});
});

test('writeJson creates parent dirs and round-trips', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-'));
  const p = join(dir, 'a/b/settings.json');
  await writeJson(p, { x: 1 });
  assert.deepEqual(await readJson(p), { x: 1 });
  assert.ok((await readFile(p, 'utf8')).endsWith('\n'));
});

test('mergeHookSettings replaces per-event, preserves other keys', () => {
  const existing = { model: 'opus', hooks: { Stop: [{ old: true }], SessionEnd: [{ keep: true }] } };
  const rendered = { hooks: { Stop: [{ new: true }], SessionStart: [{ added: true }] } };
  const merged = mergeHookSettings(existing, rendered);
  assert.deepEqual(merged.hooks.Stop, [{ new: true }]);
  assert.deepEqual(merged.hooks.SessionEnd, [{ keep: true }]);
  assert.deepEqual(merged.hooks.SessionStart, [{ added: true }]);
  assert.equal(merged.model, 'opus');
});

test('addPermissions unions and dedupes', () => {
  const s = addPermissions({ permissions: { allow: ['a'] } }, ['a', 'b']);
  assert.deepEqual(s.permissions.allow, ['a', 'b']);
});

test('registerMcp adds npx entry once, skips when present', () => {
  const first = registerMcp({}, { url: 'http://localhost:8900', apiKey: 'k' });
  assert.equal(first.skipped, false);
  assert.deepEqual(first.settings.mcpServers.memories.command, 'npx');
  assert.deepEqual(first.settings.mcpServers.memories.args, ['-y', 'memories-mcp']);
  assert.equal(first.settings.mcpServers.memories.env.MEMORIES_URL, 'http://localhost:8900');
  const second = registerMcp({ mcpServers: { memories: { command: 'node' } } }, { url: 'x', apiKey: '' });
  assert.equal(second.skipped, true);
  assert.equal(second.settings.mcpServers.memories.command, 'node'); // untouched
});
