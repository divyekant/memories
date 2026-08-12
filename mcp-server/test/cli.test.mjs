import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, symlink, access, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { parseArgs, run, validateRemoteMcpUrl } from '../cli/index.mjs';
import { readJson, writeJson } from '../cli/lib/json-file.mjs';

const exists = (p) => access(p).then(() => true, () => false);

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

test('parseArgs accepts --mcp-url and rejects a missing value', () => {
  assert.equal(
    parseArgs(['init', '--codex', '--mcp-url', 'https://memory.example/mcp']).mcpUrl,
    'https://memory.example/mcp',
  );
  assert.throws(() => parseArgs(['init', '--mcp-url']), /Missing value/);
});

test('remote Codex init writes an OAuth URL block without contacting the REST backend', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  const logs = [];
  let healthCalls = 0;
  await run(['init', '--codex', '--mcp-url', 'https://memory.example/mcp', '--yes'], {
    home,
    log: (message) => logs.push(message),
    fetchImpl: async () => {
      healthCalls += 1;
      throw new Error('REST health/bootstrap must not run for --mcp-url');
    },
  });

  const config = await readFile(join(home, '.codex/config.toml'), 'utf8');
  assert.equal(healthCalls, 0);
  assert.match(config, /\[mcp_servers\.memories\]\nurl = "https:\/\/memory\.example\/mcp"\nauth = "oauth"/);
  assert.match(config, /default_tools_approval_mode = "prompt"/);
  assert.doesNotMatch(config, /command = /);
  assert.doesNotMatch(config, /MEMORIES_API_KEY/);
  assert.ok(logs.some((message) => message.includes('codex mcp login memories')));
});

test('remote MCP options are validated before dry-run or backend checks', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  let healthCalls = 0;
  const opts = {
    home,
    log: () => {},
    fetchImpl: async () => {
      healthCalls += 1;
      throw new Error('health should not run after validation failure');
    },
  };

  await assert.rejects(
    () => run(['init', '--codex', '--mcp-url', 'https://memory.example/mcp', '--url', 'http://localhost:8900'], opts),
    /--mcp-url cannot be combined with --url/,
  );
  await assert.rejects(
    () => run(['init', '--codex', '--mcp-url', 'https://memory.example/mcp', '--api-key', 'backend-key'], opts),
    /--mcp-url cannot be combined with --api-key/,
  );
  await assert.rejects(
    () => run(['init', '--claude', '--mcp-url', 'https://memory.example/mcp'], opts),
    /--mcp-url is only supported with --codex/,
  );
  assert.equal(healthCalls, 0);
});

async function snapshotRemoteSetupPaths(home) {
  const paths = [
    join(home, '.codex'),
    join(home, '.codex/config.toml'),
    join(home, '.codex/hooks.json'),
    join(home, '.codex/hooks/memory'),
    join(home, '.codex/hooks/memory/foreign.sh'),
    join(home, '.config/memories'),
    join(home, '.config/memories/state.json'),
  ];
  const snapshot = {};
  for (const path of paths) {
    try {
      snapshot[path] = { type: 'file', value: await readFile(path, 'utf8') };
    } catch (error) {
      if (error.code === 'EISDIR') {
        snapshot[path] = { type: 'directory', value: await readdir(path) };
      } else {
        snapshot[path] = { type: 'missing', value: error.code };
      }
    }
  }
  return snapshot;
}

test('invalid remote MCP URLs fail atomically before prompts, logs, health, or setup writes', async () => {
  const cases = [
    ['https://memory.example/mcp\nmalicious = true', /control character/i],
    ['https://memory.example/mcp\u0000malicious', /control character/i],
    ['https:memory.example/mcp', /canonical HTTPS URL/i],
    ['https:///memory.example/mcp', /canonical HTTPS URL/i],
    [String.raw`https:\memory.example\mcp`, /canonical HTTPS URL/i],
    ['memory.example/mcp', /absolute HTTPS URL/i],
    ['http://memory.example/mcp', /HTTPS/i],
    ['https://user:pass@memory.example/mcp', /credentials/i],
    ['https://memory.example/mcp#fragment', /fragment/i],
  ];

  for (const [mcpUrl, expectedError] of cases) {
    const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
    await mkdir(join(home, '.codex/hooks/memory'), { recursive: true });
    await mkdir(join(home, '.config/memories'), { recursive: true });
    await writeFile(join(home, '.codex/config.toml'), 'model = "gpt-5.5"\n');
    await writeFile(join(home, '.codex/hooks.json'), '{"foreign":true}\n');
    await writeFile(join(home, '.codex/hooks/memory/foreign.sh'), '#!/bin/sh\n');
    await writeFile(join(home, '.config/memories/state.json'), '{"installedTargets":["foreign"]}\n');
    const before = await snapshotRemoteSetupPaths(home);
    const logs = [];
    let healthCalls = 0;
    let promptCalls = 0;

    await assert.rejects(
      () => run(['init', '--codex', '--mcp-url', mcpUrl, '--yes'], {
        home,
        log: (message) => logs.push(message),
        askImpl: async () => { promptCalls += 1; return ''; },
        fetchImpl: async () => { healthCalls += 1; throw new Error('health should not run'); },
      }),
      expectedError,
    );

    assert.deepEqual(await snapshotRemoteSetupPaths(home), before, `setup artifacts changed for ${JSON.stringify(mcpUrl)}`);
    assert.deepEqual(logs, [], `logs should stay empty for ${JSON.stringify(mcpUrl)}`);
    assert.equal(promptCalls, 0, `prompts should stay untouched for ${JSON.stringify(mcpUrl)}`);
    assert.equal(healthCalls, 0, `health should stay untouched for ${JSON.stringify(mcpUrl)}`);
  }
});

test('validateRemoteMcpUrl accepts canonical HTTPS URLs with encoded paths and queries', () => {
  assert.doesNotThrow(() => validateRemoteMcpUrl('https://memory.example/mcp?scope=read%2Fonly&next=%2Fv1%2Fsearch'));
  assert.throws(() => validateRemoteMcpUrl('https:memory.example/mcp'), /canonical HTTPS URL/i);
  assert.throws(() => validateRemoteMcpUrl('https:///memory.example/mcp'), /canonical HTTPS URL/i);
  assert.throws(() => validateRemoteMcpUrl(String.raw`https:\memory.example\mcp`), /canonical HTTPS URL/i);
});

test('remote MCP value validation precedes the injectable Windows restriction log', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  const logs = [];
  await assert.rejects(
    () => run(['init', '--codex', '--mcp-url', 'https:memory.example/mcp', '--yes'], {
      home,
      platform: 'win32',
      log: (message) => logs.push(message),
    }),
    /canonical HTTPS URL/i,
  );
  assert.deepEqual(logs, []);
});

test('parseArgs collects repeatable --mcp-name into mcpNames', () => {
  assert.deepEqual(parseArgs(['init']).mcpNames, []);
  assert.deepEqual(parseArgs(['init', '--mcp-name', 'Remote_Memories']).mcpNames, ['Remote_Memories']);
  assert.deepEqual(
    parseArgs(['init', '--mcp-name', 'Remote_Memories', '--mcp-name', '843a7d55-4d6a-4efb-b73e-90428866e135']).mcpNames,
    ['Remote_Memories', '843a7d55-4d6a-4efb-b73e-90428866e135'],
  );
});

test('parseArgs throws on trailing --mcp-name with no value', () => {
  assert.throws(() => parseArgs(['init', '--mcp-name']), /--mcp-name/);
  assert.throws(() => parseArgs(['init', '--mcp-name', '--yes']), /--mcp-name/);
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

test('init --yes --mcp-name pre-approves read-only tools for the extra server too', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  await run(['init', '--yes', '--claude', '--mcp-name', 'Remote_Memories'], {
    home, log: () => {},
    fetchImpl: async () => new Response(JSON.stringify({ total_memories: 0 }), { status: 200 }),
  });
  const settings = await readJson(join(home, '.claude/settings.json'));
  const allow = settings.permissions.allow;
  assert.ok(allow.includes('mcp__memories__memory_search'), 'default server still pre-approved');
  assert.ok(allow.includes('mcp__Remote_Memories__memory_search'), 'named server pre-approved');
  assert.equal(new Set(allow).size, allow.length, 'no duplicate rules');
});

test('init --yes without --mcp-name only pre-approves the default server (unchanged default path)', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  await run(['init', '--yes', '--claude'], {
    home, log: () => {},
    fetchImpl: async () => new Response(JSON.stringify({ total_memories: 0 }), { status: 200 }),
  });
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.permissions.allow.length, 6);
  assert.ok(settings.permissions.allow.every((t) => t.startsWith('mcp__memories__')));
  assert.ok(!settings.permissions.allow.includes('mcp__memories__memory_is_useful'));
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

test('init --cursor adopts a pre-existing legacy claude-code install into ownership state, so uninstall --cursor does not delete it', async () => {
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  // Simulate a legacy install.sh/marketplace claude-code install that
  // predates state.json entirely: no state file, but real wiring present.
  await writeJson(join(home, '.claude/settings.json'), {
    mcpServers: { memories: { command: 'node', args: ['/old/path.js'] } },
  });
  await mkdir(join(home, '.claude/hooks/memory'), { recursive: true });
  await writeFile(join(home, '.claude/hooks/memory/memory-recall.sh'), '#!/bin/sh\necho legacy\n');

  const opts = { home, log: () => {}, fetchImpl: async () => new Response('{"total_memories":0}', { status: 200 }) };
  await run(['init', '--cursor', '--yes'], opts);

  const stateAfterInit = await readJson(join(home, '.config/memories/state.json'));
  assert.ok(stateAfterInit.installedTargets.includes('claude-code'));

  await run(['uninstall', '--cursor', '--yes'], opts);

  assert.ok(await exists(join(home, '.claude/hooks/memory'))); // claude side preserved
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.ok(settings.mcpServers?.memories); // claude side preserved
  const cursorMcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(cursorMcp.mcpServers?.memories, undefined); // cursor entry gone
});

test('repeat init/update --cursor does not adopt claude-side wiring cursor itself created', async () => {
  // Regression test: the Finding 6 preflight detected the cursor-created
  // claude-side files on a SECOND `init`/`update --cursor` run (since
  // claude-code was never explicitly tracked) and adopted 'claude-code' into
  // state, making a later `uninstall --cursor` treat the shared wiring as
  // independently claude-owned and leave it behind — the original bug back
  // for anyone who ran `update` once.
  const home = await mkdtemp(join(tmpdir(), 'mem-cli-'));
  await mkdir(join(home, '.claude'), { recursive: true });
  const opts = { home, log: () => {}, fetchImpl: async () => new Response('{"total_memories":0}', { status: 200 }) };

  await run(['init', '--cursor', '--yes'], opts);
  await run(['update', '--cursor', '--yes'], opts);

  const state = await readJson(join(home, '.config/memories/state.json'));
  assert.deepEqual(state.installedTargets, ['cursor']); // claude-code NOT adopted

  await run(['uninstall', '--cursor', '--yes'], opts);

  assert.equal(await exists(join(home, '.claude/hooks/memory')), false);
  assert.equal(await exists(join(home, '.claude/skills/memories')), false);
  const settings = await readJson(join(home, '.claude/settings.json'));
  assert.equal(settings.mcpServers?.memories, undefined);
  const cursorMcp = await readJson(join(home, '.cursor/mcp.json'));
  assert.equal(cursorMcp.mcpServers?.memories, undefined);
});

test('bin symlink invocation reaches main (realpath guard)', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-bin-'));
  const link = join(dir, 'memories');
  await symlink(join(dirname(fileURLToPath(import.meta.url)), '../cli/index.mjs'), link);
  const { stdout } = await promisify(execFile)('node', [link, 'help']);
  assert.match(stdout, /init/); // usage text proves main() ran
});
