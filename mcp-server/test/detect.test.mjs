import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { detectAgents } from '../cli/detect.mjs';

test('detects nothing in empty home', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-home-'));
  assert.deepEqual(await detectAgents(home), { 'claude-code': false, codex: false, cursor: false });
});

test('detects each agent from its dir', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-home-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  await mkdir(join(home, '.codex'), { recursive: true });
  await mkdir(join(home, '.cursor'), { recursive: true });
  assert.deepEqual(await detectAgents(home), { 'claude-code': true, codex: true, cursor: true });
});
