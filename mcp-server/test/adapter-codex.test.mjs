import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, readdir, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as adapter from '../cli/adapters/codex.mjs';
import { readJson, writeJson } from '../cli/lib/json-file.mjs';
import { appendMarkedBlock } from '../cli/lib/toml.mjs';
import { READONLY_MCP_TOOL_NAMES } from '../cli/lib/hooks.mjs';

const assetsDir = join(dirname(fileURLToPath(import.meta.url)), '../assets');
const exists = (p) => access(p).then(() => true, () => false);

async function freshCtx() {
  const home = await mkdtemp(join(tmpdir(), 'mem-cx-'));
  return { home, assetsDir, url: 'http://localhost:8900', apiKey: 'k', dryRun: false, log: () => {} };
}

function rootPrefix(toml) {
  const lines = toml.split('\n');
  const firstSection = lines.findIndex((l) => /^\s*\[/.test(l));
  return lines.slice(0, firstSection === -1 ? lines.length : firstSection).join('\n');
}

const LEGACY_RULES = [
  ...READONLY_MCP_TOOL_NAMES.map((tool) => `mcp__memories__${tool}`),
  'mcp__memories__memory_is_useful',
];

const LEGACY_HOOK_ASSETS = [
  'memory-recall.sh',
  'memory-query.sh',
  'memory-extract.sh',
  'memory-observe.sh',
  'memory-guard.sh',
  'memory-codex-notify.sh',
];

test('install writes hooks, hooks.json, current approvals, and config.toml blocks', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  assert.ok(await exists(join(ctx.home, '.codex/hooks/memory/memory-recall.sh')));
  assert.ok(await exists(join(ctx.home, '.codex/hooks/memory/memory-codex-notify.sh')));
  const hooksJson = await readJson(join(ctx.home, '.codex/hooks.json'));
  const flat = JSON.stringify(hooksJson);
  assert.ok(flat.includes(join(ctx.home, '.codex/hooks/memory/')));
  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.equal(settings.permissions, undefined);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(toml.includes('# BEGIN Memories Codex MCP'));
  assert.ok(toml.includes('command = "npx"'));
  assert.ok(toml.includes('MEMORIES_CLIENT = "codex"'));
  assert.ok(toml.includes('MEMORIES_API_KEY = "k"'));
  assert.ok(toml.includes('default_tools_approval_mode = "prompt"'));
  for (const tool of READONLY_MCP_TOOL_NAMES) {
    assert.match(toml, new RegExp(`\\[mcp_servers\\.memories\\.tools\\.${tool}\\]\\napproval_mode = "approve"`));
  }
  for (const tool of ['memory_add', 'memory_delete', 'memory_delete_batch', 'memory_update', 'memory_extract', 'memory_is_useful']) {
    assert.doesNotMatch(toml, new RegExp(`mcp_servers\\.memories\\.tools\\.${tool}`));
  }
  assert.ok(toml.includes('# BEGIN Memories Codex developer instructions'));
  assert.ok(toml.includes('memory_search'));
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must be in the TOML root table, before any [section]',
  );
});

test('fresh install keeps developer instructions outside the MCP marker and preserves user edits on update', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);

  const configPath = join(ctx.home, '.codex/config.toml');
  const initial = await readFile(configPath, 'utf8');
  const mcpBegin = initial.indexOf('# BEGIN Memories Codex MCP');
  const mcpEnd = initial.indexOf('# END Memories Codex MCP');
  const devBegin = initial.indexOf('# BEGIN Memories Codex developer instructions');
  const devEnd = initial.indexOf('# END Memories Codex developer instructions');
  assert.ok(devBegin >= 0 && devEnd > devBegin);
  assert.ok(devBegin < mcpBegin, 'developer instructions must be a separate root block before MCP');
  assert.ok(devEnd < mcpBegin || devBegin > mcpEnd, 'developer marker must not be nested in MCP marker');

  const edited = initial.replace(
    'Source prefixes: replace {project} with the current working directory basename.',
    'USER EDIT: keep this instruction.\n\nSource prefixes: replace {project} with the current working directory basename.',
  );
  await writeFile(configPath, edited);
  await adapter.install(ctx);

  const updated = await readFile(configPath, 'utf8');
  assert.match(updated, /USER EDIT: keep this instruction/);
  const updatedMcpBegin = updated.indexOf('# BEGIN Memories Codex MCP');
  const updatedDevBegin = updated.indexOf('# BEGIN Memories Codex developer instructions');
  const updatedDevEnd = updated.indexOf('# END Memories Codex developer instructions');
  assert.ok(updatedDevBegin < updatedMcpBegin);
  assert.ok(updatedDevEnd < updatedMcpBegin || updatedDevBegin > updated.indexOf('# END Memories Codex MCP'));
});

test('install omits only the local API key when persistence is disabled', async () => {
  const ctx = await freshCtx();
  ctx.persistApiKey = false;
  ctx.apiKey = 'super-secret';
  await adapter.install(ctx);

  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.match(toml, /MEMORIES_URL = "http:\/\/localhost:8900"/);
  assert.match(toml, /MEMORIES_CLIENT = "codex"/);
  assert.doesNotMatch(toml, /MEMORIES_API_KEY/);
  assert.doesNotMatch(toml, /super-secret/);
});

test('install writes a direct remote MCP URL/OAuth block with current read-only approvals', async () => {
  const ctx = await freshCtx();
  ctx.mcpUrl = 'https://memory.example/mcp';
  ctx.apiKey = 'backend-key';
  await adapter.install(ctx);

  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.match(
    toml,
    /\[mcp_servers\.memories\]\nurl = "https:\/\/memory\.example\/mcp"\nauth = "oauth"\ndefault_tools_approval_mode = "prompt"/,
  );
  assert.doesNotMatch(toml, /command = /);
  assert.doesNotMatch(toml, /MEMORIES_(URL|API_KEY|CLIENT)/);
  for (const tool of READONLY_MCP_TOOL_NAMES) {
    assert.match(toml, new RegExp(`\\[mcp_servers\\.memories\\.tools\\.${tool}\\]\\napproval_mode = "approve"`));
  }
});

test('supports expanded Codex hooks only at the supported client threshold', () => {
  assert.equal(adapter.supportsExpandedHooks('codex-cli 0.146.0'), true);
  assert.equal(adapter.supportsExpandedHooks('codex-cli 0.145.9'), false);
  assert.equal(adapter.supportsExpandedHooks('unknown'), false);
});

test('install selects the expanded profile and renders every native lifecycle event', async () => {
  const ctx = await freshCtx();
  ctx.codexVersion = 'codex-cli 0.146.0';
  await adapter.install(ctx);

  const hooksJson = await readJson(join(ctx.home, '.codex/hooks.json'));
  assert.deepEqual(Object.keys(hooksJson.hooks).sort(), [
    'PostCompact', 'PostToolUse', 'PreCompact', 'PreToolUse',
    'SessionEnd', 'SessionStart', 'Stop', 'SubagentStart',
    'SubagentStop', 'UserPromptSubmit',
  ]);
  assert.equal(hooksJson.hooks.SessionEnd[0].hooks[0].timeout, 3);
  assert.equal(ctx.codexHookProfile, 'expanded');

  for (const name of [
    'memory-flush.sh',
    'memory-rehydrate.sh',
    'memory-subagent-recall.sh',
    'memory-subagent-capture.sh',
    'memory-commit.sh',
  ]) {
    assert.ok(await exists(join(ctx.home, '.codex/hooks/memory', name)), name);
  }

  const status = await adapter.status(ctx);
  assert.ok(status.details.some((detail) => detail.includes('expanded')));
});

test('install selects the five-event legacy profile for older or undetectable clients', async () => {
  const oldCtx = await freshCtx();
  oldCtx.codexVersion = 'codex-cli 0.145.9';
  await adapter.install(oldCtx);
  const oldHooks = await readJson(join(oldCtx.home, '.codex/hooks.json'));
  assert.deepEqual(Object.keys(oldHooks.hooks).sort(), [
    'PostToolUse', 'PreToolUse', 'SessionStart', 'Stop', 'UserPromptSubmit',
  ]);
  assert.equal(oldCtx.codexHookProfile, 'legacy');

  const unknownCtx = await freshCtx();
  unknownCtx.codexVersion = 'unknown';
  await adapter.install(unknownCtx);
  const unknownHooks = await readJson(join(unknownCtx.home, '.codex/hooks.json'));
  assert.deepEqual(Object.keys(unknownHooks.hooks).sort(), [
    'PostToolUse', 'PreToolUse', 'SessionStart', 'Stop', 'UserPromptSubmit',
  ]);
  assert.equal(unknownCtx.codexHookProfile, 'legacy');
});

test('install removes stale expanded lifecycle entries when downgrading to legacy', async () => {
  const ctx = await freshCtx();
  ctx.codexVersion = 'codex-cli 0.146.0';
  await adapter.install(ctx);
  ctx.codexVersion = 'codex-cli 0.145.9';
  await adapter.install(ctx);

  const hooksJson = await readJson(join(ctx.home, '.codex/hooks.json'));
  assert.deepEqual(Object.keys(hooksJson.hooks).sort(), [
    'PostToolUse', 'PreToolUse', 'SessionStart', 'Stop', 'UserPromptSubmit',
  ]);
});

test('install uses injectable Codex detection and fails closed on detection errors', async () => {
  const expandedCtx = await freshCtx();
  let expandedArgs;
  expandedCtx.execFileImpl = async (...args) => {
    expandedArgs = args;
    return { stdout: 'codex-cli 0.146.0' };
  };
  await adapter.install(expandedCtx);
  assert.deepEqual(expandedArgs, ['codex', ['--version']]);
  assert.equal(expandedCtx.codexHookProfile, 'expanded');

  const failedCtx = await freshCtx();
  failedCtx.execFileImpl = async () => { throw new Error('codex is unavailable'); };
  await adapter.install(failedCtx);
  assert.equal(failedCtx.codexHookProfile, 'legacy');
});

test('install preserves foreign hooks while merging the selected Codex profile', async () => {
  const ctx = await freshCtx();
  ctx.codexVersion = 'codex-cli 0.146.0';
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeJson(join(ctx.home, '.codex/hooks.json'), {
    hooks: {
      SessionStart: [{ matcher: 'foreign', hooks: [{ type: 'command', command: '/x/foreign.sh' }] }],
      CustomEvent: [{ matcher: '', hooks: [{ type: 'command', command: '/x/custom.sh' }] }],
    },
  });

  await adapter.install(ctx);

  const hooksJson = await readJson(join(ctx.home, '.codex/hooks.json'));
  assert.ok(JSON.stringify(hooksJson).includes('/x/foreign.sh'));
  assert.ok(JSON.stringify(hooksJson).includes('/x/custom.sh'));
  assert.equal(hooksJson.hooks.SessionStart.some((entry) => entry.matcher === 'foreign'), true);
  assert.ok(hooksJson.hooks.SessionStart.some((entry) => entry.hooks.some((hook) => hook.command.endsWith('/memory-recall.sh'))));
});

test('status does not classify a foreign PreCompact hook as expanded', async () => {
  const ctx = await freshCtx();
  ctx.codexVersion = 'codex-cli 0.145.9';
  await adapter.install(ctx);
  delete ctx.codexHookProfile;

  const hooksPath = join(ctx.home, '.codex/hooks.json');
  const hooksJson = await readJson(hooksPath);
  hooksJson.hooks.PreCompact = [{
    matcher: '',
    hooks: [{ type: 'command', command: '/foreign/precompact.sh' }],
  }];
  await writeJson(hooksPath, hooksJson);

  const status = await adapter.status(ctx);
  assert.ok(status.details.some((detail) => detail.includes('hook profile: legacy')));
});

test('status reports only explicit root Codex native-memory settings', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  const configPath = join(ctx.home, '.codex/config.toml');
  const config = `
# [features] memories = false
[profiles.team.features]
memories = false

[features] # exact root table
memories = true # enabled for native local cache

[managed.features]
memories = false

[profiles.team.memories]
disable_on_external_context = true

[memories] # exact root table
disable_on_external_context = false # optional dedupe recommendation
`;
  await writeFile(configPath, config);
  const before = await readFile(configPath, 'utf8');

  const status = await adapter.status(ctx);

  assert.ok(status.details.includes('native memories: enabled'));
  assert.ok(status.details.includes('external-context dedupe: disabled'));
  assert.equal(status.details.some((detail) => detail.includes('native memories: disabled')), false);
  assert.equal(status.details.some((detail) => detail.includes('external-context dedupe: enabled')), false);
  assert.equal(await readFile(configPath, 'utf8'), before);
});

test('status reports unset native settings without matching similarly named sections', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeFile(join(ctx.home, '.codex/config.toml'), `
[features.extra]
memories = true
[[features]]
memories = true
[memories.profile]
disable_on_external_context = true
[[memories]]
disable_on_external_context = true
`);

  const status = await adapter.status(ctx);

  assert.ok(status.details.includes('native memories: not explicitly configured'));
  assert.ok(status.details.includes('external-context dedupe: not explicitly configured'));
});

test('status preserves explicit false values in the exact root tables', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeFile(join(ctx.home, '.codex/config.toml'), `
 [features]   # root table with whitespace
 memories = false   # explicitly disabled
 [memories]
 disable_on_external_context = true # explicitly enabled
`);

  const status = await adapter.status(ctx);

  assert.ok(status.details.includes('native memories: disabled'));
  assert.ok(status.details.includes('external-context dedupe: enabled'));
});

test('status ignores root-looking assignments inside TOML multiline strings', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  const configPath = join(ctx.home, '.codex/config.toml');
  await writeFile(
    configPath,
    '[features]\n'
      + 'description = """embedded text [features]\n'
      + 'memories = false\n'
      + '"""\n'
      + "literal = '''embedded text [memories]\n"
      + 'disable_on_external_context = true\n'
      + "'''\n",
  );

  const embeddedOnly = await adapter.status(ctx);
  assert.ok(embeddedOnly.details.includes('native memories: not explicitly configured'));
  assert.ok(embeddedOnly.details.includes('external-context dedupe: not explicitly configured'));

  await writeFile(
    configPath,
    (await readFile(configPath, 'utf8'))
      + '\n[features]\nmemories = true\n\n[memories]\ndisable_on_external_context = false\n',
  );
  const status = await adapter.status(ctx);

  assert.ok(status.details.includes('native memories: enabled'));
  assert.ok(status.details.includes('external-context dedupe: disabled'));
});

test('install is idempotent on config.toml', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const snap = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  await adapter.install(ctx);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.equal(toml, snap);
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must still be in the TOML root table after a second install',
  );
});

test('install respects a pre-existing unmanaged [mcp_servers.memories]', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  const unmanaged = '[mcp_servers.memories]\ncommand = "node"\nenv = { KEEP = "byte-for-byte" }\n';
  await writeFile(join(ctx.home, '.codex/config.toml'), unmanaged);
  await adapter.install(ctx);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(!toml.includes('# BEGIN Memories Codex MCP')); // no duplicate block
  assert.ok(toml.includes(unmanaged), 'unmanaged MCP section must remain byte-for-byte unchanged');
  assert.doesNotMatch(toml, /MEMORIES_CLIENT = "codex"/);
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must land at root even when the file already starts with a [section] on line 1',
  );
});

test('install refreshes the owned MCP block and removes only recorded legacy settings permissions', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  const oldBody = `[mcp_servers.memories]\ncommand = "npx"\nargs = ["-y", "memories-mcp"]\n\n[mcp_servers.memories.env]\nMEMORIES_URL = "http://old.example"\nMEMORIES_API_KEY = "old"\nMEMORIES_CLIENT = "codex"`;
  await writeFile(
    join(ctx.home, '.codex/config.toml'),
    appendMarkedBlock('model = "gpt-5.5"\n', 'Memories Codex MCP', oldBody),
  );

  // Include the former feedback rule to prove recorded legacy cleanup still
  // removes it even though current approvals no longer include it.
  const legacyRules = [
    ...READONLY_MCP_TOOL_NAMES.map((tool) => `mcp__memories__${tool}`),
    'mcp__memories__memory_is_useful',
  ];
  const preservedRules = ['mcp__memories__memory_delete', 'mcp__other_memory_product__memory_search'];
  await writeJson(join(ctx.home, '.codex/settings.json'), {
    permissions: { allow: [...legacyRules, ...preservedRules] },
  });
  await writeJson(join(ctx.home, '.config/memories/install-state.json'), {
    permissions: { codex: legacyRules },
  });

  await adapter.install(ctx);

  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.doesNotMatch(toml, /http:\/\/old\.example/);
  assert.match(toml, /MEMORIES_URL = "http:\/\/localhost:8900"/);
  assert.match(toml, /default_tools_approval_mode = "prompt"/);
  for (const tool of READONLY_MCP_TOOL_NAMES) {
    assert.match(toml, new RegExp(`\\[mcp_servers\\.memories\\.tools\\.${tool}\\]\\napproval_mode = "approve"`));
  }

  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.deepEqual(settings.permissions.allow, preservedRules);
  assert.equal((await readJson(join(ctx.home, '.config/memories/install-state.json'))).permissions, undefined);
});

test('pre-manifest Codex update removes exact legacy rules when hook ownership evidence exists', async () => {
  const ctx = await freshCtx();
  const hooksDir = join(ctx.home, '.codex/hooks/memory');
  await mkdir(hooksDir, { recursive: true });
  for (const name of LEGACY_HOOK_ASSETS) await writeFile(join(hooksDir, name), '#!/bin/sh\n');
  const preserved = ['mcp__memories__memory_delete', 'mcp__other_memory_product__memory_search'];
  await writeJson(join(ctx.home, '.codex/settings.json'), {
    permissions: { allow: [...LEGACY_RULES, ...preserved] },
  });

  await adapter.install(ctx);

  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.deepEqual(settings.permissions.allow, preserved);
});

test('pre-manifest Codex cleanup stays untouched without hook ownership evidence', async () => {
  const ctx = await freshCtx();
  const preserved = ['mcp__memories__memory_delete', 'mcp__other_memory_product__memory_search'];
  await writeJson(join(ctx.home, '.codex/settings.json'), {
    permissions: { allow: [...LEGACY_RULES, ...preserved] },
  });

  await adapter.install(ctx);

  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.deepEqual(settings.permissions.allow, [...LEGACY_RULES, ...preserved]);
});

test('pre-manifest Codex uninstall removes exact legacy rules only with ownership evidence', async () => {
  const ctx = await freshCtx();
  const hooksDir = join(ctx.home, '.codex/hooks/memory');
  await mkdir(hooksDir, { recursive: true });
  for (const name of LEGACY_HOOK_ASSETS) await writeFile(join(hooksDir, name), '#!/bin/sh\n');
  const preserved = ['mcp__memories__memory_delete', 'mcp__other_memory_product__memory_search'];
  await writeJson(join(ctx.home, '.codex/settings.json'), {
    permissions: { allow: [...LEGACY_RULES, ...preserved] },
  });

  await adapter.uninstall(ctx);

  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.deepEqual(settings.permissions.allow, preserved);
});

test('pre-manifest Codex uninstall leaves legacy-looking rules untouched without ownership evidence', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  const preserved = ['mcp__memories__memory_delete', 'mcp__other_memory_product__memory_search'];
  await writeJson(join(ctx.home, '.codex/settings.json'), {
    permissions: { allow: [...LEGACY_RULES, ...preserved] },
  });

  await adapter.uninstall(ctx);

  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.deepEqual(settings.permissions.allow, [...LEGACY_RULES, ...preserved]);
});

test('install fails closed on malformed owned blocks before any mutation', async () => {
  const malformedBlocks = [
    ['Memories Codex notify', 'notify = true'],
    ['Memories Codex MCP', '[mcp_servers.memories]\ncommand = "npx"'],
    ['Memories Codex developer instructions', 'dev = true'],
  ].flatMap(([marker, body]) => [
    appendMarkedBlock('model = "gpt-5.5"\n', marker, `${body}\nforeign_after = 1`)
      .replace(`\n# END ${marker}\n`, '\n'),
    `# END ${marker}\nforeign_between = 2\n# BEGIN ${marker}\n${body}\nforeign_after = 3\n`,
    `# BEGIN ${marker}\none = true\n# END ${marker}\nforeign_between = 4\n# BEGIN ${marker}\ntwo = true\n# END ${marker}\nforeign_after = 5\n`,
  ]);

  for (const malformed of malformedBlocks) {
    const ctx = await freshCtx();
    const codexDir = join(ctx.home, '.codex');
    const hooksDir = join(codexDir, 'hooks/memory');
    const hooksJsonPath = join(codexDir, 'hooks.json');
    const configPath = join(codexDir, 'config.toml');
    const settingsPath = join(codexDir, 'settings.json');
    const statePath = join(ctx.home, '.config/memories/install-state.json');
    await mkdir(hooksDir, { recursive: true });
    await writeFile(join(hooksDir, 'foreign-hook.sh'), '#!/bin/sh\nexit 0\n');
    await writeJson(hooksJsonPath, {
      hooks: { Foreign: [{ hooks: [{ type: 'command', command: '/foreign/hook.sh' }] }] },
      keep: 'foreign',
    });
    await writeFile(configPath, malformed);

    const legacyRules = [
      ...READONLY_MCP_TOOL_NAMES.map((tool) => `mcp__memories__${tool}`),
      'mcp__memories__memory_is_useful',
    ];
    await writeJson(settingsPath, { permissions: { allow: legacyRules } });
    await writeJson(statePath, { permissions: { codex: legacyRules } });

    const beforeConfig = await readFile(configPath, 'utf8');
    const beforeSettings = await readJson(settingsPath);
    const beforeState = await readJson(statePath);
    const beforeHooksJson = await readFile(hooksJsonPath, 'utf8');
    const beforeHookNames = await readdir(hooksDir);
    const beforeHook = await readFile(join(hooksDir, 'foreign-hook.sh'), 'utf8');
    const logs = [];
    ctx.log = (message) => logs.push(message);
    await assert.rejects(() => adapter.install(ctx), /invalid marked block/i);

    assert.equal(await readFile(configPath, 'utf8'), beforeConfig);
    assert.deepEqual(await readJson(settingsPath), beforeSettings);
    assert.deepEqual(await readJson(statePath), beforeState);
    assert.equal(await readFile(hooksJsonPath, 'utf8'), beforeHooksJson);
    assert.deepEqual(await readdir(hooksDir), beforeHookNames);
    assert.equal(await readFile(join(hooksDir, 'foreign-hook.sh'), 'utf8'), beforeHook);
    assert.equal(ctx.codexHookProfile, undefined);
    assert.deepEqual(logs, []);
  }
});

test('uninstall fails closed on malformed owned markers before any cleanup', async () => {
  const malformedBlocks = [
    '# BEGIN Memories Codex developer instructions\ndev = true\nforeign_after = 1\n',
    '# END Memories Codex developer instructions\nforeign_between = 2\n# BEGIN Memories Codex developer instructions\ndev = true\nforeign_after = 3\n',
    '# BEGIN Memories Codex developer instructions\none = true\n# END Memories Codex developer instructions\nforeign_between = 4\n# BEGIN Memories Codex developer instructions\ntwo = true\n# END Memories Codex developer instructions\nforeign_after = 5\n',
  ];

  for (const malformedBlock of malformedBlocks) {
    const ctx = await freshCtx();
    const codexDir = join(ctx.home, '.codex');
    const hooksDir = join(codexDir, 'hooks/memory');
    const configPath = join(codexDir, 'config.toml');
    const settingsPath = join(codexDir, 'settings.json');
    const hooksJsonPath = join(codexDir, 'hooks.json');
    const statePath = join(ctx.home, '.config/memories/install-state.json');
    await mkdir(hooksDir, { recursive: true });
    await writeFile(join(hooksDir, 'foreign-hook.sh'), '#!/bin/sh\nexit 0\n');
    await writeJson(hooksJsonPath, { hooks: { Foreign: [{ hooks: [{ type: 'command', command: '/foreign/hook.sh' }] }] } });
    const validPrefix = appendMarkedBlock(
      appendMarkedBlock('foreign_before = 1\n', 'Memories Codex notify', 'notify = true'),
      'Memories Codex MCP',
      '[mcp_servers.memories]\ncommand = "npx"',
    );
    await writeFile(configPath, `${validPrefix}${malformedBlock}`);
    const recordedRules = ['mcp__memories__memory_search'];
    await writeJson(settingsPath, { permissions: { allow: recordedRules } });
    await writeJson(statePath, { permissions: { codex: recordedRules } });

    const beforeConfig = await readFile(configPath, 'utf8');
    const beforeSettings = await readFile(settingsPath, 'utf8');
    const beforeState = await readFile(statePath, 'utf8');
    const beforeHooksJson = await readFile(hooksJsonPath, 'utf8');
    const beforeHook = await readFile(join(hooksDir, 'foreign-hook.sh'), 'utf8');
    const beforeHookNames = await readdir(hooksDir);

    await assert.rejects(
      () => adapter.uninstall(ctx),
      (error) => {
        assert.equal(error.code, 'ERR_TOML_MARKED_BLOCK');
        return true;
      },
    );

    assert.equal(await readFile(configPath, 'utf8'), beforeConfig);
    assert.equal(await readFile(settingsPath, 'utf8'), beforeSettings);
    assert.equal(await readFile(statePath, 'utf8'), beforeState);
    assert.equal(await readFile(hooksJsonPath, 'utf8'), beforeHooksJson);
    assert.equal(await readFile(join(hooksDir, 'foreign-hook.sh'), 'utf8'), beforeHook);
    assert.deepEqual(await readdir(hooksDir), beforeHookNames);
    assert.equal(await exists(hooksDir), true);
  }
});

test('uninstall removes blocks and hooks but keeps foreign toml', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeFile(join(ctx.home, '.codex/config.toml'), 'model = "gpt-5.5"\n');
  await adapter.install(ctx);
  await adapter.uninstall(ctx);
  assert.equal(await exists(join(ctx.home, '.codex/hooks/memory')), false);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(toml.includes('model = "gpt-5.5"'));
  assert.ok(!toml.includes('Memories Codex'));
});

test('uninstall leaves current MCP approvals in config and does not create legacy settings permissions', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const afterInstall = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.equal(afterInstall.permissions, undefined);

  await adapter.uninstall(ctx);
  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.equal(settings.permissions, undefined);
});

test('uninstall preserves an unrelated memory product rule and an unmanaged machine', async () => {
  const foreignAllow = ['mcp__other_memory_product__memory_search', 'mcp__memories__memory_search'];

  // (a) never installed here — nothing may be removed
  const fresh = await freshCtx();
  await mkdir(join(fresh.home, '.codex'), { recursive: true });
  await writeJson(join(fresh.home, '.codex/settings.json'), { permissions: { allow: [...foreignAllow] } });
  await adapter.uninstall(fresh);
  assert.deepEqual(
    (await readJson(join(fresh.home, '.codex/settings.json'))).permissions.allow,
    foreignAllow,
  );

  // (b) installed here — only rules we introduced go
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await writeJson(join(ctx.home, '.codex/settings.json'), { permissions: { allow: [...foreignAllow] } });
  await adapter.install(ctx);
  await adapter.uninstall(ctx);
  assert.deepEqual(
    (await readJson(join(ctx.home, '.codex/settings.json'))).permissions.allow,
    foreignAllow, // both survive: neither was recorded as legacy installer state
  );
});

test('a failed uninstall keeps its ownership record so a retry still cleans up', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await adapter.install(ctx);
  const statePath = join(ctx.home, '.config/memories/install-state.json');
  const legacyRules = [
    ...READONLY_MCP_TOOL_NAMES.map((tool) => `mcp__memories__${tool}`),
    'mcp__memories__memory_is_useful',
  ];
  await writeJson(join(ctx.home, '.codex/settings.json'), { permissions: { allow: legacyRules } });
  await writeJson(statePath, { permissions: { codex: legacyRules } });
  assert.equal((await readJson(statePath)).permissions.codex.length, legacyRules.length);

  // Make uninstall throw partway through, after the point where provenance
  // used to be consumed.
  await writeFile(join(ctx.home, '.codex/hooks.json'), '{ not json');
  await assert.rejects(() => adapter.uninstall(ctx));

  // The record must survive the failure — the on-disk artifacts it would
  // otherwise be inferred from are already gone.
  assert.deepEqual((await readJson(statePath)).permissions.codex.length, legacyRules.length);

  await writeFile(join(ctx.home, '.codex/hooks.json'), '{}');
  await adapter.uninstall(ctx);

  assert.equal((await readJson(join(ctx.home, '.codex/settings.json'))).permissions, undefined);
  // Cleared only now that cleanup succeeded.
  assert.equal((await readJson(statePath)).permissions, undefined);
});
