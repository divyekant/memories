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

// Hard cap on the DCR-registered client registry. `upsertClient` evicts the
// oldest never-activated entry to make room for a new client once this is
// hit — an unauthenticated /register endpoint must never be able to grow
// clients.json without bound. Activated clients (a real user has completed
// login+consent for them — see markClientActive) are never eviction
// candidates: a registration flood must not be able to displace a client
// that's actually in live use (PR 83 follow-up — the original "evict
// oldest regardless of activation" policy let a flood displace claude.ai
// itself).
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
    const raw = await readFile(claimed, 'utf8');
    try {
      return JSON.parse(raw);
    } catch {
      // A corrupt (non-JSON) grant file should never happen — these are
      // only ever written by saveCode/saveRefresh — but if disk corruption
      // or a partial write somehow produces one, treat it the same as a
      // missing grant (null -> caller returns invalid_grant) instead of
      // letting JSON.parse's SyntaxError bubble into a 500.
      return null;
    }
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
  // this, concurrent mutations each do read-modify-write against the same
  // in-memory snapshot of the file — the last writer to finish wins and
  // every other writer's change is silently lost. Chaining through a
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

  // Runs `updateFn` against the client currently stored at `clientId`
  // entirely inside the write queue — the read, the merge, the eviction
  // check, and the write all happen in one serialized step. This closes a
  // race that existed when a caller (register(), originally) read a client
  // OUTSIDE the queue via getClient() and only entered the queue later, to
  // write: a second mutation (markClientActive) could land on the queue in
  // between, and the first caller's eventual write — built from the
  // now-stale pre-queue snapshot — would clobber it. Concretely, a
  // concurrent re-registration could silently erase activated_at, making a
  // client a real user was actively using evictable again (PR 83
  // follow-up).
  //
  // `updateFn(current)` receives the client currently stored at `clientId`,
  // or `null` if it isn't registered yet, and returns the record to
  // persist. Returning `undefined` means "no-op" (e.g. markClientActive on
  // an unknown id) — nothing is written and `{ ok: false }` comes back.
  //
  // Server-owned fields (`created_at`, `activated_at`) are re-applied to
  // whatever `updateFn` returns, unconditionally, using the `current`
  // record read inside this same serialized step. That's what makes the
  // race impossible rather than just less likely: register()'s updateFn
  // builds a brand-new record from submitted metadata with no idea whether
  // an activation landed a moment ago, and doesn't need to — this function
  // reasserts the truth from a snapshot taken atomically alongside the
  // write, not from anything the caller read earlier.
  //
  // Also applies the MAX_CLIENTS eviction policy for brand-new client_ids
  // (never for updates to an existing id — those never consume a new slot).
  // Returns `{ ok: false }` when the registry is at MAX_CLIENTS and every
  // occupant is already activated — nothing safe to evict.
  async function upsertClient(clientId, updateFn) {
    // `ensureDirs()` deliberately runs INSIDE the serialized closure, not
    // awaited here first. `serialize(fn)` captures this call's place in the
    // queue synchronously (`chain = chain.then(fn, fn)`), so as long as
    // nothing async happens between two callers' `upsertClient(...)`
    // invocations and their `serialize(...)` calls, invocation order IS
    // queue order. Awaiting `ensureDirs()` (real `mkdir` I/O) before
    // `serialize()` broke that: two calls issued back-to-back could resolve
    // their `ensureDirs()` in either order depending on filesystem timing,
    // so the SECOND caller could win the race to `serialize()` and jump the
    // queue ahead of the first — invocation order and write order could
    // diverge.
    return serialize(async () => {
      await ensureDirs();
      const clients = await readClients(clientsFile);
      const current = Object.hasOwn(clients, clientId) ? clients[clientId] : null;

      const next = updateFn(current);
      if (next === undefined) {
        return { ok: false };
      }

      if (current) {
        next.created_at = current.created_at;
        if (current.activated_at) next.activated_at = current.activated_at;
      }
      next.client_id = clientId;

      const isNewClient = !current;
      if (isNewClient) {
        const ids = Object.keys(clients);
        if (ids.length >= MAX_CLIENTS) {
          const evictable = ids.filter((id) => !clients[id].activated_at);
          if (evictable.length === 0) {
            return { ok: false };
          }
          // Stable sort by created_at ascending (oldest first); entries
          // with no created_at — pre-existing records from before this
          // field existed — sort as oldest so they're evicted first.
          evictable.sort((a, b) => (clients[a].created_at ?? 0) - (clients[b].created_at ?? 0));
          const victim = evictable[0];
          console.warn(
            `[remote-mcp] client registry at cap (${MAX_CLIENTS}); evicted never-activated client: ${victim}`
          );
          delete clients[victim];
        }
      }

      clients[clientId] = next;
      await atomicWriteJSON(clientsFile, clients);
      return { ok: true, client: next };
    });
  }

  // Thin wrapper over upsertClient for callers — mainly tests — that
  // already have a complete record to persist rather than an updateFn.
  // Note this still goes through the same atomic upsert path: server-owned
  // fields on an existing record win over whatever `client` contains, and
  // the eviction policy still applies for a brand-new client_id.
  async function saveClient(client) {
    const result = await upsertClient(client.client_id, () => ({ ...client }));
    return result.ok;
  }

  // Marks a client as activated — a real user has completed a login+consent
  // flow for it (called from oauth.mjs on a successful authorization_code
  // grant). Idempotent: only the first call sets activated_at, so its
  // timestamp reflects when the client first went live, not its most recent
  // token grant. Returns false for an unknown client_id without throwing or
  // creating a phantom record.
  async function markClientActive(clientId) {
    const result = await upsertClient(clientId, (current) => {
      if (!current) return undefined;
      return { ...current, activated_at: current.activated_at || Date.now() };
    });
    return result.ok;
  }

  // Like markClientActive, but resurrects a MISSING client instead of
  // no-op'ing. Needed for the authorize->token window: a client that just
  // finished /authorize (code issued, but not yet activated — activation
  // only happens on a successful /token exchange) can be evicted by a
  // concurrent /register flood before /token runs, because outstanding
  // codes live in codesDir, entirely separate from the client registry —
  // takeCode() still succeeds against an evicted client's code. If
  // grantAuthorizationCode then called plain markClientActive(cid), it
  // would find no client (current === null), its updateFn would return
  // `undefined`, and upsertClient would silently no-op — leaving the
  // client permanently absent and perpetually flood-evictable even though
  // a human just completed login+consent for it (PR 83 follow-up).
  //
  // Recreating a minimal activated record here is safe specifically
  // because the caller is grantAuthorizationCode: the code was only ever
  // issued by /authorize after validating client_id + redirect_uri
  // against the (then-existing) client record, and grantAuthorizationCode
  // re-validates the redeemed redirect_uri against the code record before
  // calling this. `redirectUri` is that already-twice-validated value, not
  // caller-supplied at this point.
  async function activateOrCreate(clientId, { redirectUri }) {
    const result = await upsertClient(clientId, (current) => {
      if (current) {
        return { ...current, activated_at: current.activated_at || Date.now() };
      }
      return {
        client_id: clientId,
        redirect_uris: [redirectUri],
        client_name: '(reactivated)',
        token_endpoint_auth_method: 'none',
        created_at: Date.now(),
        activated_at: Date.now(),
      };
    });
    if (!result.ok) {
      // Registry is at MAX_CLIENTS and every occupant is already activated
      // — there's no slot to resurrect this client into. Don't fail the
      // grant over it: the user already consented and the tokens being
      // issued are valid regardless of whether the client record persists.
      // The cost is narrow — this client stays vulnerable to the same
      // eviction race on its NEXT authorize->token window — not a security
      // regression versus today, just an unresolved edge case logged for
      // visibility.
      console.warn(
        `[remote-mcp] activateOrCreate: registry full of activated clients — could not persist ` +
        `resurrected client ${clientId}; proceeding with token issuance anyway`
      );
    }
    return result.ok;
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

  return {
    getClient,
    saveClient,
    upsertClient,
    markClientActive,
    activateOrCreate,
    saveCode,
    takeCode,
    saveRefresh,
    takeRefresh,
  };
}
