import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

import { createApp, createRateLimiter, detectRemoteClient, validateAuthMode, validateIssuerScheme } from '../remote/server.mjs';
import { hashPassword } from '../remote/oauth.mjs';
import { createStore } from '../remote/store.mjs';

const MCP_ACCEPT = 'application/json, text/event-stream';

async function freshStoreDir() {
  return mkdtemp(join(tmpdir(), 'mem-remote-'));
}

// Starts an app on an ephemeral port and returns { baseUrl, close }.
async function startApp(cfg) {
  const app = createApp(cfg);
  const server = app.listen(0);
  await new Promise((resolve) => server.once('listening', resolve));
  const port = server.address().port;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function noneModeApp(fetchImpl) {
  const storeDir = await freshStoreDir();
  return startApp({
    issuer: 'http://issuer.invalid',
    authMode: 'none',
    storeDir,
    memoriesUrl: 'http://unused.invalid',
    memoriesApiKey: '',
    fetchImpl,
  });
}

async function oauthModeApp(fetchImpl) {
  const storeDir = await freshStoreDir();
  const passwordHash = hashPassword('s3cret-pw');
  const tokenSecret = 'test-token-secret';
  const { baseUrl, close } = await startApp({
    issuer: 'http://issuer.invalid',
    authMode: 'oauth',
    passwordHash,
    tokenSecret,
    storeDir,
    memoriesUrl: 'http://unused.invalid',
    memoriesApiKey: '',
    fetchImpl,
  });
  return { baseUrl, close, storeDir, passwordHash, tokenSecret };
}

function jsonRpc(method, params, id = 1) {
  return { jsonrpc: '2.0', id, method, params };
}

async function mcpFetch(baseUrl, body, extraHeaders = {}) {
  return fetch(`${baseUrl}/mcp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: MCP_ACCEPT, ...extraHeaders },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// authMode validation — fails closed instead of open (item 1)
// ---------------------------------------------------------------------------

test('validateAuthMode: rejects anything other than "oauth" or "none"', () => {
  assert.throws(() => validateAuthMode('Oauth'), /REMOTE_MCP_AUTH/);
  assert.throws(() => validateAuthMode(''), /REMOTE_MCP_AUTH/);
  assert.throws(() => validateAuthMode(undefined), /REMOTE_MCP_AUTH/);
  assert.doesNotThrow(() => validateAuthMode('oauth'));
  assert.doesNotThrow(() => validateAuthMode('none'));
});

test('createApp: invalid authMode throws instead of failing open', async () => {
  const storeDir = await freshStoreDir();
  assert.throws(() => createApp({ authMode: 'Oauth', storeDir }), /REMOTE_MCP_AUTH/);
});

test("createApp: authMode 'none' logs a loud console.warn that auth is disabled", async () => {
  const originalWarn = console.warn;
  let warned = '';
  console.warn = (...args) => { warned += args.join(' '); };
  try {
    createApp({ authMode: 'none', storeDir: await freshStoreDir() });
  } finally {
    console.warn = originalWarn;
  }
  assert.match(warned, /AUTH DISABLED/);
  assert.match(warned, /REMOTE_MCP_AUTH=none/);
});

// ---------------------------------------------------------------------------
// Issuer fail-fast (item 5)
// ---------------------------------------------------------------------------

test('createApp: oauth mode without an issuer throws', async () => {
  const storeDir = await freshStoreDir();
  assert.throws(
    () => createApp({ authMode: 'oauth', passwordHash: 'x', tokenSecret: 'y', storeDir }),
    /issuer/i
  );
});

test('createApp: oauth mode with an issuer does not throw on the missing-issuer check', async () => {
  assert.doesNotThrow(() => createApp({
    authMode: 'oauth',
    issuer: 'http://issuer.invalid',
    passwordHash: 'x',
    tokenSecret: 'y',
    storeDir: 'unused-in-this-assertion',
  }));
});

// PR 83 follow-up #3, MINOR 5: mirrors main()'s existing
// REMOTE_MCP_TOKEN_SECRET pre-flight check — tokenSecret signs every access
// token (createHmac throws on a missing/empty key), so a direct createApp()
// call with it omitted should fail fast at construction time instead of
// surfacing as a confusing 500 the first time a token is issued.
test('createApp: oauth mode without a tokenSecret throws', async () => {
  const storeDir = await freshStoreDir();
  assert.throws(
    () => createApp({ authMode: 'oauth', issuer: 'http://issuer.invalid', passwordHash: 'x', storeDir }),
    /tokenSecret/i
  );
});

test('createApp: authMode "none" does not require a tokenSecret', async () => {
  assert.doesNotThrow(() => createApp({ authMode: 'none', storeDir: 'unused-in-this-assertion' }));
});

test('validateIssuerScheme: https issuer is fine', () => {
  assert.doesNotThrow(() => validateIssuerScheme('https://mcp.example.com'));
});

test('validateIssuerScheme: http issuer on a non-localhost host throws without override', () => {
  assert.throws(() => validateIssuerScheme('http://mcp.example.com'), /https/i);
});

test('validateIssuerScheme: http issuer on localhost/127.0.0.1 is allowed', () => {
  assert.doesNotThrow(() => validateIssuerScheme('http://localhost:8910'));
  assert.doesNotThrow(() => validateIssuerScheme('http://127.0.0.1:8910'));
});

test('validateIssuerScheme: http issuer on a non-localhost host is allowed with allowInsecure override', () => {
  assert.doesNotThrow(() => validateIssuerScheme('http://mcp.example.com', { allowInsecure: true }));
});

test('validateIssuerScheme: not-a-url throws', () => {
  assert.throws(() => validateIssuerScheme('not-a-url'));
});

test('detectRemoteClient uses conservative case-insensitive precedence for telemetry', () => {
  assert.equal(
    detectRemoteClient({ headers: { 'User-Agent': 'CoDeX-cli/0.146.0', Origin: 'https://claude.ai' } }),
    'codex',
  );
  assert.equal(detectRemoteClient({ headers: { Origin: 'https://claude.ai' } }), 'claude-web');
  assert.equal(detectRemoteClient({ headers: { 'user-agent': 'Claude Desktop/1.0' } }), 'claude-web');
  assert.equal(detectRemoteClient({ headers: { 'User-Agent': 'generic-mcp-client/1.0' } }), 'remote-mcp');
  assert.equal(detectRemoteClient({ headers: { Origin: 'https://claude.ai.evil.example' } }), 'remote-mcp');
  assert.equal(detectRemoteClient({ headers: { Origin: 'http://claude.ai' } }), 'remote-mcp');
  assert.equal(detectRemoteClient({ headers: { Origin: 'https://evil.example', 'User-Agent': 'codex-cli/0.146.0' } }), 'codex');
});

// ---------------------------------------------------------------------------
// /healthz
// ---------------------------------------------------------------------------

test('/healthz returns ok shape', async () => {
  const { baseUrl, close } = await startApp({ authMode: 'none', storeDir: await freshStoreDir() });
  try {
    const res = await fetch(`${baseUrl}/healthz`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.status, 'ok');
    assert.equal(body.service, 'memories-remote-mcp');
    assert.ok(body.version);
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// /mcp — auth mode 'none'
// ---------------------------------------------------------------------------

test('none mode: POST /mcp initialize succeeds without a token, serverInfo.name present', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('initialize', {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'test-client', version: '1.0.0' },
    }));
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.result.serverInfo.name, 'memories');
  } finally {
    await close();
  }
});

test('none mode: tools/list includes memory_search', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}));
    assert.equal(res.status, 200);
    const body = await res.json();
    const names = body.result.tools.map((t) => t.name);
    assert.ok(names.includes('memory_search'), `expected memory_search in ${names.join(', ')}`);
  } finally {
    await close();
  }
});

test('none mode: tools/call memory_stats hits injected fetchImpl backend', async () => {
  let called = false;
  const fetchImpl = async (url) => {
    called = true;
    assert.ok(String(url).endsWith('/stats'));
    return new Response(JSON.stringify({
      total_memories: 42,
      model: 'test-model',
      dimension: 768,
      index_size_bytes: 1024,
      backup_count: 1,
      last_updated: '2026-08-01',
    }), { status: 200 });
  };
  const { baseUrl, close } = await noneModeApp(fetchImpl);
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/call', { name: 'memory_stats', arguments: {} }));
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.ok(called, 'expected fetchImpl to be invoked');
    assert.match(body.result.content[0].text, /Total memories: 42/);
  } finally {
    await close();
  }
});

test('none mode: remote client attribution is telemetry-only and preserves authorization outcome', async () => {
  const backendCalls = [];
  const fetchImpl = async (url, options) => {
    backendCalls.push({ url, headers: options.headers });
    return new Response(JSON.stringify({
      total_memories: 42,
      model: 'test-model',
      dimension: 768,
      index_size_bytes: 1024,
      backup_count: 1,
      last_updated: '2026-08-01',
    }), { status: 200 });
  };
  const { baseUrl, close } = await noneModeApp(fetchImpl);
  try {
    const variants = [
      { 'User-Agent': 'codex-cli/0.146.0' },
      { Origin: 'https://claude.ai' },
      { 'User-Agent': 'generic-mcp-client/1.0' },
    ];
    for (const headers of variants) {
      const res = await mcpFetch(baseUrl, jsonRpc('tools/call', { name: 'memory_stats', arguments: {} }), headers);
      assert.equal(res.status, 200);
    }
    assert.deepEqual(
      backendCalls.map(({ headers }) => headers['X-Memories-Client']),
      ['codex', 'claude-web', 'remote-mcp'],
    );
  } finally {
    await close();
  }
});

test('oauth mode: authenticated client attribution preserves authorization and forwards telemetry only', async () => {
  const backendCalls = [];
  const fetchImpl = async (url, options) => {
    backendCalls.push({ url, headers: options.headers });
    return new Response(JSON.stringify({
      total_memories: 42,
      model: 'test-model',
      dimension: 768,
      index_size_bytes: 1024,
      backup_count: 1,
      last_updated: '2026-08-01',
    }), { status: 200 });
  };
  const { baseUrl, close } = await oauthModeApp(fetchImpl);
  try {
    const registerRes = await fetch(`${baseUrl}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ redirect_uris: ['https://claude.ai/api/mcp/callback'], client_name: 'Parity test' }),
    });
    assert.equal(registerRes.status, 201);
    const client = await registerRes.json();
    const verifier = Buffer.from('oauth-attribution-verifier-long-enough').toString('base64url');
    const challenge = createHash('sha256').update(verifier).digest('base64url');
    const authorizeRes = await fetch(`${baseUrl}/authorize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      redirect: 'manual',
      body: new URLSearchParams({
        password: 's3cret-pw',
        client_id: client.client_id,
        redirect_uri: client.redirect_uris[0],
        state: 'attribution-state',
        code_challenge: challenge,
        code_challenge_method: 'S256',
      }),
    });
    assert.equal(authorizeRes.status, 302);
    const code = new URL(authorizeRes.headers.get('location')).searchParams.get('code');
    assert.ok(code);
    const tokenRes = await fetch(`${baseUrl}/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code,
        redirect_uri: client.redirect_uris[0],
        client_id: client.client_id,
        code_verifier: verifier,
      }),
    });
    assert.equal(tokenRes.status, 200);
    const { access_token: accessToken } = await tokenRes.json();
    assert.ok(accessToken);

    for (const headers of [
      { 'User-Agent': 'codex-cli/0.146.0' },
      { Origin: 'https://claude.ai' },
      { 'User-Agent': 'generic-mcp-client/1.0' },
    ]) {
      const res = await mcpFetch(
        baseUrl,
        jsonRpc('tools/call', { name: 'memory_stats', arguments: {} }),
        { ...headers, Authorization: `Bearer ${accessToken}` },
      );
      assert.equal(res.status, 200);
    }
    assert.deepEqual(
      backendCalls.map(({ headers }) => headers['X-Memories-Client']),
      ['codex', 'claude-web', 'remote-mcp'],
    );
  } finally {
    await close();
  }
});

test('none mode: 200 without any Authorization header', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}));
    assert.equal(res.status, 200);
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// /mcp — auth mode 'oauth'
// ---------------------------------------------------------------------------

test('oauth mode: 401 without a token, WWW-Authenticate header present', async () => {
  const { baseUrl, close } = await oauthModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}));
    assert.equal(res.status, 401);
    const www = res.headers.get('www-authenticate');
    assert.ok(www, 'expected WWW-Authenticate header');
    assert.match(www, /Bearer/);
    assert.match(www, /resource_metadata=/);
    assert.match(www, /oauth-protected-resource/);
    const body = await res.json();
    assert.equal(body.error, 'invalid_token');
  } finally {
    await close();
  }
});

test('oauth mode: garbage bearer token is also 401', async () => {
  const { baseUrl, close } = await oauthModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}), { Authorization: 'Bearer not-a-real-token' });
    assert.equal(res.status, 401);
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// Origin guard
// ---------------------------------------------------------------------------

test('foreign Origin header is rejected with 403', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}), { Origin: 'https://evil.example.com' });
    assert.equal(res.status, 403);
  } finally {
    await close();
  }
});

test('claude.ai Origin header is allowed', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}), { Origin: 'https://claude.ai' });
    assert.equal(res.status, 200);
  } finally {
    await close();
  }
});

test('missing Origin header (non-browser client) is allowed', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}));
    assert.equal(res.status, 200);
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// GET/DELETE /mcp — 405 in stateless mode
// ---------------------------------------------------------------------------

test('GET /mcp is 405 in stateless mode', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await fetch(`${baseUrl}/mcp`, { headers: { Accept: MCP_ACCEPT } });
    assert.equal(res.status, 405);
  } finally {
    await close();
  }
});

test('DELETE /mcp is 405 in stateless mode', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    const res = await fetch(`${baseUrl}/mcp`, { method: 'DELETE', headers: { Accept: MCP_ACCEPT } });
    assert.equal(res.status, 405);
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// Rate limiting — /mcp (PR 83 follow-up #3, MINOR 3: defense-in-depth — a
// leaked bearer token shouldn't buy unthrottled access to the backend). The
// cap here (120) is deliberately generous compared to the unauthenticated
// OAuth endpoints' cap (20) so normal interactive tool-call bursts aren't
// throttled — this test just confirms the guard is wired in, not that the
// limit is tight.
// ---------------------------------------------------------------------------

test('429 after exceeding the generous rate limit on /mcp', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    let sawTooMany = false;
    // No Origin header (non-browser client, always allowed) — this needs to
    // actually reach mcpRateLimit, which now runs AFTER originGuard.
    for (let i = 0; i < 121; i++) {
      const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}));
      if (res.status === 429) sawTooMany = true;
    }
    assert.ok(sawTooMany, 'expected at least one 429 within 121 rapid requests to /mcp');
  } finally {
    await close();
  }
});

// PR 83 follow-up #4, P2 (guard ordering regression): mcpRateLimit briefly
// ran BEFORE originGuard, so a foreign-Origin caller — rejected 403 by
// originGuard either way — still consumed a slot in the victim IP's
// rate-limit bucket before being rejected. A malicious page (no token
// needed, just a same-machine/same-IP browser tab) could flood /mcp with a
// disallowed Origin and 429 the legitimate connector sharing that IP.
// Reviewer's exact repro: 120 foreign-Origin POSTs (all 403) left the next
// legit request 429'd. originGuard must gate BEFORE any quota is charged.
test('foreign-Origin requests to /mcp are rejected before they touch the rate-limit bucket', async () => {
  const { baseUrl, close } = await noneModeApp();
  try {
    // 120 requests with a disallowed Origin — every one must be 403, and
    // none of them should count against the shared per-IP bucket that a
    // legitimate (no-Origin) request from the same machine will hit next.
    for (let i = 0; i < 120; i++) {
      const res = await mcpFetch(baseUrl, jsonRpc('tools/list', {}), { Origin: 'https://evil.example' });
      assert.equal(res.status, 403, `foreign-Origin request ${i} should be 403, not consume the rate-limit bucket`);
    }

    // The legitimate request right after must NOT be rate-limited — if
    // originGuard ran after mcpRateLimit, this would come back 429 instead.
    const legit = await mcpFetch(baseUrl, jsonRpc('tools/list', {}));
    assert.notEqual(legit.status, 429, 'a legit request must not be rate-limited by a foreign-Origin flood');
    assert.equal(legit.status, 200);
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// Rate limiting — /token
// ---------------------------------------------------------------------------

test('429 after exceeding the rate limit on /token', async () => {
  const { baseUrl, close } = await oauthModeApp();
  try {
    let sawTooMany = false;
    for (let i = 0; i < 21; i++) {
      const res = await fetch(`${baseUrl}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'grant_type=refresh_token&refresh_token=nope',
      });
      if (res.status === 429) sawTooMany = true;
    }
    assert.ok(sawTooMany, 'expected at least one 429 within 21 rapid requests');
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// Full OAuth browser-flow smoke test
// ---------------------------------------------------------------------------

test('full oauth flow: register -> authorize (GET login page) -> authorize (POST password) -> token -> authed /mcp call', async () => {
  const { baseUrl, close, storeDir } = await oauthModeApp();
  try {
    // 1. Dynamic Client Registration
    const registerRes = await fetch(`${baseUrl}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ redirect_uris: ['https://claude.ai/api/mcp/callback'], client_name: 'Claude' }),
    });
    assert.equal(registerRes.status, 201);
    const client = await registerRes.json();
    assert.ok(client.client_id);

    // 2. PKCE pair
    const verifier = Buffer.from('a-random-verifier-string-that-is-long-enough').toString('base64url');
    const challenge = createHash('sha256').update(verifier).digest('base64url');

    // 3. GET /authorize -> login HTML
    const authorizeParams = new URLSearchParams({
      client_id: client.client_id,
      redirect_uri: client.redirect_uris[0],
      state: 'xyz-state',
      code_challenge: challenge,
      code_challenge_method: 'S256',
    });
    const getAuthorizeRes = await fetch(`${baseUrl}/authorize?${authorizeParams}`);
    assert.equal(getAuthorizeRes.status, 200);
    const loginHtml = await getAuthorizeRes.text();
    assert.match(loginHtml, /<form/);
    assert.match(loginHtml, /Claude/);

    // 4. POST /authorize with password -> 302 with ?code=
    const postAuthorizeRes = await fetch(`${baseUrl}/authorize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      redirect: 'manual',
      body: new URLSearchParams({
        password: 's3cret-pw',
        client_id: client.client_id,
        redirect_uri: client.redirect_uris[0],
        state: 'xyz-state',
        code_challenge: challenge,
        code_challenge_method: 'S256',
      }),
    });
    assert.equal(postAuthorizeRes.status, 302);
    const location = new URL(postAuthorizeRes.headers.get('location'));
    const code = location.searchParams.get('code');
    assert.ok(code);

    // 5. POST /token -> access_token
    const tokenRes = await fetch(`${baseUrl}/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code,
        redirect_uri: client.redirect_uris[0],
        client_id: client.client_id,
        code_verifier: verifier,
      }),
    });
    assert.equal(tokenRes.status, 200);
    const tokenBody = await tokenRes.json();
    assert.ok(tokenBody.access_token);

    // 6. Authed /mcp call
    const mcpRes = await mcpFetch(baseUrl, jsonRpc('tools/list', {}), {
      Authorization: `Bearer ${tokenBody.access_token}`,
    });
    assert.equal(mcpRes.status, 200);
    const mcpBody = await mcpRes.json();
    const names = mcpBody.result.tools.map((t) => t.name);
    assert.ok(names.includes('memory_search'));
    // RFC 6749 §5.1 — token responses must never be cached (bearer material).
    assert.equal(tokenRes.headers.get('cache-control'), 'no-store');
    assert.equal(tokenRes.headers.get('pragma'), 'no-cache');

    // A completed authorization_code grant marks the client activated —
    // registration floods can no longer evict it (PR 83 follow-up).
    const store = createStore(storeDir);
    const persisted = await store.getClient(client.client_id);
    assert.equal(typeof persisted.activated_at, 'number', 'client must be marked activated after a successful token grant');
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// createRateLimiter — bounded rate limiter (unit tests, no HTTP round-trips)
// ---------------------------------------------------------------------------

function fakeReqRes(ip) {
  const req = { ip };
  let status;
  let body;
  const res = {
    status(code) { status = code; return this; },
    json(payload) { body = payload; return this; },
    get _status() { return status; },
    get _body() { return body; },
  };
  return { req, res };
}

test('rate limiter: a flooding IP is capped at 429 without growing its tracked array past the limit', () => {
  const limiter = createRateLimiter({ limit: 20, windowMs: 60_000, sweepIntervalMs: 0 });
  let nextCalled = 0;
  const next = () => { nextCalled++; };

  let lastRes;
  for (let i = 0; i < 21; i++) {
    const { req, res } = fakeReqRes('203.0.113.5');
    limiter(req, res, next);
    lastRes = res;
  }
  assert.equal(nextCalled, 20, 'only the first 20 requests should pass through');
  assert.equal(lastRes._status, 429, 'the 21st request is rejected');
  assert.ok(limiter.countFor('203.0.113.5') <= 20, 'stored array must not grow past the limit');

  // A further flood of rejected requests must not grow the array either.
  for (let i = 0; i < 50; i++) {
    const { req, res } = fakeReqRes('203.0.113.5');
    limiter(req, res, next);
  }
  assert.equal(nextCalled, 20, 'still only the original 20 requests ever passed through');
  assert.ok(limiter.countFor('203.0.113.5') <= 20, 'stored array stays capped after a sustained flood');

  limiter.stop();
});

test('rate limiter: sweep evicts an idle IP once its whole window has aged out', async () => {
  const limiter = createRateLimiter({ limit: 20, windowMs: 1, sweepIntervalMs: 0 });
  const { req, res } = fakeReqRes('203.0.113.9');
  const next = () => {};
  limiter(req, res, next);
  assert.equal(limiter.size(), 1, 'IP is tracked after its first hit');

  await new Promise((resolve) => setTimeout(resolve, 20)); // let the 1ms window fully age out
  limiter.sweep();
  assert.equal(limiter.size(), 0, 'sweep drops IPs with no timestamps left in the window');

  limiter.stop();
});

test('rate limiter: hard cap on distinct tracked IPs fails closed instead of growing without bound', () => {
  const limiter = createRateLimiter({ limit: 20, windowMs: 60_000, sweepIntervalMs: 0, maxKeys: 2 });
  const next = () => {};

  const first = fakeReqRes('203.0.113.1');
  limiter(first.req, first.res, next);
  const second = fakeReqRes('203.0.113.2');
  limiter(second.req, second.res, next);
  assert.equal(limiter.size(), 2, 'two distinct IPs tracked, at the cap');

  const third = fakeReqRes('203.0.113.3');
  limiter(third.req, third.res, next);
  assert.equal(third.res._status, 429, 'a new IP arriving at capacity is rejected, not tracked');
  assert.equal(limiter.size(), 2, 'the Map never grows past maxKeys');

  // An IP already being tracked still gets served normally even at the cap.
  const firstAgain = fakeReqRes('203.0.113.1');
  limiter(firstAgain.req, firstAgain.res, next);
  assert.notEqual(firstAgain.res._status, 429);

  limiter.stop();
});

// ---------------------------------------------------------------------------
// trust proxy — per-client rate-limit buckets behind a reverse proxy
// ---------------------------------------------------------------------------

test('trustProxy "loopback": X-Forwarded-For gives each client its own rate-limit bucket', async () => {
  const storeDir = await freshStoreDir();
  const passwordHash = hashPassword('s3cret-pw');
  const { baseUrl, close } = await startApp({
    issuer: 'http://issuer.invalid',
    authMode: 'oauth',
    passwordHash,
    tokenSecret: 'test-token-secret',
    storeDir,
    memoriesUrl: 'http://unused.invalid',
    memoriesApiKey: '',
    trustProxy: 'loopback',
  });
  try {
    const hitToken = (clientIp) => fetch(`${baseUrl}/token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Forwarded-For': clientIp,
      },
      body: 'grant_type=refresh_token&refresh_token=nope',
    });

    // These fetches originate from 127.0.0.1 (loopback), the one hop
    // 'loopback' trusts, so Express derives req.ip from X-Forwarded-For.
    let lastA;
    for (let i = 0; i < 21; i++) {
      lastA = await hitToken('203.0.113.5');
    }
    assert.equal(lastA.status, 429, 'client A is rate-limited after 21 rapid requests');

    const resB = await hitToken('203.0.113.6');
    assert.notEqual(resB.status, 429, 'client B has its own bucket, unaffected by A\'s flood');
    assert.equal(resB.status, 400); // invalid_grant for the bogus refresh_token — proves the request was processed, not blocked
  } finally {
    await close();
  }
});

test('trustProxy off (default): every request shares one socket-address bucket', async () => {
  const storeDir = await freshStoreDir();
  const passwordHash = hashPassword('s3cret-pw');
  const { baseUrl, close } = await startApp({
    issuer: 'http://issuer.invalid',
    authMode: 'oauth',
    passwordHash,
    tokenSecret: 'test-token-secret',
    storeDir,
    memoriesUrl: 'http://unused.invalid',
    memoriesApiKey: '',
    // trustProxy intentionally omitted — defaults to false
  });
  try {
    const hitToken = (clientIp) => fetch(`${baseUrl}/token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Forwarded-For': clientIp,
      },
      body: 'grant_type=refresh_token&refresh_token=nope',
    });

    for (let i = 0; i < 20; i++) {
      await hitToken('203.0.113.5');
    }
    // X-Forwarded-For is ignored without trust proxy, so this "different
    // client" actually shares the loopback socket bucket with the 20 hits
    // above and trips the limit on request 21.
    const res = await hitToken('203.0.113.6');
    assert.equal(res.status, 429, 'X-Forwarded-For is ignored — same bucket as the prior 20 hits');
  } finally {
    await close();
  }
});

test(
  'trustProxy "uniquelocal": express trust-proxy setting reflects cfg ' +
  '(the real docker/tunnel topology has a bridge-IP peer, not loopback — ' +
  'full bridge-IP simulation is not feasible in-process, so this asserts the setting is applied)',
  async () => {
    const app = createApp({
      authMode: 'none',
      storeDir: await freshStoreDir(),
      trustProxy: 'uniquelocal',
    });
    assert.equal(app.get('trust proxy'), 'uniquelocal');
  }
);

// ---------------------------------------------------------------------------
// Rate limiting — /register (unauthenticated disk-fill guard)
// ---------------------------------------------------------------------------

test('429 after exceeding the rate limit on /register', async () => {
  const { baseUrl, close } = await oauthModeApp();
  try {
    let sawTooMany = false;
    for (let i = 0; i < 21; i++) {
      const res = await fetch(`${baseUrl}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ redirect_uris: ['https://claude.ai/cb'] }),
      });
      if (res.status === 429) sawTooMany = true;
    }
    assert.ok(sawTooMany, 'expected at least one 429 within 21 rapid requests to /register');
  } finally {
    await close();
  }
});

// ---------------------------------------------------------------------------
// Prototype-pollution client_id — must not crash the server (P1)
// ---------------------------------------------------------------------------

for (const pollutingId of ['toString', 'constructor', '__proto__']) {
  test(`GET /authorize?client_id=${pollutingId} does not crash the server — 4xx, and the app keeps serving`, async () => {
    const { baseUrl, close } = await oauthModeApp();
    try {
      const params = new URLSearchParams({
        client_id: pollutingId,
        redirect_uri: 'https://claude.ai/cb',
        code_challenge: 'x',
        code_challenge_method: 'S256',
      });
      const res = await fetch(`${baseUrl}/authorize?${params}`);
      assert.ok(res.status >= 400 && res.status < 500, `expected 4xx, got ${res.status}`);

      // The process must still be alive and serving — a prior crash would
      // manifest as this follow-up request failing (connection refused).
      const health = await fetch(`${baseUrl}/healthz`);
      assert.equal(health.status, 200);
    } finally {
      await close();
    }
  });
}

// ---------------------------------------------------------------------------
// trust proxy sentinel values — 'off'/'false' must disable, never throw
// ---------------------------------------------------------------------------

test('trustProxy "off" disables trust proxy without throwing', async () => {
  const app = createApp({
    authMode: 'none',
    storeDir: await freshStoreDir(),
    trustProxy: 'off',
  });
  assert.ok(!app.get('trust proxy'));
});

test('trustProxy "false" (string) disables trust proxy without throwing (previously crashed proxy-addr)', async () => {
  const app = createApp({
    authMode: 'none',
    storeDir: await freshStoreDir(),
    trustProxy: 'false',
  });
  assert.ok(!app.get('trust proxy'));
});

test('trustProxy "" (empty string) disables trust proxy without throwing', async () => {
  const app = createApp({
    authMode: 'none',
    storeDir: await freshStoreDir(),
    trustProxy: '',
  });
  assert.ok(!app.get('trust proxy'));
});

test('trustProxy "OFF" (case-insensitive) disables trust proxy', async () => {
  const app = createApp({
    authMode: 'none',
    storeDir: await freshStoreDir(),
    trustProxy: 'OFF',
  });
  assert.ok(!app.get('trust proxy'));
});
