import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
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

test('parseArgs throws on trailing --url with no value', () => {
  assert.throws(() => parseArgs(['init', '--url']), /--url/);
  assert.throws(() => parseArgs(['init', '--url', '--yes']), /--url/);
});

test('parseArgs throws on trailing --api-key with no value', () => {
  assert.throws(() => parseArgs(['init', '--api-key']), /--api-key/);
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

test('init --yes with healthy backend logs a health line', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const logs = [];
  await run(['init', '--yes'], {
    home, log: (m) => logs.push(m),
    fetchImpl: async () => new Response(JSON.stringify({ total_memories: 3 }), { status: 200 }),
  });
  assert.ok(logs.some((l) => l.includes('Backend healthy') && l.includes('3')));
});

test('init interactive: declined bootstrap still wires adapters and prints manual steps', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const logs = [];
  const askImpl = async (question, { def } = {}) => (question.startsWith('Provision the backend') ? 'n' : (def ?? ''));
  const askChoiceImpl = async () => { throw new Error('askChoiceImpl should not be called when bootstrap is declined'); };
  await run(['init', '--claude'], {
    home, log: (m) => logs.push(m),
    fetchImpl: async () => { throw new Error('ECONNREFUSED'); },
    askImpl, askChoiceImpl,
  });
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'npx');
  assert.ok(logs.some((l) => l.includes('docker compose -f')));
  assert.ok(logs.some((l) => l.includes('memories doctor')));
});

test('init interactive: bootstrap accepted but docker fails — still wires adapters, does not throw', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const logs = [];
  const askImpl = async (question, { def } = {}) => def ?? ''; // accept provision (def 'Y'), accept url/apiKey defaults
  const askChoiceImpl = async (question, choices, { def }) => def; // 'skip'
  await run(['init', '--claude'], {
    home, log: (m) => logs.push(m),
    fetchImpl: async () => { throw new Error('ECONNREFUSED'); },
    execImpl: async () => { throw new Error('docker not found'); },
    askImpl, askChoiceImpl,
  });
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'npx');
  assert.ok(logs.some((l) => l.includes('Backend bootstrap failed')));
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

test('init --cursor then uninstall --cursor on a fresh home also tears down the shared claude-code wiring cursor created', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const opts = { home, log: () => {}, fetchImpl: async () => new Response('{"total_memories":0}', { status: 200 }) };
  await run(['init', '--cursor', '--yes'], opts);
  await run(['uninstall', '--cursor', '--yes'], opts);

  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers?.memories, undefined);
  await assert.rejects(readFile(join(home, '.claude/hooks/memory/memory-recall.sh')));
  const cursorMcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(cursorMcp.mcpServers?.memories, undefined);
});

test('init --claude --cursor then uninstall --cursor leaves the claude-code side intact', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const opts = { home, log: () => {}, fetchImpl: async () => new Response('{"total_memories":0}', { status: 200 }) };
  await run(['init', '--claude', '--cursor', '--yes'], opts);
  await run(['uninstall', '--cursor', '--yes'], opts);

  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers.memories.command, 'npx'); // claude-code side survives
  await assert.doesNotReject(readFile(join(home, '.claude/hooks/memory/memory-recall.sh')));
  const cursorMcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(cursorMcp.mcpServers?.memories, undefined); // cursor entry gone
});

test('bin symlink invocation reaches main (realpath guard)', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-bin-'));
  const link = join(dir, 'memories');
  await symlink(join(dirname(fileURLToPath(import.meta.url)), '../cli/index.mjs'), link);
  const { stdout } = await promisify(execFile)('node', [link, 'help']);
  assert.match(stdout, /init/); // usage text proves main() ran
});
