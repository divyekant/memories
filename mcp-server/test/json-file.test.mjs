import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readJson, writeJson, mergeHookSettings, addPermissions, removePermissions, registerMcp } from '../cli/lib/json-file.mjs';
import { isReadonlyMcpRule } from '../cli/lib/hooks.mjs';

test('readJson returns fallback for missing file', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-'));
  assert.deepEqual(await readJson(join(dir, 'nope.json'), {}), {});
});

test('writeJson creates parent dirs and round-trips', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-'));
  const p = join(dir, 'a/b/settings.json');
  await writeJson(p, { x: 1 });
  assert.deepEqual(await readJson(p), { x: 1 });
  assert.ok((await readFile(p, 'utf8')).endsWith('\n'));
});

test('mergeHookSettings unions per-event, ours first, foreign preserved', () => {
  const foreign = { matcher: '', hooks: [{ type: 'command', command: '/user/own-hook.sh' }] };
  const oursOld = { matcher: '', hooks: [{ type: 'command', command: '/h/memory-extract.sh', timeout: 20 }] };
  const oursNew = { matcher: '', hooks: [{ type: 'command', command: '/h/memory-extract.sh', timeout: 30 }] };
  const existing = { model: 'opus', hooks: { Stop: [foreign, oursOld], SessionEnd: [{ matcher: '', hooks: [{ type: 'command', command: '/x/keep.sh' }] }] } };
  const merged = mergeHookSettings(existing, { hooks: { Stop: [oursNew], SessionStart: [{ matcher: '', hooks: [{ type: 'command', command: '/h/memory-recall.sh' }] }] } });
  assert.equal(merged.hooks.Stop.length, 2); // ours (refreshed) + foreign, deduped by command
  assert.equal(merged.hooks.Stop[0].hooks[0].timeout, 30); // rendered wins the dedupe
  assert.ok(merged.hooks.Stop.some((e) => e.hooks[0].command === '/user/own-hook.sh'));
  assert.equal(merged.hooks.SessionEnd[0].hooks[0].command, '/x/keep.sh');
  assert.equal(merged.hooks.SessionStart.length, 1);
  assert.equal(merged.model, 'opus');
});

test('mergeHookSettings preserves a foreign hook packed into the SAME matcher entry as ours', () => {
  // Regression test: dedup used to key on hooks[0].command only, so an
  // existing entry whose hooks array was [oursOld, foreignHook] got discarded
  // wholesale by the old entry, losing the foreign handler at index >= 1.
  const existing = {
    hooks: {
      Stop: [{
        matcher: '',
        hooks: [
          { type: 'command', command: '/h/memory-extract.sh' },
          { type: 'command', command: '/user/foreign.sh' },
        ],
      }],
    },
  };
  const rendered = {
    hooks: {
      Stop: [{ matcher: '', hooks: [{ type: 'command', command: '/h/memory-extract.sh', timeout: 30 }] }],
    },
  };
  const merged = mergeHookSettings(existing, rendered);
  assert.equal(merged.hooks.Stop.length, 2);
  assert.equal(merged.hooks.Stop[0].hooks[0].command, '/h/memory-extract.sh');
  assert.equal(merged.hooks.Stop[0].hooks[0].timeout, 30); // rendered wins
  assert.deepEqual(
    merged.hooks.Stop[1].hooks.map((h) => h.command),
    ['/user/foreign.sh'],
  ); // foreign hook survives, alone in its own entry
});

test('addPermissions unions and dedupes', () => {
  const s = addPermissions({ permissions: { allow: ['a'] } }, ['a', 'b']);
  assert.deepEqual(s.permissions.allow, ['a', 'b']);
});

test('registerMcp adds npx entry once, skips when present', () => {
  const first = registerMcp({}, { url: 'http://localhost:8900', apiKey: 'k' });
  assert.equal(first.skipped, false);
  assert.deepEqual(first.settings.mcpServers.memories.command, 'npx');
  assert.deepEqual(first.settings.mcpServers.memories.args, ['-y', 'memories-mcp']);
  assert.equal(first.settings.mcpServers.memories.env.MEMORIES_URL, 'http://localhost:8900');
  const second = registerMcp({ mcpServers: { memories: { command: 'node' } } }, { url: 'x', apiKey: '' });
  assert.equal(second.skipped, true);
  assert.equal(second.settings.mcpServers.memories.command, 'node'); // untouched
});

test('removePermissions drops matched rules and keeps the rest', () => {
  const s = removePermissions(
    { model: 'opus', permissions: { allow: ['Bash(ls)', 'mcp__memories__memory_search'], deny: ['Bash(rm)'] } },
    isReadonlyMcpRule,
  );
  assert.deepEqual(s.permissions.allow, ['Bash(ls)']);
  assert.deepEqual(s.permissions.deny, ['Bash(rm)']); // untouched
  assert.equal(s.model, 'opus');
});

test('removePermissions prunes empty containers instead of leaving {} / []', () => {
  const s = removePermissions({ permissions: { allow: ['mcp__memories__memory_search'] } }, isReadonlyMcpRule);
  assert.equal('permissions' in s, false);
  const kept = removePermissions(
    { permissions: { allow: ['mcp__memories__memory_search'], deny: ['x'] } },
    isReadonlyMcpRule,
  );
  assert.equal('allow' in kept.permissions, false);
  assert.deepEqual(kept.permissions.deny, ['x']);
});

test('removePermissions is a no-op when nothing matches or allow is absent', () => {
  const untouched = { permissions: { allow: ['Bash(ls)'] } };
  assert.equal(removePermissions(untouched, isReadonlyMcpRule), untouched); // same reference
  const noAllow = { permissions: { deny: ['x'] } };
  assert.equal(removePermissions(noAllow, isReadonlyMcpRule), noAllow);
  assert.equal(removePermissions({}, isReadonlyMcpRule).permissions, undefined);
});

test('isReadonlyMcpRule matches any server name but only our read-only tools', () => {
  for (const rule of [
    'mcp__memories__memory_search',
    'mcp__Remote_Memories__memory_stats',
    'mcp__843a7d55-4d6a-4efb-b73e-90428866e135__memory_is_novel',
  ]) assert.ok(isReadonlyMcpRule(rule), rule);

  for (const rule of [
    'mcp__memories__memory_delete',        // write tool — never ours to remove
    'mcp__memories__memory_update',
    'mcp__memories__*',                    // user-authored wildcard
    'mcp__memories__memory_search_extra',  // longer tool name
    'Bash(ls)',
    'memory_search',
  ]) assert.equal(isReadonlyMcpRule(rule), false, rule);
});
