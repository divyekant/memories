import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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

// ---------------------------------------------------------------------------
// skipFileConfig — ctx wins over .memories/backends.yaml (item 3)
// ---------------------------------------------------------------------------

test('buildServer skipFileConfig: ctx url/apiKey win over a .memories/backends.yaml in cwd', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-libtools-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(
    join(dir, '.memories', 'backends.yaml'),
    'backends:\n  bogus:\n    url: http://bogus-backend.invalid\n    api_key: nope\n'
  );

  const originalCwd = process.cwd();
  let seenUrl;
  const fetchImpl = async (url) => {
    seenUrl = String(url);
    return new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 });
  };

  process.chdir(dir);
  try {
    const server = buildServer({ url: 'http://ctx-wins.invalid', apiKey: '', client: 'x', skipFileConfig: true, fetchImpl });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const client = new Client({ name: 'test-client', version: '1.0.0' });
    await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
    try {
      await client.callTool({ name: 'memory_search', arguments: { query: 'hi' } });
    } finally {
      await client.close();
      await server.close();
    }
  } finally {
    process.chdir(originalCwd);
  }

  assert.match(seenUrl, /^http:\/\/ctx-wins\.invalid/, `expected request to go to ctx-wins host, got ${seenUrl}`);
});

// ---------------------------------------------------------------------------
// version — read from package.json instead of a hardcoded string (item 6)
// ---------------------------------------------------------------------------

test('buildServer default version equals package.json version (via MCP initialize serverInfo)', async () => {
  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
    assert.equal(client.getServerVersion().version, pkg.version);
  } finally {
    await client.close();
    await server.close();
  }
});

test('buildServer ctx.version overrides the package.json default', async () => {
  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x', version: '9.9.9-test' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    assert.equal(client.getServerVersion().version, '9.9.9-test');
  } finally {
    await client.close();
    await server.close();
  }
});
