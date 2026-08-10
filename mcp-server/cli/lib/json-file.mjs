import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

export async function readJson(path, fallback = {}) {
  try { return JSON.parse(await readFile(path, 'utf8')); }
  catch (err) { if (err.code === 'ENOENT') return fallback; throw err; }
}

export async function writeJson(path, obj) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(obj, null, 2) + '\n');
}

export function mergeHookSettings(settings, rendered) {
  return { ...settings, hooks: { ...(settings.hooks ?? {}), ...rendered.hooks } };
}

export function addPermissions(settings, tools) {
  const allow = [...new Set([...(settings.permissions?.allow ?? []), ...tools])];
  return { ...settings, permissions: { ...(settings.permissions ?? {}), allow } };
}

export function registerMcp(settings, { url, apiKey, extraEnv = {} }) {
  if (settings.mcpServers?.memories) return { settings, skipped: true };
  return {
    skipped: false,
    settings: {
      ...settings,
      mcpServers: {
        ...(settings.mcpServers ?? {}),
        memories: {
          command: 'npx',
          args: ['-y', 'memories-mcp'],
          env: { MEMORIES_URL: url, MEMORIES_API_KEY: apiKey ?? '', ...extraEnv },
        },
      },
    },
  };
}
