import { mkdir, readFile, appendFile } from 'node:fs/promises';
import { dirname } from 'node:path';

export async function ensureEnvVar(filePath, name, value) {
  await mkdir(dirname(filePath), { recursive: true });
  let body = '';
  try { body = await readFile(filePath, 'utf8'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  if (new RegExp(`^${name}=`, 'm').test(body)) return { added: false };
  await appendFile(filePath, `${name}="${value}"\n`);
  return { added: true };
}
