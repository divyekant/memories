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
  return (
    host === 'claude.ai' ||
    host.endsWith('.claude.ai') ||
    host === 'claude.com' ||
    host.endsWith('.claude.com') ||
    host === 'localhost' ||
    host === '127.0.0.1'
  );
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
    if (!redirectUris.every(isAllowedRedirectUri)) {
      return { status: 400, body: { error: 'invalid_redirect_uri' } };
    }
    const client = {
      client_id: randomBytes(16).toString('base64url'),
      redirect_uris: redirectUris,
      client_name: typeof body.client_name === 'string' && body.client_name ? body.client_name : 'Claude',
      token_endpoint_auth_method: 'none',
    };
    await store.saveClient(client);
    return { status: 201, body: client };
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
