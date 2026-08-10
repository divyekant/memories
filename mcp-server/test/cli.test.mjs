import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseArgs, run } from '../cli/index.mjs';
import { readJson } from '../cli/lib/json-file.mjs';

test('parseArgs defaults and flags', () => {
  assert.deepEqual(parseArgs(['init']).command, 'init');
  assert.deepEqual(parseArgs(['init', '--claude', '--codex']).targets, ['claude-code', 'codex']);
  assert.equal(parseArgs(['doctor', '--dry-run']).dryRun, true);
  assert.equal(parseArgs(['init', '--url', 'http://h:1']).url, 'http://h:1');
  assert.equal(parseArgs([]).command, 'help');
  assert.throws(() => parseArgs(['init', '--bogus']), /--bogus/);
});

test('init --yes wires detected agents end-to-end (healthy backend)', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const logs = [];
  await run(['init', '--yes', '--url', 'http://localhost:8900'], {
    home, log: (m) => logs.push(m),
    fetchImpl: async () => new Response(JSON.stringify({ total_memories: 1 }), { status: 200 }),
  });
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'npx');
});

test('init --dry-run writes nothing', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  await run(['init', '--dry-run'], { home, log: () => {} });
  await assert.rejects(readFile(join(home, '.claude/settings.json')));
});

test('double init --yes is idempotent', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const opts = { home, log: () => {}, fetchImpl: async () => new Response('{"total_memories":0}', { status: 200 }) };
  await run(['init', '--yes'], opts);
  const snap = await readFile(join(home, '.claude/settings.json'), 'utf8');
  await run(['init', '--yes'], opts);
  assert.equal(await readFile(join(home, '.claude/settings.json'), 'utf8'), snap);
});

test('uninstall --claude reverses init', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const opts = { home, log: () => {}, fetchImpl: async () => new Response('{"total_memories":0}', { status: 200 }) };
  await run(['init', '--yes'], opts);
  await run(['uninstall', '--claude', '--yes'], opts);
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers?.memories, undefined);
});
