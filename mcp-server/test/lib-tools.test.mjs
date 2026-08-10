import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { buildServer } from '../lib-tools.mjs';

test('buildServer threads ctx.client into the X-Memories-Client header via fetchImpl', async () => {
  let seenHeaders;
  const fetchImpl = async (_url, opts) => {
    seenHeaders = opts.headers;
    return new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 });
  };

  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x', fetchImpl });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });

  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    await client.callTool({ name: 'memory_search', arguments: { query: 'hi' } });
    assert.equal(seenHeaders['X-Memories-Client'], 'x');
  } finally {
    await client.close();
    await server.close();
  }
});
