import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const pexec = promisify(execFile);
const pkgRoot = join(dirname(fileURLToPath(import.meta.url)), '..');

test('npm pack ships exactly what we intend', async () => {
  const { stdout } = await pexec('npm', ['pack', '--dry-run', '--json'], { cwd: pkgRoot });
  const files = JSON.parse(stdout)[0].files.map((f) => f.path);
  assert.ok(files.includes('index.js'));
  assert.ok(files.includes('cli/index.mjs'));
  assert.ok(files.includes('assets/claude-code/hooks/hooks.json'));
  assert.ok(files.includes('assets/backend/docker-compose.standalone.yml'));
  assert.ok(!files.some((f) => f.startsWith('test/')));
  assert.ok(!files.some((f) => f.includes('smoke')));
  assert.ok(!files.some((f) => f.includes('node_modules')));
});
