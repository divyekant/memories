import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
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
  assert.ok(files.includes('assets/codex/hooks/hooks.json'));
  assert.ok(files.includes('assets/codex/hooks/hooks.legacy.json'));
  for (const name of [
    'memory-flush.sh',
    'memory-rehydrate.sh',
    'memory-subagent-recall.sh',
    'memory-subagent-capture.sh',
    'memory-commit.sh',
  ]) {
    assert.ok(files.includes(`assets/codex/hooks/${name}`), name);
  }
  assert.ok(files.includes('assets/backend/docker-compose.standalone.yml'));
  assert.ok(files.includes('lib-tools.mjs'));
  assert.ok(files.includes('remote/server.mjs'));
  assert.ok(files.includes('remote/oauth.mjs'));
  assert.ok(files.includes('remote/store.mjs'));
  assert.ok(files.includes('remote/login.mjs'));
  assert.ok(!files.some((f) => f.startsWith('test/')));
  assert.ok(!files.some((f) => f.includes('smoke')));
  assert.ok(!files.some((f) => f.includes('node_modules')));
});

test('npm README documents current local and remote Codex setup', async () => {
  const readme = await readFile(join(pkgRoot, 'README.md'), 'utf8');
  assert.match(readme, /npx memories-mcp init --codex --yes/);
  assert.match(readme, /--mcp-url https:\/\/memory\.example\/mcp/);
  assert.match(readme, /codex mcp login memories/);
  assert.match(readme, /--no-persist-api-key/);
  assert.match(readme, /at or above `0\.146\.0`/);
  assert.match(readme, /ten-event\s+lifecycle\s+profile/);
  assert.match(readme, /older or unparseable versions use the compatible five-event profile/);
});
