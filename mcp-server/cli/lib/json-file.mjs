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

// Drops allow-rules matching `predicate`, leaving every other rule in place.
// Empty containers are deleted rather than left as `{}`/`[]`, matching how
// uninstall already prunes `mcpServers` and `hooks`.
export function removePermissions(settings, predicate) {
  const current = settings.permissions?.allow;
  if (!Array.isArray(current)) return settings;
  const allow = current.filter((rule) => !predicate(rule));
  if (allow.length === current.length) return settings;
  const permissions = { ...settings.permissions };
  if (allow.length) permissions.allow = allow;
  else delete permissions.allow;
  const out = { ...settings, permissions };
  if (Object.keys(permissions).length === 0) delete out.permissions;
  return out;
}

// `persistApiKey: false` omits MEMORIES_API_KEY from the written entry. The
// server reads process.env.MEMORIES_API_KEY (mcp-server/index.js), so where the
// credential already exists as a real environment variable — a cloud
// environment's variable box — writing a second copy into settings.json buys
// nothing and leaves a live credential in any config dump. Default stays true:
// a local install has no such variable, so the key must be persisted there.
export function registerMcp(settings, { url, apiKey, extraEnv = {}, persistApiKey = true }) {
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
          env: {
            MEMORIES_URL: url,
            ...(persistApiKey ? { MEMORIES_API_KEY: apiKey ?? '' } : {}),
            ...extraEnv,
          },
        },
      },
    },
  };
}
