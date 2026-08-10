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

import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

const CODE_TTL_MS = 10 * 60 * 1000;
const REFRESH_TTL_MS = 30 * 24 * 60 * 60 * 1000;

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

export function createStore(dir) {
  const clientsFile = join(dir, 'clients.json');
  const codesDir = join(dir, 'codes');
  const refreshDir = join(dir, 'refresh');

  async function ensureDirs() {
    await mkdir(dir, { recursive: true });
    await mkdir(codesDir, { recursive: true });
    await mkdir(refreshDir, { recursive: true });
  }

  async function getClient(id) {
    await ensureDirs();
    const clients = (await readJsonSafe(clientsFile)) || {};
    return clients[id] || null;
  }

  async function saveClient(client) {
    await ensureDirs();
    const clients = (await readJsonSafe(clientsFile)) || {};
    clients[client.client_id] = client;
    await writeFile(clientsFile, JSON.stringify(clients, null, 2));
  }

  async function saveCode(code, data) {
    await ensureDirs();
    const record = { ...data, exp: Date.now() + CODE_TTL_MS };
    await writeFile(join(codesDir, `${hashToken(code)}.json`), JSON.stringify(record));
  }

  async function takeCode(code) {
    await ensureDirs();
    const path = join(codesDir, `${hashToken(code)}.json`);
    const data = await readJsonSafe(path);
    if (!data) return null;
    await unlink(path).catch(() => {});
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
    const data = await readJsonSafe(path);
    if (!data) return null;
    await unlink(path).catch(() => {});
    if (typeof data.exp !== 'number' || data.exp < Date.now()) return null;
    return data;
  }

  return { getClient, saveClient, saveCode, takeCode, saveRefresh, takeRefresh };
}
