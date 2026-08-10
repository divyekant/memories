import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ensureEnvVar } from '../cli/lib/env-file.mjs';

test('ensureEnvVar creates file, appends, skips existing', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-env-'));
  const f = join(dir, 'cfg/env');
  assert.deepEqual(await ensureEnvVar(f, 'MEMORIES_URL', 'http://localhost:8900'), { added: true });
  assert.deepEqual(await ensureEnvVar(f, 'MEMORIES_URL', 'http://other'), { added: false });
  const body = await readFile(f, 'utf8');
  assert.equal(body, 'MEMORIES_URL="http://localhost:8900"\n');
});
