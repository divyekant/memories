import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash, createHmac } from 'node:crypto';

import { createStore } from '../remote/store.mjs';
import { createOAuth, hashPassword, verifyPassword } from '../remote/oauth.mjs';
import { renderLogin } from '../remote/login.mjs';

const ISSUER = 'https://mcp.example.com';
const PASSWORD = 's3cret-pw';
const TOKEN_SECRET = 'test-token-secret';

async function freshStore() {
  const dir = await mkdtemp(join(tmpdir(), 'mem-oauth-'));
  return createStore(dir);
}

async function freshOAuth() {
  const store = await freshStore();
  const passwordHash = hashPassword(PASSWORD);
  const oauth = createOAuth({ issuer: ISSUER, passwordHash, tokenSecret: TOKEN_SECRET, store });
  return { oauth, store };
}

function b64url(buf) {
  return Buffer.from(buf).toString('base64url');
}

function pkcePair() {
  const verifier = b64url(Buffer.from('a-random-verifier-string-that-is-long-enough'));
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

async function registerClient(oauth, redirectUri = 'https://claude.ai/api/mcp/callback') {
  const res = await oauth.register({ redirect_uris: [redirectUri], client_name: 'Claude' });
  assert.equal(res.status, 201);
  return res.body;
}

// ---------------------------------------------------------------------------
// store.mjs
// ---------------------------------------------------------------------------

test('store: client round-trips through save/get', async () => {
  const store = await freshStore();
  await store.saveClient({ client_id: 'abc', redirect_uris: ['https://claude.ai/cb'] });
  const client = await store.getClient('abc');
  assert.equal(client.client_id, 'abc');
  assert.deepEqual(client.redirect_uris, ['https://claude.ai/cb']);
});

test('store: getClient returns null for unknown id', async () => {
  const store = await freshStore();
  assert.equal(await store.getClient('nope'), null);
});

test('store: code is single-use — second take returns null', async () => {
  const store = await freshStore();
  await store.saveCode('code123', { cid: 'client1', redirect_uri: 'https://claude.ai/cb', challenge: 'xyz' });
  const first = await store.takeCode('code123');
  assert.equal(first.cid, 'client1');
  const second = await store.takeCode('code123');
  assert.equal(second, null);
});

test('store: unknown code returns null', async () => {
  const store = await freshStore();
  assert.equal(await store.takeCode('never-saved'), null);
});

test('store: path-traversal code does not escape codesDir — takeCode returns null, clients.json survives', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-oauth-'));
  const store = createStore(dir);
  await store.saveClient({ client_id: 'abc', redirect_uris: ['https://claude.ai/cb'] });
  const clientsPath = join(dir, 'clients.json');
  assert.doesNotReject(() => access(clientsPath));

  const result = await store.takeCode('../clients');
  assert.equal(result, null);
  await access(clientsPath); // throws if the file was deleted — proves no traversal
  const client = await store.getClient('abc');
  assert.equal(client.client_id, 'abc');
});

test('store: path-traversal code with multiple segments does not throw and touches nothing outside codesDir', async () => {
  const store = await freshStore();
  await assert.doesNotReject(async () => {
    const result = await store.takeCode('../../etc/passwd');
    assert.equal(result, null);
  });
});

test('store: code round-trips normally after the filename-hashing fix', async () => {
  const store = await freshStore();
  await store.saveCode('a-normal-code', { cid: 'client1', redirect_uri: 'https://claude.ai/cb', challenge: 'xyz' });
  const record = await store.takeCode('a-normal-code');
  assert.equal(record.cid, 'client1');
  assert.equal(record.challenge, 'xyz');
});

test('store: refresh token rotates — take deletes it', async () => {
  const store = await freshStore();
  await store.saveRefresh('refresh-raw-token', { cid: 'client1' });
  const first = await store.takeRefresh('refresh-raw-token');
  assert.equal(first.cid, 'client1');
  const second = await store.takeRefresh('refresh-raw-token');
  assert.equal(second, null);
});

test('store: refresh tokens are stored hashed, not raw, on disk', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-oauth-'));
  const store = createStore(dir);
  const rawToken = 'super-secret-refresh-token';
  await store.saveRefresh(rawToken, { cid: 'client1' });
  const files = await (await import('node:fs/promises')).readdir(join(dir, 'refresh'));
  assert.equal(files.length, 1);
  assert.ok(!files[0].includes(rawToken), 'filename must not contain the raw token');
  const contents = await readFile(join(dir, 'refresh', files[0]), 'utf8');
  assert.ok(!contents.includes(rawToken), 'file contents must not contain the raw token');
});

// ---------------------------------------------------------------------------
// hashPassword / verifyPassword
// ---------------------------------------------------------------------------

test('hashPassword/verifyPassword: correct password verifies', () => {
  const hash = hashPassword('hunter2');
  assert.ok(hash.startsWith('scrypt:'));
  assert.equal(verifyPassword('hunter2', hash), true);
});

test('hashPassword/verifyPassword: wrong password fails', () => {
  const hash = hashPassword('hunter2');
  assert.equal(verifyPassword('wrong', hash), false);
});

test('hashPassword: two hashes of the same password differ (random salt)', () => {
  const h1 = hashPassword('hunter2');
  const h2 = hashPassword('hunter2');
  assert.notEqual(h1, h2);
});

test('verifyPassword: malformed hash string does not throw', () => {
  assert.doesNotThrow(() => {
    assert.equal(verifyPassword('anything', 'not-a-valid-hash'), false);
  });
  assert.doesNotThrow(() => {
    assert.equal(verifyPassword('anything', ''), false);
  });
  assert.doesNotThrow(() => {
    assert.equal(verifyPassword('anything', 'scrypt:onlyonepart'), false);
  });
});

// ---------------------------------------------------------------------------
// metadata
// ---------------------------------------------------------------------------

test('metadataAS: shape matches OAuth AS metadata contract', async () => {
  const { oauth } = await freshOAuth();
  const meta = oauth.metadataAS();
  assert.equal(meta.issuer, ISSUER);
  assert.equal(meta.authorization_endpoint, `${ISSUER}/authorize`);
  assert.equal(meta.token_endpoint, `${ISSUER}/token`);
  assert.equal(meta.registration_endpoint, `${ISSUER}/register`);
  assert.deepEqual(meta.response_types_supported, ['code']);
  assert.deepEqual(meta.grant_types_supported, ['authorization_code', 'refresh_token']);
  assert.deepEqual(meta.code_challenge_methods_supported, ['S256']);
  assert.deepEqual(meta.token_endpoint_auth_methods_supported, ['none']);
});

test('metadataPR: shape matches OAuth protected-resource metadata contract', async () => {
  const { oauth } = await freshOAuth();
  const meta = oauth.metadataPR();
  assert.equal(meta.resource, `${ISSUER}/mcp`);
  assert.deepEqual(meta.authorization_servers, [ISSUER]);
});

// ---------------------------------------------------------------------------
// DCR (register)
// ---------------------------------------------------------------------------

test('register: accepts claude.ai redirect_uri, returns public client', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({ redirect_uris: ['https://claude.ai/api/mcp/callback'], client_name: 'Claude' });
  assert.equal(res.status, 201);
  assert.ok(res.body.client_id);
  assert.equal(res.body.client_secret, undefined);
  assert.equal(res.body.token_endpoint_auth_method, 'none');
});

test('register: accepts claude.com and subdomain redirect_uris', async () => {
  const { oauth } = await freshOAuth();
  const res1 = await oauth.register({ redirect_uris: ['https://claude.com/cb'] });
  assert.equal(res1.status, 201);
  const res2 = await oauth.register({ redirect_uris: ['https://foo.claude.ai/cb'] });
  assert.equal(res2.status, 201);
});

test('register: accepts localhost and 127.0.0.1 redirect_uris', async () => {
  const { oauth } = await freshOAuth();
  const res1 = await oauth.register({ redirect_uris: ['http://localhost:1234/cb'] });
  assert.equal(res1.status, 201);
  const res2 = await oauth.register({ redirect_uris: ['http://127.0.0.1:1234/cb'] });
  assert.equal(res2.status, 201);
});

test('register: rejects redirect_uri on a foreign host', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({ redirect_uris: ['https://evil.example.com/cb'] });
  assert.equal(res.status, 400);
  assert.ok(res.body.error);
});

test('register: rejects when any of several redirect_uris is foreign', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({
    redirect_uris: ['https://claude.ai/cb', 'https://evil.example.com/cb'],
  });
  assert.equal(res.status, 400);
});

test('register: rejects missing redirect_uris', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({});
  assert.equal(res.status, 400);
});

test('register: rejects more than 10 redirect_uris (invalid_client_metadata)', async () => {
  const { oauth } = await freshOAuth();
  const uris = Array.from({ length: 11 }, (_, i) => `https://claude.ai/cb${i}`);
  const res = await oauth.register({ redirect_uris: uris });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'invalid_client_metadata');
});

test('register: accepts exactly 10 redirect_uris', async () => {
  const { oauth } = await freshOAuth();
  const uris = Array.from({ length: 10 }, (_, i) => `https://claude.ai/cb${i}`);
  const res = await oauth.register({ redirect_uris: uris });
  assert.equal(res.status, 201);
});

test('register: rejects client_name longer than 200 chars (invalid_client_metadata)', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({
    redirect_uris: ['https://claude.ai/cb'],
    client_name: 'x'.repeat(201),
  });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'invalid_client_metadata');
});

test('register: accepts client_name at exactly 200 chars', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({
    redirect_uris: ['https://claude.ai/cb'],
    client_name: 'x'.repeat(200),
  });
  assert.equal(res.status, 201);
});

test('register: rejects http:// redirect_uri on claude.ai (https required except localhost)', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({ redirect_uris: ['http://claude.ai/cb'] });
  assert.equal(res.status, 400);
});

test('register: still accepts http://localhost:3000/cb (localhost exempt from https requirement)', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.register({ redirect_uris: ['http://localhost:3000/cb'] });
  assert.equal(res.status, 201);
});

// ---------------------------------------------------------------------------
// authorizePage (GET /authorize)
// ---------------------------------------------------------------------------

test('authorizePage: renders login form with client name and hidden oauth params', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { challenge } = pkcePair();
  const res = await oauth.authorizePage({
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 'state123',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  assert.equal(res.status, 200);
  assert.match(res.body, /Claude/);
  assert.match(res.body, /state123/);
  assert.match(res.body, /<form/);
  assert.match(res.body, /\/authorize/);
});

test('authorizePage: unknown client_id does not render a redirecting form', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.authorizePage({ client_id: 'nope', redirect_uri: 'https://claude.ai/cb' });
  assert.equal(res.status, 400);
});

test('authorizePage: redirect_uri not registered for client is rejected', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const res = await oauth.authorizePage({
    client_id: client.client_id,
    redirect_uri: 'https://claude.ai/some-other-callback',
    code_challenge: 'x',
    code_challenge_method: 'S256',
  });
  assert.equal(res.status, 400);
});

test('authorizePage: missing PKCE code_challenge is rejected', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const res = await oauth.authorizePage({
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
  });
  assert.equal(res.status, 400);
});

// ---------------------------------------------------------------------------
// Full happy-path flow: authorize -> code -> token -> verifyAccess
// ---------------------------------------------------------------------------

test('happy path: authorize with correct password issues code, code exchanges for tokens, access token verifies', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 'xyz-state',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });

  assert.ok(authRes.redirect, 'expected a redirect on success');
  const redirectUrl = new URL(authRes.redirect);
  assert.equal(`${redirectUrl.origin}${redirectUrl.pathname}`, client.redirect_uris[0]);
  const code = redirectUrl.searchParams.get('code');
  assert.ok(code);
  assert.equal(redirectUrl.searchParams.get('state'), 'xyz-state');

  const tokenRes = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    client_id: client.client_id,
    code_verifier: verifier,
  });

  assert.equal(tokenRes.status, 200);
  assert.ok(tokenRes.body.access_token);
  assert.equal(tokenRes.body.token_type, 'bearer');
  assert.equal(tokenRes.body.expires_in, 3600);
  assert.ok(tokenRes.body.refresh_token);

  const verify = oauth.verifyAccess(tokenRes.body.access_token);
  assert.equal(verify.ok, true);
  assert.equal(verify.subject, 'dk');
});

test('happy path: authorization code is single-use — replay fails', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');

  const tokenArgs = {
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    client_id: client.client_id,
    code_verifier: verifier,
  };
  const first = await oauth.token(tokenArgs);
  assert.equal(first.status, 200);

  const second = await oauth.token(tokenArgs);
  assert.equal(second.status, 400);
  assert.equal(second.body.error, 'invalid_grant');
});

test('wrong password re-renders login with error, no code issued', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { challenge } = pkcePair();

  const res = await oauth.handleAuthorize({
    password: 'totally-wrong',
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });

  assert.equal(res.status, 200);
  assert.equal(res.redirect, undefined);
  assert.match(res.body, /<form/);
  assert.match(res.body, /error/i);
});

test('handleAuthorize: missing PKCE fields rejected even with correct password', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);

  const res = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
  });

  assert.notEqual(res.status, undefined);
  assert.equal(res.redirect, undefined);
});

test('token: wrong code_verifier yields invalid_grant', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');

  const tokenRes = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    client_id: client.client_id,
    code_verifier: 'totally-the-wrong-verifier',
  });

  assert.equal(tokenRes.status, 400);
  assert.equal(tokenRes.body.error, 'invalid_grant');
});

test('token: mismatched redirect_uri yields invalid_grant', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');

  const tokenRes = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: 'https://claude.ai/a-different-callback',
    client_id: client.client_id,
    code_verifier: verifier,
  });

  assert.equal(tokenRes.status, 400);
  assert.equal(tokenRes.body.error, 'invalid_grant');
});

test('token: unknown code yields invalid_grant', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const tokenRes = await oauth.token({
    grant_type: 'authorization_code',
    code: 'never-issued',
    redirect_uri: client.redirect_uris[0],
    client_id: client.client_id,
    code_verifier: 'whatever',
  });
  assert.equal(tokenRes.status, 400);
  assert.equal(tokenRes.body.error, 'invalid_grant');
});

test('token: missing client_id on authorization_code grant is invalid_request', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');

  const tokenRes = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    code_verifier: verifier,
    // client_id intentionally omitted
  });
  assert.equal(tokenRes.status, 400);
  assert.equal(tokenRes.body.error, 'invalid_request');
});

test('token: client_id that does not match the code issuer is invalid_grant', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const otherClient = await registerClient(oauth, 'https://claude.ai/other-callback');
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');

  const tokenRes = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    client_id: otherClient.client_id,
    code_verifier: verifier,
  });
  assert.equal(tokenRes.status, 400);
  assert.equal(tokenRes.body.error, 'invalid_grant');
});

test('token: unsupported grant_type is rejected', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.token({ grant_type: 'client_credentials' });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'unsupported_grant_type');
});

// ---------------------------------------------------------------------------
// verifyAccess
// ---------------------------------------------------------------------------

test('verifyAccess: expired access token fails with expired_token', async () => {
  const { oauth } = await freshOAuth();
  const now = Math.floor(Date.now() / 1000);
  const payload = { sub: 'dk', iat: now - 7200, exp: now - 3600, cid: 'client1' };
  const payloadB64 = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = createHmac('sha256', TOKEN_SECRET).update(payloadB64).digest('base64url');
  const expiredToken = `${payloadB64}.${sig}`;

  const res = oauth.verifyAccess(expiredToken);
  assert.equal(res.ok, false);
  assert.equal(res.error, 'expired_token');
});

test('verifyAccess: tampered signature fails with invalid_token', async () => {
  const { oauth } = await freshOAuth();
  const now = Math.floor(Date.now() / 1000);
  const payload = { sub: 'dk', iat: now, exp: now + 3600, cid: 'client1' };
  const payloadB64 = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const wrongSig = createHmac('sha256', 'wrong-secret').update(payloadB64).digest('base64url');
  const tampered = `${payloadB64}.${wrongSig}`;

  const res = oauth.verifyAccess(tampered);
  assert.equal(res.ok, false);
  assert.equal(res.error, 'invalid_token');
});

test('verifyAccess: garbage token fails without throwing', async () => {
  const { oauth } = await freshOAuth();
  assert.doesNotThrow(() => {
    const res = oauth.verifyAccess('not-a-real-token');
    assert.equal(res.ok, false);
  });
  assert.doesNotThrow(() => {
    const res = oauth.verifyAccess('');
    assert.equal(res.ok, false);
  });
});

// ---------------------------------------------------------------------------
// Refresh rotation
// ---------------------------------------------------------------------------

test('refresh: rotates — old refresh token dies, new one works', async () => {
  const { oauth } = await freshOAuth();
  const client = await registerClient(oauth);
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');
  const first = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    client_id: client.client_id,
    code_verifier: verifier,
  });
  const oldRefresh = first.body.refresh_token;

  const refreshed = await oauth.token({ grant_type: 'refresh_token', refresh_token: oldRefresh });
  assert.equal(refreshed.status, 200);
  assert.ok(refreshed.body.access_token);
  assert.ok(refreshed.body.refresh_token);
  assert.notEqual(refreshed.body.refresh_token, oldRefresh);

  const verify = oauth.verifyAccess(refreshed.body.access_token);
  assert.equal(verify.ok, true);
  assert.equal(verify.subject, 'dk');

  const replay = await oauth.token({ grant_type: 'refresh_token', refresh_token: oldRefresh });
  assert.equal(replay.status, 400);
  assert.equal(replay.body.error, 'invalid_grant');
});

test('refresh: unknown refresh_token yields invalid_grant', async () => {
  const { oauth } = await freshOAuth();
  const res = await oauth.token({ grant_type: 'refresh_token', refresh_token: 'never-issued' });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'invalid_grant');
});

test('refresh: expired refresh record yields invalid_grant', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-oauth-'));
  const store = createStore(dir);
  const passwordHash = hashPassword(PASSWORD);
  const oauth = createOAuth({ issuer: ISSUER, passwordHash, tokenSecret: TOKEN_SECRET, store });

  const rawToken = 'expired-refresh-token';
  // saveRefresh always stamps a fresh 30-day exp; to exercise the expiry path,
  // write an already-expired record directly using the same sha256-hex
  // filename scheme store.mjs uses to key refresh tokens on disk.
  await store.saveRefresh(rawToken, { cid: 'some-client' });
  const filename = `${createHash('sha256').update(rawToken).digest('hex')}.json`;
  await writeFile(join(dir, 'refresh', filename), JSON.stringify({ cid: 'some-client', exp: Date.now() - 1000 }));

  const res = await oauth.token({ grant_type: 'refresh_token', refresh_token: rawToken });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'invalid_grant');
});

test('refresh: rotated replacement gets a fresh (non-expired) exp', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-oauth-'));
  const store = createStore(dir);
  const passwordHash = hashPassword(PASSWORD);
  const oauth = createOAuth({ issuer: ISSUER, passwordHash, tokenSecret: TOKEN_SECRET, store });
  const client = await registerClient(oauth);
  const { verifier, challenge } = pkcePair();

  const authRes = await oauth.handleAuthorize({
    password: PASSWORD,
    client_id: client.client_id,
    redirect_uri: client.redirect_uris[0],
    state: 's',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  const code = new URL(authRes.redirect).searchParams.get('code');
  const first = await oauth.token({
    grant_type: 'authorization_code',
    code,
    redirect_uri: client.redirect_uris[0],
    client_id: client.client_id,
    code_verifier: verifier,
  });

  const refreshed = await oauth.token({ grant_type: 'refresh_token', refresh_token: first.body.refresh_token });
  assert.equal(refreshed.status, 200);
  const filename = `${createHash('sha256').update(refreshed.body.refresh_token).digest('hex')}.json`;
  const onDisk = JSON.parse(await readFile(join(dir, 'refresh', filename), 'utf8'));
  assert.ok(onDisk.exp > Date.now(), 'new refresh record must have a future exp');
});

// ---------------------------------------------------------------------------
// login.mjs
// ---------------------------------------------------------------------------

test('renderLogin: contains no external assets (no http(s) src/href to another origin)', () => {
  const html = renderLogin({ clientName: 'Claude', params: { client_id: 'c1', redirect_uri: 'https://claude.ai/cb' } });
  assert.doesNotMatch(html, /<link[^>]+href=["']https?:\/\//);
  assert.doesNotMatch(html, /<script[^>]+src=["']https?:\/\//);
});

test('renderLogin: shows error text when provided', () => {
  const html = renderLogin({ clientName: 'Claude', error: 'Invalid password' });
  assert.match(html, /Invalid password/);
});
