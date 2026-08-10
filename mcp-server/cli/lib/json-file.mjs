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
  const events = new Set([...Object.keys(rendered.hooks ?? {}), ...Object.keys(settings.hooks ?? {})]);
  const hooks = {};
  for (const k of events) {
    const renderedEntries = rendered.hooks?.[k] ?? [];
    const existingEntries = settings.hooks?.[k] ?? [];
    // Dedupe by individual hook COMMAND, not by whole-entry identity — an
    // existing entry can pack a foreign hook alongside ours in the same
    // matcher (hooks: [ours, foreign]); whole-entry dedup on hooks[0].command
    // used to discard that entry wholesale, losing the foreign hook at
    // index >= 1. Instead, strip only the commands we're re-rendering from
    // each existing entry, and keep the entry if anything foreign remains.
    const renderedCommands = new Set(renderedEntries.flatMap((e) => (e.hooks ?? []).map((h) => h.command)));
    const survivingExisting = existingEntries
      .map((e) => ({ ...e, hooks: (e.hooks ?? []).filter((h) => !renderedCommands.has(h.command)) }))
      .filter((e) => e.hooks.length > 0);
    hooks[k] = [...renderedEntries, ...survivingExisting];
  }
  return { ...settings, hooks };
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
