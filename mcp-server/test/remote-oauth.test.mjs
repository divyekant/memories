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

// Like freshStore, but also returns `dir` — needed by tests that inspect
// clients.json directly (e.g. asserting the registry never exceeds
// MAX_CLIENTS on disk) rather than through the store's own accessors.
async function freshStoreWithDir() {
  const dir = await mkdtemp(join(tmpdir(), 'mem-oauth-'));
  return { store: createStore(dir), dir };
}

async function registrySize(dir) {
  const raw = await readFile(join(dir, 'clients.json'), 'utf8').catch(() => '{}');
  return Object.keys(JSON.parse(raw)).length;
}

async function freshOAuth() {
  const { store, dir } = await freshStoreWithDir();
  const passwordHash = hashPassword(PASSWORD);
  const oauth = createOAuth({ issuer: ISSUER, passwordHash, tokenSecret: TOKEN_SECRET, store });
  return { oauth, store, dir };
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
// store: prototype-pollution-safe registry lookups (P1)
// ---------------------------------------------------------------------------

for (const pollutingId of ['toString', 'constructor', '__proto__', 'hasOwnProperty']) {
  test(`store: getClient('${pollutingId}') returns null instead of an inherited Object.prototype value`, async () => {
    const store = await freshStore();
    await store.saveClient({ client_id: 'real-client', redirect_uris: ['https://claude.ai/cb'] });
    const client = await store.getClient(pollutingId);
    assert.equal(client, null);
  });
}

// ---------------------------------------------------------------------------
// store: atomic grant consume — concurrent takeCode/takeRefresh (P1)
// ---------------------------------------------------------------------------

test('store: concurrent takeCode on the same code — exactly one caller gets it', async () => {
  const store = await freshStore();
  await store.saveCode('shared-code', { cid: 'client1', redirect_uri: 'https://claude.ai/cb', challenge: 'xyz' });
  const results = await Promise.all(Array.from({ length: 5 }, () => store.takeCode('shared-code')));
  const nonNull = results.filter((r) => r !== null);
  assert.equal(nonNull.length, 1, 'exactly one concurrent takeCode should succeed');
  assert.equal(nonNull[0].cid, 'client1');
});

test('store: concurrent takeRefresh on the same token — exactly one caller gets it', async () => {
  const store = await freshStore();
  await store.saveRefresh('shared-refresh', { cid: 'client1' });
  const results = await Promise.all(Array.from({ length: 5 }, () => store.takeRefresh('shared-refresh')));
  const nonNull = results.filter((r) => r !== null);
  assert.equal(nonNull.length, 1, 'exactly one concurrent takeRefresh should succeed');
  assert.equal(nonNull[0].cid, 'client1');
});

// ---------------------------------------------------------------------------
// store: saveClient concurrency — no lost updates (P1)
// ---------------------------------------------------------------------------

test('store: 50 concurrent saveClient calls with distinct ids all persist', async () => {
  const store = await freshStore();
  const ids = Array.from({ length: 50 }, (_, i) => `client-${i}`);
  await Promise.all(
    ids.map((id) => store.saveClient({ client_id: id, redirect_uris: ['https://claude.ai/cb'] }))
  );
  for (const id of ids) {
    const client = await store.getClient(id);
    assert.ok(client, `client ${id} should have persisted`);
    assert.equal(client.client_id, id);
  }
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

test('register: rejects a redirect_uri longer than 2048 chars (invalid_client_metadata)', async () => {
  const { oauth } = await freshOAuth();
  const longUri = `https://claude.ai/cb?pad=${'a'.repeat(3000)}`;
  const res = await oauth.register({ redirect_uris: [longUri] });
  assert.equal(res.status, 400);
  assert.equal(res.body.error, 'invalid_client_metadata');
});

test('register: accepts a redirect_uri at exactly 2048 chars', async () => {
  const { oauth } = await freshOAuth();
  const prefix = 'https://claude.ai/cb?pad=';
  const uri = prefix + 'a'.repeat(2048 - prefix.length);
  assert.equal(uri.length, 2048);
  const res = await oauth.register({ redirect_uris: [uri] });
  assert.equal(res.status, 201);
});

test('register: client record carries created_at', async () => {
  const { oauth } = await freshOAuth();
  const before = Date.now();
  const res = await oauth.register({ redirect_uris: ['https://claude.ai/cb'] });
  assert.equal(res.status, 201);
  assert.equal(typeof res.body.created_at, 'number');
  assert.ok(res.body.created_at >= before);
});

test('register: 105 sequential registrations cap the registry at 100, evicting the 5 oldest', async () => {
  const { oauth, store } = await freshOAuth();
  const clients = [];
  for (let i = 0; i < 105; i++) {
    const res = await oauth.register({ redirect_uris: [`http://localhost:1234/cb${i}`] });
    assert.equal(res.status, 201);
    clients.push(res.body);
  }
  const oldest5 = clients.slice(0, 5);
  const newest100 = clients.slice(5);

  for (const c of oldest5) {
    assert.equal(await store.getClient(c.client_id), null, `oldest client ${c.client_id} should have been evicted`);
  }
  for (const c of newest100) {
    assert.ok(await store.getClient(c.client_id), `newest client ${c.client_id} should still be present`);
  }
});

// ---------------------------------------------------------------------------
// PR-83 P1: registration floods must not be able to evict an activated
// (real user has completed login+consent) client.
// ---------------------------------------------------------------------------

test('store: markClientActive sets activated_at once and is idempotent', async () => {
  const store = await freshStore();
  await store.saveClient({ client_id: 'c1', redirect_uris: ['https://claude.ai/cb'], created_at: Date.now() });

  assert.equal((await store.getClient('c1')).activated_at, undefined);
  await store.markClientActive('c1');
  const first = (await store.getClient('c1')).activated_at;
  assert.equal(typeof first, 'number');

  await store.markClientActive('c1');
  const second = (await store.getClient('c1')).activated_at;
  assert.equal(second, first, 'a second activation must not move the timestamp');
});

test('store: markClientActive on an unknown client_id is a no-op, returns false', async () => {
  const store = await freshStore();
  assert.equal(await store.markClientActive('nope'), false);
});

test(
  'reviewer repro: an activated client survives a 150-registration flood, registry stays <= MAX_CLIENTS',
  async () => {
    const { oauth, store, dir } = await freshOAuth();

    const legit = await registerClient(oauth, 'https://claude.ai/api/mcp/callback');
    await store.markClientActive(legit.client_id);

    for (let i = 0; i < 150; i++) {
      const res = await oauth.register({ redirect_uris: [`https://claude.ai/flood-cb-${i}`] });
      // Some of these are expected to be rejected once the registry fills
      // and every never-activated slot has already been evicted — that's
      // exactly the point (see the 429 test below). Only fail the test on
      // an unexpected status.
      assert.ok(res.status === 201 || res.status === 429, `unexpected status ${res.status} for flood registration ${i}`);
    }

    const resolved = await store.getClient(legit.client_id);
    assert.ok(resolved, 'the legitimate, activated claude.ai client must still resolve');
    assert.equal(resolved.client_id, legit.client_id);

    assert.ok((await registrySize(dir)) <= 100, 'registry must never exceed MAX_CLIENTS');
  }
);

test('register: re-registering identical redirect_uris returns the same client_id and does not grow the registry', async () => {
  const { oauth, dir } = await freshOAuth();
  const uris = ['https://claude.ai/api/mcp/callback'];

  const first = await oauth.register({ redirect_uris: uris, client_name: 'Claude' });
  assert.equal(first.status, 201);
  const sizeAfterFirst = await registrySize(dir);

  const second = await oauth.register({ redirect_uris: uris, client_name: 'Claude (renamed)' });
  assert.equal(second.status, 201);

  assert.equal(second.body.client_id, first.body.client_id, 're-registration must land on the same client_id');
  assert.equal(await registrySize(dir), sizeAfterFirst, 're-registration must not consume a new registry slot');
});

test('register: re-registration with a cosmetic client_name change updates client_name in place', async () => {
  const { oauth, store } = await freshOAuth();
  const uris = ['https://claude.ai/api/mcp/callback'];

  const first = await oauth.register({ redirect_uris: uris, client_name: 'Claude' });
  const second = await oauth.register({ redirect_uris: uris, client_name: 'Claude Desktop' });

  assert.equal(second.body.client_id, first.body.client_id);
  const stored = await store.getClient(first.body.client_id);
  assert.equal(stored.client_name, 'Claude Desktop');
});

test('register: two different client metadata payloads never collide on client_id', async () => {
  const { oauth } = await freshOAuth();
  const a = await oauth.register({ redirect_uris: ['https://claude.ai/cb-a'] });
  const b = await oauth.register({ redirect_uris: ['https://claude.ai/cb-b'] });
  assert.notEqual(a.body.client_id, b.body.client_id);
});

test('register: registry full of 100 ACTIVATED clients rejects a new registration with 429 temporarily_unavailable (no eviction)', async () => {
  const { oauth, store, dir } = await freshOAuth();

  const activatedIds = [];
  for (let i = 0; i < 100; i++) {
    const res = await oauth.register({ redirect_uris: [`https://claude.ai/activated-cb-${i}`] });
    assert.equal(res.status, 201);
    await store.markClientActive(res.body.client_id);
    activatedIds.push(res.body.client_id);
  }
  assert.equal(await registrySize(dir), 100);

  const overflow = await oauth.register({ redirect_uris: ['https://claude.ai/one-too-many'] });
  assert.equal(overflow.status, 429);
  assert.equal(overflow.body.error, 'temporarily_unavailable');

  assert.equal(await registrySize(dir), 100, 'no client should have been evicted to make room');
  for (const id of activatedIds) {
    assert.ok(await store.getClient(id), `activated client ${id} must survive the rejected registration attempt`);
  }
});

test('register: at cap with a mix of activated and never-activated clients, only a never-activated one is evicted', async () => {
  const { oauth, store, dir } = await freshOAuth();

  const activated = await registerClient(oauth, 'https://claude.ai/the-activated-one');
  await store.markClientActive(activated.client_id);

  for (let i = 0; i < 99; i++) {
    const res = await oauth.register({ redirect_uris: [`https://claude.ai/never-activated-${i}`] });
    assert.equal(res.status, 201);
  }
  assert.equal(await registrySize(dir), 100);

  const res = await oauth.register({ redirect_uris: ['https://claude.ai/pushes-one-out'] });
  assert.equal(res.status, 201, 'a never-activated victim is available, so this registration must succeed');
  assert.equal(await registrySize(dir), 100);
  assert.ok(await store.getClient(activated.client_id), 'the activated client must never be the eviction victim');
});

// ---------------------------------------------------------------------------
// PR 83 follow-up (P1, reviewer-orchestrated): register() used to read the
// existing client OUTSIDE the write queue and write that stale snapshot
// INSIDE it — a markClientActive() landing between the read and the write
// got silently clobbered. Fixed via store.upsertClient(), which does the
// read-modify-write in one serialized step.
// ---------------------------------------------------------------------------

test('store: upsertClient creates a new record when none exists', async () => {
  const store = await freshStore();
  const result = await store.upsertClient('c1', (current) => {
    assert.equal(current, null);
    return { redirect_uris: ['https://claude.ai/cb'], client_name: 'Claude' };
  });
  assert.equal(result.ok, true);
  assert.equal(result.client.client_id, 'c1');
  const stored = await store.getClient('c1');
  assert.equal(stored.client_name, 'Claude');
});

test('store: upsertClient re-applies created_at/activated_at even when updateFn omits them', async () => {
  const store = await freshStore();
  await store.saveClient({ client_id: 'c1', redirect_uris: ['https://claude.ai/cb'], created_at: 12345 });
  await store.markClientActive('c1');
  const activatedAt = (await store.getClient('c1')).activated_at;
  assert.equal(typeof activatedAt, 'number');

  // updateFn returns a record with NO created_at/activated_at at all —
  // exactly what register()'s updateFn does (it has no idea whether the
  // client existed before or was ever activated).
  const result = await store.upsertClient('c1', () => ({
    redirect_uris: ['https://claude.ai/cb'],
    client_name: 'Renamed',
  }));

  assert.equal(result.ok, true);
  assert.equal(result.client.created_at, 12345, 'created_at must survive even though updateFn never set it');
  assert.equal(result.client.activated_at, activatedAt, 'activated_at must survive even though updateFn never set it');
  assert.equal(result.client.client_name, 'Renamed');
});

test('store: upsertClient with an updateFn returning undefined is a no-op — no phantom record created', async () => {
  const store = await freshStore();
  const result = await store.upsertClient('nope', () => undefined);
  assert.equal(result.ok, false);
  assert.equal(await store.getClient('nope'), null);
});

test(
  'store: a stale pre-queue snapshot (the exact old register() pattern) never clobbers a concurrent activation',
  async () => {
    const store = await freshStore();
    await store.saveClient({ client_id: 'c1', redirect_uris: ['https://claude.ai/cb'], created_at: Date.now() });

    // This reproduces the exact buggy sequence the old register() executed:
    // read a snapshot OUTSIDE any serialization...
    const staleSnapshot = await store.getClient('c1');
    // ...then a concurrent markClientActive() lands and completes first...
    await store.markClientActive('c1');
    assert.ok((await store.getClient('c1')).activated_at, 'sanity: activation landed');
    // ...then the original caller's save proceeds, built from the now-stale
    // snapshot (no activated_at in it).
    await store.saveClient({ ...staleSnapshot, client_name: 'Renamed by a racing re-registration' });

    const current = await store.getClient('c1');
    assert.equal(
      typeof current.activated_at,
      'number',
      'activated_at must survive a save built from a pre-activation snapshot'
    );
    assert.equal(current.client_name, 'Renamed by a racing re-registration');
  }
);

test(
  'reviewer repro: concurrent re-registration + activation never loses activated_at, over 20 jittered iterations',
  async () => {
    const jitter = () => new Promise((resolve) => setImmediate(resolve));

    for (let i = 0; i < 20; i++) {
      // Fresh store per iteration: once a client is durably activated, a
      // register() that reads it will always see activated_at already
      // set (no race window left), so each iteration needs its own
      // never-yet-activated client to give the race a fresh chance to
      // manifest. Varying which side gets the jitter alternates which
      // operation is more likely to "win" the queue first across
      // iterations.
      const { oauth, store } = await freshOAuth();
      const redirectUris = [`https://claude.ai/race-cb-${i}`];

      const seeded = await oauth.register({ redirect_uris: redirectUris, client_name: 'Claude' });
      assert.equal(seeded.status, 201);
      const clientId = seeded.body.client_id;

      await Promise.all([
        (async () => {
          if (i % 2 === 0) await jitter();
          await oauth.register({ redirect_uris: redirectUris, client_name: `Claude re-reg ${i}` });
        })(),
        (async () => {
          if (i % 2 === 1) await jitter();
          await store.markClientActive(clientId);
        })(),
      ]);

      const current = await store.getClient(clientId);
      assert.equal(
        typeof current.activated_at,
        'number',
        `iteration ${i}: activated_at lost to a concurrent re-registration (queue-boundary race)`
      );
    }
  }
);

// ---------------------------------------------------------------------------
// PR 83 follow-up #3, IMPORTANT: activation window — a client that finishes
// /authorize but hasn't yet redeemed its code at /token can be evicted by a
// concurrent /register flood (codes live in a separate directory from the
// client registry, so an evicted client's outstanding code still redeems
// fine). grantAuthorizationCode must resurrect-and-activate rather than
// silently no-op via markClientActive, or the client comes back from
// /token with valid tokens but stays permanently absent from the registry
// — flood-evictable forever despite the user having just consented.
// ---------------------------------------------------------------------------

test('store: activateOrCreate resurrects a missing client with the given redirect_uri, activated', async () => {
  const store = await freshStore();
  assert.equal(await store.getClient('gone'), null);

  const ok = await store.activateOrCreate('gone', { redirectUri: 'https://claude.ai/cb' });
  assert.equal(ok, true);

  const resurrected = await store.getClient('gone');
  assert.equal(resurrected.client_id, 'gone');
  assert.deepEqual(resurrected.redirect_uris, ['https://claude.ai/cb']);
  assert.equal(typeof resurrected.activated_at, 'number');
  assert.equal(typeof resurrected.created_at, 'number');
});

test('store: activateOrCreate on an EXISTING client just activates it — does not overwrite redirect_uris', async () => {
  const store = await freshStore();
  await store.saveClient({
    client_id: 'c1',
    redirect_uris: ['https://claude.ai/cb', 'https://claude.ai/cb2'],
    created_at: 12345,
  });

  const ok = await store.activateOrCreate('c1', { redirectUri: 'https://claude.ai/cb' });
  assert.equal(ok, true);

  const current = await store.getClient('c1');
  assert.deepEqual(current.redirect_uris, ['https://claude.ai/cb', 'https://claude.ai/cb2']);
  assert.equal(current.created_at, 12345, 'created_at must be untouched for an existing record');
  assert.equal(typeof current.activated_at, 'number');
});

test('store: activateOrCreate on a full registry of activated clients logs and returns false, without throwing', async () => {
  const store = await freshStore();
  for (let i = 0; i < 100; i++) {
    await store.saveClient({ client_id: `activated-${i}`, redirect_uris: ['https://claude.ai/cb'] });
    await store.markClientActive(`activated-${i}`);
  }

  const originalWarn = console.warn;
  let warned = '';
  console.warn = (...args) => { warned += args.join(' '); };
  let ok;
  try {
    ok = await store.activateOrCreate('newcomer', { redirectUri: 'https://claude.ai/cb' });
  } finally {
    console.warn = originalWarn;
  }

  assert.equal(ok, false);
  assert.match(warned, /registry full/i);
  assert.equal(await store.getClient('newcomer'), null, 'not persisted, but must not have thrown');
});

test(
  'reviewer repro: a client evicted between /authorize and /token still gets tokens, and comes back activated',
  async () => {
    const { oauth, store } = await freshOAuth();

    // 1. Register the legit client (never activated yet — activation only
    // happens on a successful /token exchange, not at /authorize).
    const client = await registerClient(oauth, 'https://claude.ai/api/mcp/callback');

    // 2. Complete /authorize — issues a code without activating anything.
    const { verifier, challenge } = pkcePair();
    const authorizeRes = await oauth.handleAuthorize({
      password: PASSWORD,
      client_id: client.client_id,
      redirect_uri: client.redirect_uris[0],
      code_challenge: challenge,
      code_challenge_method: 'S256',
    });
    assert.ok(authorizeRes.redirect, 'expected a redirect with ?code=');
    const code = new URL(authorizeRes.redirect).searchParams.get('code');
    assert.ok(code);

    // Sanity: still never-activated right after /authorize.
    assert.equal((await store.getClient(client.client_id)).activated_at, undefined);

    // 3. A registration flood evicts it. Outstanding codes live in their
    // own directory, untouched by client-registry eviction, so the code
    // issued above stays redeemable throughout.
    for (let i = 0; i < 150; i++) {
      await oauth.register({ redirect_uris: [`https://claude.ai/flood-cb-${i}`] });
    }
    assert.equal(
      await store.getClient(client.client_id),
      null,
      'sanity: the flood evicted the never-activated client before /token ran'
    );

    // 4. /token still succeeds — grantAuthorizationCode trusts the code
    // record's cid, not a live client-registry lookup.
    const tokenRes = await oauth.token({
      grant_type: 'authorization_code',
      code,
      redirect_uri: client.redirect_uris[0],
      client_id: client.client_id,
      code_verifier: verifier,
    });
    assert.equal(tokenRes.status, 200);
    assert.ok(tokenRes.body.access_token);

    // 5. The fix: the client must be resurrected and activated — not left
    // permanently absent, which would make it flood-evictable forever
    // despite the user having just consented.
    const resurrected = await store.getClient(client.client_id);
    assert.ok(resurrected, 'client must be resurrected after a successful token exchange');
    assert.equal(typeof resurrected.activated_at, 'number');
    assert.deepEqual(resurrected.redirect_uris, [client.redirect_uris[0]]);

    // 6. And it must actually be flood-proof now: another wave cannot
    // evict it, because it's activated.
    for (let i = 0; i < 50; i++) {
      await oauth.register({ redirect_uris: [`https://claude.ai/flood-wave-2-${i}`] });
    }
    assert.ok(
      await store.getClient(client.client_id),
      'an activated (resurrected) client must survive a further flood'
    );
  }
);

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
