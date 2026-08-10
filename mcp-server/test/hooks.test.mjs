import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, stat, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderHooksJson, copyHookScripts, READONLY_MCP_TOOLS } from '../cli/lib/hooks.mjs';

const ASSETS = join(dirname(fileURLToPath(import.meta.url)), '../assets/claude-code/hooks');

test('renderHooksJson rewrites every command to hooksDir/basename', async () => {
  const cfg = { hooks: { Stop: [{ matcher: '', hooks: [{ type: 'command', command: '${CLAUDE_PLUGIN_ROOT}/hooks/memory-extract.sh', timeout: 30 }] }] } };
  const out = renderHooksJson(cfg, '/home/u/.claude/hooks/memory');
  assert.equal(out.hooks.Stop[0].hooks[0].command, '/home/u/.claude/hooks/memory/memory-extract.sh');
  assert.equal(out.hooks.Stop[0].hooks[0].timeout, 30);
  assert.equal(cfg.hooks.Stop[0].hooks[0].command.includes('CLAUDE_PLUGIN_ROOT'), true); // input not mutated
});

test('renderHooksJson handles the real shipped hooks.json', async () => {
  const real = JSON.parse(await (await import('node:fs/promises')).readFile(join(ASSETS, 'hooks.json'), 'utf8'));
  const out = renderHooksJson(real, '/tmp/h');
  for (const arr of Object.values(out.hooks))
    for (const entry of arr)
      for (const h of entry.hooks)
        if (h.type === 'command') assert.ok(h.command.startsWith('/tmp/h/'), h.command);
});

test('copyHookScripts copies scripts + support files and sets exec bit', async () => {
  const dest = await mkdtemp(join(tmpdir(), 'mem-hooks-'));
  await copyHookScripts(ASSETS, dest);
  const names = await readdir(dest);
  assert.ok(names.includes('memory-recall.sh'));
  assert.ok(names.includes('_lib.sh'));
  assert.ok(names.includes('response-hints.json'));
  assert.ok(!names.includes('hooks.json')); // config is rendered, not copied
  const mode = (await stat(join(dest, 'memory-recall.sh'))).mode & 0o777;
  assert.equal(mode & 0o100, 0o100);
});

test('READONLY_MCP_TOOLS matches install.sh allowlist', () => {
  assert.equal(READONLY_MCP_TOOLS.length, 7);
  assert.ok(READONLY_MCP_TOOLS.includes('mcp__memories__memory_search'));
});
