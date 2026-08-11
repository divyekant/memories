import { readJson, writeJson } from './json-file.mjs';

// Records what this installer actually wrote into a user's settings, so
// uninstall can remove exactly that and nothing else.
//
// Permission allow-rules cannot be identified by shape at uninstall time: a
// rule like `mcp__<server>__memory_search` is indistinguishable from an
// unrelated memory product's rule, and the server name is a free-form
// `--mcp-name` value we cannot re-derive. So we persist the rules we added,
// in a file this integration owns.
export const installStatePath = (home) => `${home}/.config/memories/install-state.json`;

// Records `rules` as owned by `target`, unioned with anything already
// recorded — a repeat `init`/`update` with a different --mcp-name must not
// drop the names an earlier run added.
export async function recordPermissions(path, target, rules) {
  if (!rules.length) return;
  const state = await readJson(path);
  const permissions = { ...(state.permissions ?? {}) };
  permissions[target] = [...new Set([...(permissions[target] ?? []), ...rules])];
  await writeJson(path, { ...state, permissions });
}

// Returns the rules recorded for `target`, or null when nothing was ever
// recorded — the caller distinguishes "we added none" (an empty array) from
// "this predates the manifest" (null) and must not treat them alike.
//
// Reading is deliberately non-destructive. An uninstall that throws partway
// through gets retried, and the retry needs the same provenance: consuming
// the record up front would strand the very rules it identifies (the
// artifacts it would otherwise infer ownership from are already gone by
// then). Callers clear the entry with clearRecordedPermissions only after
// the cleanup that depends on it has succeeded.
export async function readRecordedPermissions(path, target) {
  const recorded = (await readJson(path)).permissions?.[target];
  return Array.isArray(recorded) ? recorded : null;
}

export async function clearRecordedPermissions(path, target) {
  const state = await readJson(path);
  if (!state.permissions || !(target in state.permissions)) return;
  const permissions = { ...state.permissions };
  delete permissions[target];
  const next = { ...state };
  if (Object.keys(permissions).length) next.permissions = permissions;
  else delete next.permissions;
  await writeJson(path, next);
}
