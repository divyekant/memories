import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendMarkedBlock, removeMarkedBlock, hasTomlSection, hasTomlKey, ensureTomlStringKey, tomlEscape } from '../cli/lib/toml.mjs';

test('appendMarkedBlock appends once, idempotent', () => {
  const once = appendMarkedBlock('a = 1\n', 'Memories Codex MCP', '[mcp_servers.memories]\ncommand = "npx"');
  assert.ok(once.includes('# BEGIN Memories Codex MCP'));
  assert.ok(once.includes('# END Memories Codex MCP'));
  assert.equal(appendMarkedBlock(once, 'Memories Codex MCP', 'anything'), once);
});

test('removeMarkedBlock strips block and markers, keeps rest', () => {
  const text = 'keep = 1\n\n# BEGIN M\ninner = 2\n# END M\ntail = 3\n';
  const out = removeMarkedBlock(text, 'M');
  assert.ok(out.includes('keep = 1'));
  assert.ok(out.includes('tail = 3'));
  assert.ok(!out.includes('inner'));
  assert.ok(!out.includes('# BEGIN M'));
});

test('removeMarkedBlock no-op when marker absent', () => {
  assert.equal(removeMarkedBlock('a = 1\n', 'Nope'), 'a = 1\n');
});

test('ensureTomlStringKey inserts into existing section', () => {
  const text = '[mcp_servers.memories.env]\nMEMORIES_URL = "x"\n\n[other]\nz = 1\n';
  const out = ensureTomlStringKey(text, 'mcp_servers.memories.env', 'MEMORIES_CLIENT', 'codex');
  const section = out.split('[other]')[0];
  assert.ok(section.includes('MEMORIES_CLIENT = "codex"'));
});

test('ensureTomlStringKey leaves existing key alone', () => {
  const text = '[s]\nk = "old"\n';
  assert.equal(ensureTomlStringKey(text, 's', 'k', 'new'), text);
});

test('ensureTomlStringKey creates missing section at EOF', () => {
  const out = ensureTomlStringKey('a = 1\n', 's', 'k', 'v');
  assert.ok(out.endsWith('[s]\nk = "v"\n'));
});

test('tomlEscape escapes backslashes then quotes', () => {
  assert.equal(tomlEscape('a"b\\c'), 'a\\"b\\\\c'); // input a"b\c → a\"b\\c
  assert.equal(tomlEscape('plain'), 'plain');
});

test('hasTomlSection / hasTomlKey', () => {
  assert.ok(hasTomlSection('  [mcp_servers.memories]  \n', 'mcp_servers.memories'));
  assert.ok(hasTomlKey('developer_instructions = """x"""\n', 'developer_instructions'));
  assert.ok(!hasTomlKey('x = 1\n', 'developer_instructions'));
});
