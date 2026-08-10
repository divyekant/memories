import { access, copyFile, mkdir } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { join } from 'node:path';
import { ensureEnvVar } from './lib/env-file.mjs';

const defaultExecImpl = promisify(execFile);
const defaultSleepImpl = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const exists = (p) => access(p).then(() => true, () => false);

export async function checkHealth(url, { fetchImpl = globalThis.fetch } = {}) {
  try {
    const res = await fetchImpl(`${url}/health`, { signal: AbortSignal.timeout(3000) });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    const body = await res.json();
    return { ok: true, totalMemories: body.total_memories ?? 0 };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

export async function bootstrapBackend(ctx) {
  const {
    home,
    assetsDir,
    url,
    extract,
    execImpl = defaultExecImpl,
    fetchImpl = globalThis.fetch,
    sleepImpl = defaultSleepImpl,
  } = ctx;

  const configDir = join(home, '.config/memories');
  const composePath = join(configDir, 'docker-compose.yml');
  const envPath = join(configDir, 'env');

  await mkdir(configDir, { recursive: true });
  if (!(await exists(composePath))) {
    await copyFile(join(assetsDir, 'backend/docker-compose.standalone.yml'), composePath);
  }

  if (extract) {
    await ensureEnvVar(envPath, 'EXTRACT_PROVIDER', extract.provider);
    if (extract.keyVar && extract.keyVal) {
      await ensureEnvVar(envPath, extract.keyVar, extract.keyVal);
    }
  }

  try {
    await execImpl('docker', ['compose', '-f', composePath, 'up', '-d']);
  } catch (err) {
    throw new Error(`docker compose failed — is Docker installed and running? ${err.message}`);
  }

  let result = { ok: false, error: 'not checked' };
  for (let i = 0; i < 12; i++) {
    result = await checkHealth(url, { fetchImpl });
    if (result.ok) return result;
    await sleepImpl(5000);
  }
  return result;
}
