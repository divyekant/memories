// File-backed store for the single-user OAuth provider.
//
// Layout under `dir`:
//   clients.json        — map of client_id -> client record (DCR-registered clients)
//   codes/<hash>.json    — one file per outstanding authorization code, keyed by
//                          sha256(code) hex — NOT the raw code. Filenames are never built
//                          from caller-supplied input: an unauthenticated caller controls
//                          `code` at the /token endpoint, and interpolating it directly into
//                          a path let a value like "../../clients" delete arbitrary store
//                          files before the code was even validated. Hashing collapses any
//                          input to a fixed-width hex string, eliminating that class of bug
//                          (same pattern the refresh path already used).
//   refresh/<hash>.json  — one file per outstanding refresh token, keyed by sha256(token) —
//                          NOT the raw token, so a leaked directory listing or backup never
//                          exposes a bearer-usable refresh token.
//
// This is intentionally simple (no locking/transactions) — fine for a single-process,
// single-user provider. take* = read then delete, which is "atomic enough" here because
// there is exactly one Node process ever touching this directory.

import { mkdir, readFile, writeFile, unlink, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';

const CODE_TTL_MS = 10 * 60 * 1000;
const REFRESH_TTL_MS = 30 * 24 * 60 * 60 * 1000;

// Hard cap on the DCR-registered client registry. `saveClient` evicts the
// oldest (by created_at) entries on write once this is exceeded — an
// unauthenticated /register endpoint must never be able to grow clients.json
// without bound.
const MAX_CLIENTS = 100;

function hashToken(token) {
  return createHash('sha256').update(token).digest('hex');
}

async function readJsonSafe(path) {
  try {
    return JSON.parse(await readFile(path, 'utf8'));
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
}

// Registry lookups are keyed by attacker-controlled strings (client_id is
// echoed back from an unauthenticated /register call and later replayed as a
// query param to /authorize). A plain JSON.parse'd object inherits from
// Object.prototype, so `clients['toString']` or `clients['constructor']`
// resolves to an inherited function instead of `undefined` — callers then
// treat that as a real client record and crash deref'ing `.redirect_uris`.
// Deserializing onto a null-prototype object closes that off: there is
// nothing to inherit, so an unknown/polluting id always reads as `undefined`.
async function readClients(clientsFile) {
  const parsed = await readJsonSafe(clientsFile);
  return Object.assign(Object.create(null), parsed || {});
}

// Atomic write: write to a tmp file in the same directory, then rename over
// the target. `rename` within one filesystem is atomic, so a reader never
// observes a partially-written clients.json (a plain writeFile can be
// truncated-then-killed mid-write, or read mid-write by a concurrent caller).
async function atomicWriteJSON(path, obj) {
  const tmp = `${path}.tmp.${randomUUID()}`;
  await writeFile(tmp, JSON.stringify(obj, null, 2));
  await rename(tmp, path);
}

// Atomically claim a single-use grant file (authorization code / refresh
// token) via `rename`, which is atomic on a given filesystem: exactly one of
// N concurrent callers racing the same source path wins the rename, every
// other caller's rename fails with ENOENT and gets `null`. This replaces the
// old read-then-unlink sequence, where two concurrent callers could both
// read the file before either unlinked it — both would treat the grant as
// valid, allowing a code to be redeemed twice or a refresh token replayed.
async function claimFile(path) {
  const claimed = `${path}.${randomUUID()}`;
  try {
    await rename(path, claimed);
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
  try {
    return JSON.parse(await readFile(claimed, 'utf8'));
  } finally {
    await unlink(claimed).catch(() => {});
  }
}

export function createStore(dir) {
  const clientsFile = join(dir, 'clients.json');
  const codesDir = join(dir, 'codes');
  const refreshDir = join(dir, 'refresh');

  async function ensureDirs() {
    await mkdir(dir, { recursive: true });
    await mkdir(codesDir, { recursive: true });
    await mkdir(refreshDir, { recursive: true });
  }

  // In-process write queue serializing every clients.json mutation. Without
  // this, concurrent saveClient calls each do read-modify-write against the
  // same in-memory snapshot of the file — the last writer to finish wins and
  // every other writer's registration is silently lost. Chaining through a
  // single promise forces mutations to run one at a time. `fn` is passed as
  // both the fulfillment and rejection handler so a failed mutation doesn't
  // wedge the queue — the next `serialize` call still runs even if the
  // previous one threw.
  let chain = Promise.resolve();
  function serialize(fn) {
    return (chain = chain.then(fn, fn));
  }

  async function getClient(id) {
    await ensureDirs();
    const clients = await readClients(clientsFile);
    return Object.hasOwn(clients, id) ? clients[id] : null;
  }

  async function saveClient(client) {
    await ensureDirs();
    return serialize(async () => {
      const clients = await readClients(clientsFile);
      clients[client.client_id] = client;

      const ids = Object.keys(clients);
      if (ids.length > MAX_CLIENTS) {
        // Stable sort by created_at ascending (oldest first); entries with
        // no created_at — pre-existing records from before this field
        // existed — sort as oldest so they're evicted first.
        ids.sort((a, b) => (clients[a].created_at ?? 0) - (clients[b].created_at ?? 0));
        const evictCount = ids.length - MAX_CLIENTS;
        const evicted = ids.slice(0, evictCount);
        for (const id of evicted) delete clients[id];
        console.warn(
          `[remote-mcp] client registry at cap (${MAX_CLIENTS}); evicted ${evictCount} oldest client(s): ${evicted.join(', ')}`
        );
      }

      await atomicWriteJSON(clientsFile, clients);
    });
  }

  async function saveCode(code, data) {
    await ensureDirs();
    const record = { ...data, exp: Date.now() + CODE_TTL_MS };
    await writeFile(join(codesDir, `${hashToken(code)}.json`), JSON.stringify(record));
  }

  async function takeCode(code) {
    await ensureDirs();
    const path = join(codesDir, `${hashToken(code)}.json`);
    const data = await claimFile(path);
    if (!data) return null;
    if (typeof data.exp !== 'number' || data.exp < Date.now()) return null;
    return data;
  }

  async function saveRefresh(token, data) {
    await ensureDirs();
    const record = { ...data, exp: Date.now() + REFRESH_TTL_MS };
    await writeFile(join(refreshDir, `${hashToken(token)}.json`), JSON.stringify(record));
  }

  async function takeRefresh(token) {
    await ensureDirs();
    const path = join(refreshDir, `${hashToken(token)}.json`);
    const data = await claimFile(path);
    if (!data) return null;
    if (typeof data.exp !== 'number' || data.exp < Date.now()) return null;
    return data;
  }

  return { getClient, saveClient, saveCode, takeCode, saveRefresh, takeRefresh };
}
