import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendMarkedBlock, upsertMarkedBlock, insertMarkedBlockAtRoot, removeMarkedBlock, removeMarkedBlockStrict, hasTomlSection, hasTomlKey, ensureTomlStringKey, tomlEscape } from '../cli/lib/toml.mjs';

test('appendMarkedBlock appends once, idempotent', () => {
  const once = appendMarkedBlock('a = 1\n', 'Memories Codex MCP', '[mcp_servers.memories]\ncommand = "npx"');
  assert.ok(once.includes('# BEGIN Memories Codex MCP'));
  assert.ok(once.includes('# END Memories Codex MCP'));
  assert.equal(appendMarkedBlock(once, 'Memories Codex MCP', 'anything'), once);
});

test('upsertMarkedBlock replaces only the owned block', () => {
  const old = appendMarkedBlock('model = "x"\n', 'Owned', 'old = true');
  const next = upsertMarkedBlock(old, 'Owned', 'new = true');
  assert.match(next, /model = "x"/);
  assert.doesNotMatch(next, /old = true/);
  assert.match(next, /new = true/);
});

test('upsertMarkedBlock rejects incomplete or ambiguous ownership markers', () => {
  for (const text of [
    '# BEGIN Owned\nold = true\n',
    '# END Owned\nold = true\n',
    '# END Owned\n# BEGIN Owned\nold = true\n# END Owned\n',
    '# BEGIN Owned\none = true\n# END Owned\n# BEGIN Owned\ntwo = true\n# END Owned\n',
  ]) {
    assert.throws(
      () => upsertMarkedBlock(text, 'Owned', 'new = true'),
      (error) => {
        assert.equal(error.code, 'ERR_TOML_MARKED_BLOCK');
        assert.match(error.message, /invalid marked block/i);
        return true;
      },
    );
  }
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

test('removeMarkedBlockStrict removes complete blocks and rejects malformed ownership markers', () => {
  const complete = appendMarkedBlock('keep = 1\n', 'Owned', 'inner = 2');
  const removed = removeMarkedBlockStrict(`${complete}tail = 3\n`, 'Owned');
  assert.equal(removed, 'keep = 1\n\ntail = 3\n');
  assert.equal(removeMarkedBlockStrict('a = 1\n', 'Nope'), 'a = 1\n');

  for (const text of [
    '# BEGIN Owned\nold = true\nforeign = 1\n',
    'foreign = 1\n# END Owned\n# BEGIN Owned\n',
    '# BEGIN Owned\none = true\n# END Owned\nforeign = 2\n# BEGIN Owned\ntwo = true\n# END Owned\n',
  ]) {
    assert.throws(
      () => removeMarkedBlockStrict(text, 'Owned'),
      (error) => {
        assert.equal(error.code, 'ERR_TOML_MARKED_BLOCK');
        assert.match(error.message, /invalid marked block/i);
        return true;
      },
    );
  }
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

test('insertMarkedBlockAtRoot inserts before the first section', () => {
  const text = '[mcp_servers.memories]\ncommand = "npx"\n\n[other]\nz = 1\n';
  const out = insertMarkedBlockAtRoot(text, 'Dev Instructions', 'developer_instructions = "x"');
  const firstSectionIdx = out.split('\n').findIndex((l) => /^\s*\[/.test(l));
  const rootPart = out.split('\n').slice(0, firstSectionIdx).join('\n');
  assert.ok(rootPart.includes('developer_instructions'));
  assert.ok(out.indexOf('developer_instructions') < out.indexOf('[mcp_servers.memories]'));
});

test('insertMarkedBlockAtRoot ignores marker and section-looking lines in triple-quoted strings', () => {
  const original = [
    'description = """',
    'prose before marker',
    '# BEGIN foreign-looking',
    '[not_a_real_table]',
    'prose after marker',
    '"""',
    '',
    '[profiles.a]',
    'name = "x"',
    '',
  ].join('\n');
  const marker = '# BEGIN Owned\nowned = true\n# END Owned\n';
  const out = insertMarkedBlockAtRoot(original, 'Owned', 'owned = true');
  const actualFirstTable = out.indexOf('[profiles.a]');
  const inserted = out.indexOf(marker);
  assert.ok(inserted >= 0);
  assert.ok(inserted < actualFirstTable);
  assert.ok(inserted > out.lastIndexOf('"""') + 3, 'insertion must be after the closing delimiter');
  const firstTableLine = original.split('\n').findIndex((line) => line === '[profiles.a]');
  const expected = [
    ...original.split('\n').slice(0, firstTableLine),
    marker,
    ...original.split('\n').slice(firstTableLine),
  ].join('\n');
  assert.equal(out, expected, 'unmanaged bytes must remain unchanged around the insertion');
});

test('insertMarkedBlockAtRoot ignores marker and section-looking lines in triple-literal strings', () => {
  const original = [
    "description = '''",
    'prose before marker',
    '# BEGIN foreign-looking',
    '[not_a_real_table]',
    'prose after marker',
    "'''",
    '',
    '[profiles.a]',
    'name = "x"',
    '',
  ].join('\n');
  const marker = '# BEGIN Owned\nowned = true\n# END Owned\n';
  const out = insertMarkedBlockAtRoot(original, 'Owned', 'owned = true');
  const actualFirstTable = out.indexOf('[profiles.a]');
  const inserted = out.indexOf(marker);
  assert.ok(inserted >= 0);
  assert.ok(inserted < actualFirstTable);
  assert.ok(inserted > out.lastIndexOf("'''") + 3, 'insertion must be after the closing delimiter');
  const firstTableLine = original.split('\n').findIndex((line) => line === '[profiles.a]');
  const expected = [
    ...original.split('\n').slice(0, firstTableLine),
    marker,
    ...original.split('\n').slice(firstTableLine),
  ].join('\n');
  assert.equal(out, expected, 'unmanaged bytes must remain unchanged around the insertion');
});

test('insertMarkedBlockAtRoot appends when there are no sections', () => {
  const out = insertMarkedBlockAtRoot('a = 1\n', 'Dev Instructions', 'developer_instructions = "x"');
  assert.ok(out.includes('a = 1'));
  assert.ok(out.includes('developer_instructions'));
  assert.ok(!/^\s*\[/m.test(out.split('developer_instructions')[0].split('\n').pop() ?? ''));
});

test('insertMarkedBlockAtRoot is idempotent when the marker is already present', () => {
  const once = insertMarkedBlockAtRoot('[s]\nk = 1\n', 'Dev Instructions', 'developer_instructions = "x"');
  const twice = insertMarkedBlockAtRoot(once, 'Dev Instructions', 'developer_instructions = "y"');
  assert.equal(twice, once);
});

test('insertMarkedBlockAtRoot rejects malformed ownership markers', () => {
  assert.throws(
    () => insertMarkedBlockAtRoot('# BEGIN Dev Instructions\nforeign = true\n', 'Dev Instructions', 'developer_instructions = "x"'),
    (error) => {
      assert.equal(error.code, 'ERR_TOML_MARKED_BLOCK');
      return true;
    },
  );
});

test('hasTomlSection / hasTomlKey', () => {
  assert.ok(hasTomlSection('  [mcp_servers.memories]  \n', 'mcp_servers.memories'));
  assert.ok(hasTomlKey('developer_instructions = """x"""\n', 'developer_instructions'));
  assert.ok(!hasTomlKey('x = 1\n', 'developer_instructions'));
});
