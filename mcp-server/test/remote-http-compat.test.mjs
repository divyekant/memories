// Pins the HTTP replies of the remote server for empty bodies, wrong content
// types and odd query strings, so that a library update cannot change them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { createApp } from '../remote/server.mjs';
import { hashPassword } from '../remote/oauth.mjs';

const MCP_ACCEPT = 'application/json, text/event-stream';

async function startApp(authMode) {
  const app = createApp({
    issuer: 'http://issuer.invalid',
    authMode,
    passwordHash: hashPassword('pw'),
    tokenSecret: 'test-token-secret',
    storeDir: await mkdtemp(join(tmpdir(), 'mem-compat-')),
    memoriesUrl: 'http://unused.invalid',
    memoriesApiKey: '',
  });
  const server = app.listen(0);
  await new Promise((resolve) => server.once('listening', resolve));
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function call(baseUrl, method, path, init = {}) {
  const res = await fetch(baseUrl + path, { method, ...init });
  return { status: res.status, text: await res.text() };
}

const oauthCases = [
  ['POST', '/token', {}, 400, '{"error":"unsupported_grant_type"}'],
  ['POST', '/token', { headers: { 'content-type': 'text/plain' }, body: 'grant_type=refresh_token' }, 400, '{"error":"unsupported_grant_type"}'],
  ['POST', '/register', {}, 400, '{"error":"invalid_redirect_uri"}'],
  ['POST', '/register', { headers: { 'content-type': 'text/plain' }, body: '{}' }, 400, '{"error":"invalid_redirect_uri"}'],
  ['POST', '/authorize', {}, 400, 'Unknown client_id.'],
  ['GET', '/authorize?client_id[a]=b&response_type=code', {}, 400, 'Unknown client_id.'],
  ['GET', '/authorize?client_id=a&client_id=b', {}, 400, 'Unknown client_id.'],
  ['POST', '/mcp', { headers: { accept: MCP_ACCEPT } }, 401, '{"error":"invalid_token"}'],
];

for (const [method, path, init, status, text] of oauthCases) {
  test(`oauth mode: ${method} ${path} ${JSON.stringify(init)} -> ${status}`, async () => {
    const { baseUrl, close } = await startApp('oauth');
    try {
      assert.deepEqual(await call(baseUrl, method, path, init), { status, text });
    } finally {
      await close();
    }
  });
}

const noneCases = [
  [{ headers: { accept: MCP_ACCEPT } }, 415, -32000],
  [{ headers: { accept: MCP_ACCEPT, 'content-type': 'text/plain' }, body: 'x' }, 415, -32000],
  [{ headers: { accept: MCP_ACCEPT, 'content-type': 'application/json' }, body: '' }, 400, -32700],
];

for (const [init, status, code] of noneCases) {
  test(`none mode: POST /mcp ${JSON.stringify(init)} -> ${status}`, async () => {
    const { baseUrl, close } = await startApp('none');
    try {
      const r = await call(baseUrl, 'POST', '/mcp', init);
      assert.equal(r.status, status);
      assert.equal(JSON.parse(r.text).error.code, code);
    } finally {
      await close();
    }
  });
}

test('remote server exits non-zero and logs no "listening" line when the port is in use', async () => {
  const { spawn } = await import('node:child_process');
  const { createServer } = await import('node:net');
  const blocker = createServer().listen(0, '0.0.0.0');
  await new Promise((resolve) => blocker.once('listening', resolve));
  const port = blocker.address().port;
  try {
    const child = spawn(process.execPath, [new URL('../remote/server.mjs', import.meta.url).pathname], {
      env: {
        ...process.env,
        REMOTE_MCP_AUTH: 'none',
        REMOTE_MCP_PORT: String(port),
        REMOTE_MCP_STORE_DIR: await mkdtemp(join(tmpdir(), 'mem-compat-')),
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    const code = await new Promise((resolve) => child.once('exit', resolve));
    assert.notEqual(code, 0);
    assert.doesNotMatch(out, /listening on/);
    assert.match(out, /EADDRINUSE/);
  } finally {
    await new Promise((resolve) => blocker.close(resolve));
  }
});
