import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, access, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkHealth, bootstrapBackend } from '../cli/backend.mjs';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../assets');
const exists = (p) => access(p).then(() => true, () => false);

test('checkHealth ok path', async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ total_memories: 42 }), { status: 200 });
  assert.deepEqual(await checkHealth('http://x', { fetchImpl }), { ok: true, totalMemories: 42 });
});

test('checkHealth failure path', async () => {
  const fetchImpl = async () => { throw new Error('ECONNREFUSED'); };
  const r = await checkHealth('http://x', { fetchImpl });
  assert.equal(r.ok, false);
  assert.match(r.error, /ECONNREFUSED/);
});

test('checkHealth non-2xx is failure even with JSON body', async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ detail: 'degraded' }), { status: 503 });
  const r = await checkHealth('http://x', { fetchImpl });
  assert.equal(r.ok, false);
  assert.match(r.error, /503/);
});

test('bootstrapBackend copies compose, writes env, runs docker compose, polls health', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-be-'));
  const calls = [];
  let healthy = false;
  const ctx = {
    home, assetsDir, url: 'http://localhost:8900', dryRun: false, log: () => {},
    extract: { provider: 'anthropic', keyVar: 'ANTHROPIC_API_KEY', keyVal: 'sk-test' },
    execImpl: async (cmd, args) => { calls.push([cmd, ...args]); healthy = true; return { stdout: '' }; },
    fetchImpl: async () => healthy
      ? new Response(JSON.stringify({ total_memories: 0 }), { status: 200 })
      : (() => { throw new Error('down'); })(),
    sleepImpl: async () => {},
  };
  const result = await bootstrapBackend(ctx);
  assert.equal(result.ok, true);
  assert.ok(await exists(join(home, '.config/memories/docker-compose.yml')));
  const env = await readFile(join(home, '.config/memories/env'), 'utf8');
  assert.ok(env.includes('EXTRACT_PROVIDER="anthropic"'));
  assert.ok(env.includes('ANTHROPIC_API_KEY="sk-test"'));
  assert.ok(calls.some((c) => c.join(' ').includes('compose') && c.includes('up')));
  const envPath = join(home, '.config/memories/env');
  assert.ok(calls.some((c) => c.includes('--env-file') && c[c.indexOf('--env-file') + 1] === envPath));
});

test('bootstrapBackend omits --env-file when no env file was created (no extract)', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-be-'));
  const calls = [];
  let healthy = false;
  const ctx = {
    home, assetsDir, url: 'http://localhost:8900', dryRun: false, log: () => {},
    execImpl: async (cmd, args) => { calls.push([cmd, ...args]); healthy = true; return { stdout: '' }; },
    fetchImpl: async () => healthy
      ? new Response(JSON.stringify({ total_memories: 0 }), { status: 200 })
      : (() => { throw new Error('down'); })(),
    sleepImpl: async () => {},
  };
  await bootstrapBackend(ctx);
  assert.ok(!calls.some((c) => c.includes('--env-file')));
});
