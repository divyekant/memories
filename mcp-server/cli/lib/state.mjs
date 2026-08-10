import { join } from 'node:path';
import { readJson, writeJson } from './json-file.mjs';

// Tracks which targets THIS CLI explicitly installed (as opposed to targets
// wired transitively by another adapter's delegation, e.g. cursor.install
// calling claude-code.install). Used at uninstall time to know whether a
// shared piece of wiring (the ~/.claude side) is safe to tear down.
const statePath = (home) => join(home, '.config/memories/state.json');

export async function readState(home) {
  return readJson(statePath(home), { installedTargets: [] });
}

export async function writeState(home, state) {
  await writeJson(statePath(home), state);
}

export { statePath };
