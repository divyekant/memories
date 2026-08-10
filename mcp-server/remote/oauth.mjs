// Single-user OAuth 2.1 authorization server for the claude.ai connector flow:
// authorization-code + mandatory PKCE(S256) + Dynamic Client Registration,
// public clients only (token_endpoint_auth_method "none"), refresh rotation.
// No client_credentials — there is exactly one user ("dk") and one password.
//
// Functions here are pure-ish: they take plain objects and return plain
// {status, body} / {redirect} data. All I/O goes through the injected `store`
// (see store.mjs). No HTTP framework lives in this file — Task 3 wires these
// into express routes.

import { randomBytes, createHmac, createHash, timingSafeEqual, scryptSync } from 'node:crypto';
import { renderLogin } from './login.mjs';

const ACCESS_TOKEN_TTL_S = 3600;
const SCRYPT_N = 16384;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const SCRYPT_KEYLEN = 64;

const AUTHORIZE_PARAMS = ['client_id', 'redirect_uri', 'state', 'code_challenge', 'code_challenge_method', 'scope'];

// ---------------------------------------------------------------------------
// Password hashing (node:crypto scrypt, constant-time compare)
// ---------------------------------------------------------------------------

export function hashPassword(password) {
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, SCRYPT_KEYLEN, { N: SCRYPT_N, r: SCRYPT_R, p: SCRYPT_P });
  return `scrypt:${salt.toString('base64')}:${hash.toString('base64')}`;
}

export function verifyPassword(password, stored) {
  try {
    const parts = String(stored).split(':');
    if (parts.length !== 3 || parts[0] !== 'scrypt') return false;
    const salt = Buffer.from(parts[1], 'base64');
    const expected = Buffer.from(parts[2], 'base64');
    if (salt.length === 0 || expected.length === 0) return false;
    const actual = scryptSync(password, salt, expected.length, { N: SCRYPT_N, r: SCRYPT_R, p: SCRYPT_P });
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Redirect-URI allowlist for DCR
// ---------------------------------------------------------------------------

function isAllowedRedirectUri(uriStr) {
  let url;
  try {
    url = new URL(uriStr);
  } catch {
    return false;
  }
  const host = url.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1';
  // https required except on localhost/127.0.0.1 — a redirect_uri is where
  // the authorization code lands, so http on a real host would let it be
  // intercepted in transit.
  if (url.protocol !== 'https:' && !(isLocal && url.protocol === 'http:')) return false;
  return (
    host === 'claude.ai' ||
    host.endsWith('.claude.ai') ||
    host === 'claude.com' ||
    host.endsWith('.claude.com') ||
    isLocal
  );
}

const MAX_REDIRECT_URIS = 10;
const MAX_CLIENT_NAME_LENGTH = 200;
const MAX_REDIRECT_URI_LENGTH = 2048;

// ---------------------------------------------------------------------------
// Deterministic client_id (dedup)
// ---------------------------------------------------------------------------
// client_id is derived from redirect_uris alone (client_name excluded) so a
// client re-registering — which claude.ai does on essentially every
// connection — always lands on the same registry slot instead of minting a
// fresh one. A random id per call meant a registration flood (or even just
// claude.ai's own normal reconnect traffic) could grow the registry
// unbounded and, combined with the old "evict oldest" policy, eventually
// evict the very client actually in use. Sorting redirect_uris before
// hashing makes the id independent of the array's submitted order.

function deriveClientId(redirectUris) {
  const canonical = JSON.stringify({ redirect_uris: [...redirectUris].sort() });
  return `c_${createHash('sha256').update(canonical).digest('hex')}`;
}

// ---------------------------------------------------------------------------
// PKCE
// ---------------------------------------------------------------------------

function s256(verifier) {
  return createHash('sha256').update(verifier).digest('base64url');
}

function constantTimeStringEqual(a, b) {
  const bufA = Buffer.from(String(a), 'utf8');
  const bufB = Buffer.from(String(b), 'utf8');
  return bufA.length === bufB.length && timingSafeEqual(bufA, bufB);
}

// ---------------------------------------------------------------------------
// Access tokens: base64url(payload).base64url(hmacSHA256(payload, secret))
// ---------------------------------------------------------------------------

function signAccessToken(payload, secret) {
  const payloadB64 = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = createHmac('sha256', secret).update(payloadB64).digest('base64url');
  return `${payloadB64}.${sig}`;
}

export function createOAuth({ issuer, passwordHash, tokenSecret, store }) {
  function metadataAS() {
    return {
      issuer,
      authorization_endpoint: `${issuer}/authorize`,
      token_endpoint: `${issuer}/token`,
      registration_endpoint: `${issuer}/register`,
      response_types_supported: ['code'],
      grant_types_supported: ['authorization_code', 'refresh_token'],
      code_challenge_methods_supported: ['S256'],
      token_endpoint_auth_methods_supported: ['none'],
    };
  }

  function metadataPR() {
    return {
      resource: `${issuer}/mcp`,
      authorization_servers: [issuer],
    };
  }

  async function register(body) {
    const redirectUris = Array.isArray(body?.redirect_uris) ? body.redirect_uris : null;
    if (!redirectUris || redirectUris.length === 0) {
      return { status: 400, body: { error: 'invalid_redirect_uri' } };
    }
    // Bounded before any per-URI validation work — an unauthenticated caller
    // could otherwise submit an arbitrarily large array to burn CPU/storage.
    if (redirectUris.length > MAX_REDIRECT_URIS) {
      return { status: 400, body: { error: 'invalid_client_metadata' } };
    }
    // Bounded before URL parsing/host validation for the same reason as the
    // count cap above — an unauthenticated caller could otherwise submit a
    // multi-megabyte redirect_uri to burn CPU/storage on every registration.
    if (redirectUris.some((uri) => typeof uri !== 'string' || uri.length > MAX_REDIRECT_URI_LENGTH)) {
      return { status: 400, body: { error: 'invalid_client_metadata' } };
    }
    if (!redirectUris.every(isAllowedRedirectUri)) {
      return { status: 400, body: { error: 'invalid_redirect_uri' } };
    }
    if (typeof body.client_name === 'string' && body.client_name.length > MAX_CLIENT_NAME_LENGTH) {
      return { status: 400, body: { error: 'invalid_client_metadata' } };
    }

    const clientId = deriveClientId(redirectUris);
    const clientName = typeof body.client_name === 'string' && body.client_name ? body.client_name : 'Claude';
    const now = Date.now();

    // Re-registration with the same redirect_uris set lands on the same
    // client_id — update in place (name can change, e.g. a client rename)
    // rather than minting a new registry slot. This is the common case:
    // claude.ai re-runs DCR on essentially every connection.
    //
    // No pre-read here (PR 83 follow-up — queue-boundary race): the
    // previous version called store.getClient() outside the write queue,
    // then wrote that snapshot inside it via saveClient(). A
    // markClientActive() landing in the gap between the read and the write
    // got silently clobbered — a concurrent re-registration could erase
    // activated_at, making a live client evictable again. upsertClient()
    // does the read, the merge, and the write in one serialized step, so
    // created_at/activated_at always survive regardless of what races
    // with it — this updateFn doesn't even need to know whether the
    // client existed before or was ever activated.
    const result = await store.upsertClient(clientId, () => ({
      client_id: clientId,
      redirect_uris: redirectUris,
      client_name: clientName,
      token_endpoint_auth_method: 'none',
      created_at: now, // reasserted to the original value by upsertClient if this client already existed
      last_registered_at: now,
    }));

    if (!result.ok) {
      // Registry is at MAX_CLIENTS and every occupant is activated (a real
      // user has completed login+consent for it) — nothing safe to evict.
      // Reject rather than displace a live client for an unauthenticated
      // registration attempt.
      return {
        status: 429,
        body: { error: 'temporarily_unavailable', error_description: 'client registry full' },
      };
    }
    return { status: 201, body: result.client };
  }

  // Shared validation for both the GET (render form) and POST (submit password)
  // sides of /authorize. Returns { error status+body } or { client } on success.
  async function validateAuthorizeRequest(query) {
    const client = query?.client_id ? await store.getClient(query.client_id) : null;
    if (!client) {
      return { error: { status: 400, body: 'Unknown client_id.' } };
    }
    if (!client.redirect_uris.includes(query.redirect_uri)) {
      return { error: { status: 400, body: 'redirect_uri is not registered for this client.' } };
    }
    if (query.code_challenge_method !== 'S256' || !query.code_challenge) {
      return { error: { status: 400, body: 'PKCE (code_challenge with S256) is required.' } };
    }
    return { client };
  }

  function paramsFor(query) {
    return Object.fromEntries(AUTHORIZE_PARAMS.map((k) => [k, query?.[k]]));
  }

  async function authorizePage(query) {
    const { error, client } = await validateAuthorizeRequest(query);
    if (error) return error;
    return { status: 200, body: renderLogin({ clientName: client.client_name, params: paramsFor(query) }) };
  }

  async function handleAuthorize(body) {
    const { error, client } = await validateAuthorizeRequest(body);
    if (error) return error;

    if (!verifyPassword(body.password || '', passwordHash)) {
      return {
        status: 200,
        body: renderLogin({
          clientName: client.client_name,
          error: 'Incorrect password.',
          params: paramsFor(body),
        }),
      };
    }

    const code = randomBytes(32).toString('base64url');
    await store.saveCode(code, {
      cid: client.client_id,
      redirect_uri: body.redirect_uri,
      challenge: body.code_challenge,
    });

    const redirectUrl = new URL(body.redirect_uri);
    redirectUrl.searchParams.set('code', code);
    if (body.state !== undefined) redirectUrl.searchParams.set('state', body.state);
    return { redirect: redirectUrl.toString() };
  }

  function issueTokens(cid) {
    const now = Math.floor(Date.now() / 1000);
    const accessToken = signAccessToken({ sub: 'dk', iat: now, exp: now + ACCESS_TOKEN_TTL_S, cid }, tokenSecret);
    return { accessToken };
  }

  async function grantAuthorizationCode(body) {
    if (!body.code || !body.redirect_uri || !body.code_verifier || !body.client_id) {
      return { status: 400, body: { error: 'invalid_request' } };
    }
    const record = await store.takeCode(body.code);
    if (!record) return { status: 400, body: { error: 'invalid_grant' } };
    if (record.redirect_uri !== body.redirect_uri) return { status: 400, body: { error: 'invalid_grant' } };
    if (record.cid !== body.client_id) return { status: 400, body: { error: 'invalid_grant' } };
    if (!constantTimeStringEqual(s256(body.code_verifier), record.challenge)) {
      return { status: 400, body: { error: 'invalid_grant' } };
    }

    // A successful authorization_code grant means a human completed the
    // login+consent form for this client — mark it activated so it's never
    // an eviction candidate for a registration flood (see store.mjs's
    // saveClient/markClientActive).
    await store.markClientActive(record.cid);

    const { accessToken } = issueTokens(record.cid);
    const refreshToken = randomBytes(32).toString('base64url');
    await store.saveRefresh(refreshToken, { cid: record.cid });

    return {
      status: 200,
      body: { access_token: accessToken, token_type: 'bearer', expires_in: ACCESS_TOKEN_TTL_S, refresh_token: refreshToken },
    };
  }

  async function grantRefreshToken(body) {
    if (!body.refresh_token) {
      return { status: 400, body: { error: 'invalid_request' } };
    }
    const record = await store.takeRefresh(body.refresh_token);
    if (!record) return { status: 400, body: { error: 'invalid_grant' } };

    const { accessToken } = issueTokens(record.cid);
    const newRefreshToken = randomBytes(32).toString('base64url');
    await store.saveRefresh(newRefreshToken, { cid: record.cid });

    return {
      status: 200,
      body: { access_token: accessToken, token_type: 'bearer', expires_in: ACCESS_TOKEN_TTL_S, refresh_token: newRefreshToken },
    };
  }

  async function token(body) {
    if (body?.grant_type === 'authorization_code') return grantAuthorizationCode(body);
    if (body?.grant_type === 'refresh_token') return grantRefreshToken(body);
    return { status: 400, body: { error: 'unsupported_grant_type' } };
  }

  function verifyAccess(accessToken) {
    if (typeof accessToken !== 'string' || !accessToken) return { ok: false, error: 'invalid_token' };
    const parts = accessToken.split('.');
    if (parts.length !== 2) return { ok: false, error: 'invalid_token' };
    const [payloadB64, sigB64] = parts;

    const expectedSig = createHmac('sha256', tokenSecret).update(payloadB64).digest('base64url');
    if (!constantTimeStringEqual(sigB64, expectedSig)) {
      return { ok: false, error: 'invalid_token' };
    }

    let payload;
    try {
      payload = JSON.parse(Buffer.from(payloadB64, 'base64url').toString('utf8'));
    } catch {
      return { ok: false, error: 'invalid_token' };
    }

    if (typeof payload.exp !== 'number' || payload.exp < Math.floor(Date.now() / 1000)) {
      return { ok: false, error: 'expired_token' };
    }

    return { ok: true, subject: payload.sub };
  }

  return { metadataAS, metadataPR, register, authorizePage, handleAuthorize, token, verifyAccess };
}
