import { test } from 'node:test';
import assert from 'node:assert/strict';
import { snippet } from '../cli/adapters/generic.mjs';

test('generic snippet is valid JSON with npx command', () => {
  const s = snippet({ url: 'http://localhost:8900', apiKey: 'k' });
  const parsed = JSON.parse(s);
  assert.equal(parsed.mcpServers.memories.command, 'npx');
  assert.deepEqual(parsed.mcpServers.memories.args, ['-y', 'memories-mcp']);
  assert.equal(parsed.mcpServers.memories.env.MEMORIES_API_KEY, 'k');
});
