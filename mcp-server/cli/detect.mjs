import { access } from 'node:fs/promises';
import { join } from 'node:path';

const exists = (p) => access(p).then(() => true, () => false);

export async function detectAgents(home) {
  return {
    'claude-code': await exists(join(home, '.claude')),
    codex: (await exists(join(home, '.codex'))) || (await exists(join(home, '.codex', 'config.toml'))),
    cursor: await exists(join(home, '.cursor')),
  };
}
