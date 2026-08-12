import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as adapter from '../cli/adapters/codex.mjs';
import { readJson, writeJson } from '../cli/lib/json-file.mjs';

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

test('install writes hooks, hooks.json, settings perms, config.toml blocks', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  assert.ok(await exists(join(ctx.home, '.codex/hooks/memory/memory-recall.sh')));
  assert.ok(await exists(join(ctx.home, '.codex/hooks/memory/memory-codex-notify.sh')));
  const hooksJson = await readJson(join(ctx.home, '.codex/hooks.json'));
  const flat = JSON.stringify(hooksJson);
  assert.ok(flat.includes(join(ctx.home, '.codex/hooks/memory/')));
  const settings = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.ok(settings.permissions.allow.includes('mcp__memories__memory_search'));
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(toml.includes('# BEGIN Memories Codex MCP'));
  assert.ok(toml.includes('command = "npx"'));
  assert.ok(toml.includes('MEMORIES_CLIENT = "codex"'));
  assert.ok(toml.includes('# BEGIN Memories Codex developer instructions'));
  assert.ok(toml.includes('memory_search'));
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must be in the TOML root table, before any [section]',
  );
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
  await writeFile(join(ctx.home, '.codex/config.toml'), '[mcp_servers.memories]\ncommand = "node"\n');
  await adapter.install(ctx);
  const toml = await readFile(join(ctx.home, '.codex/config.toml'), 'utf8');
  assert.ok(!toml.includes('# BEGIN Memories Codex MCP')); // no duplicate block
  assert.ok(toml.includes('command = "node"'));
  assert.ok(toml.includes('MEMORIES_CLIENT = "codex"')); // env key still ensured
  assert.ok(
    rootPrefix(toml).includes('developer_instructions'),
    'developer_instructions must land at root even when the file already starts with a [section] on line 1',
  );
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

test('uninstall clears the read-only allowlist it wrote', async () => {
  const ctx = await freshCtx();
  await adapter.install(ctx);
  const afterInstall = await readJson(join(ctx.home, '.codex/settings.json'));
  assert.ok(afterInstall.permissions.allow.includes('mcp__memories__memory_search'));

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
    foreignAllow, // both survive: one is a foreign server, one the user already had
  );
});

test('a failed uninstall keeps its ownership record so a retry still cleans up', async () => {
  const ctx = await freshCtx();
  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  await adapter.install(ctx);
  const statePath = join(ctx.home, '.config/memories/install-state.json');
  assert.equal((await readJson(statePath)).permissions.codex.length, 7);

  // Make uninstall throw partway through, after the point where provenance
  // used to be consumed.
  await writeFile(join(ctx.home, '.codex/hooks.json'), '{ not json');
  await assert.rejects(() => adapter.uninstall(ctx));

  // The record must survive the failure — the on-disk artifacts it would
  // otherwise be inferred from are already gone.
  assert.deepEqual((await readJson(statePath)).permissions.codex.length, 7);

  await writeFile(join(ctx.home, '.codex/hooks.json'), '{}');
  await adapter.uninstall(ctx);

  assert.equal((await readJson(join(ctx.home, '.codex/settings.json'))).permissions, undefined);
  // Cleared only now that cleanup succeeded.
  assert.equal((await readJson(statePath)).permissions, undefined);
});
