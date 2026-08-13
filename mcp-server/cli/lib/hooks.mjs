import { chmod, copyFile, mkdir, readdir } from 'node:fs/promises';
import { basename, join } from 'node:path';

// Claude Code MCP allow-rule tool globs only match after a literal,
// glob-free server segment (`mcp__<server>__tool*`) — an unanchored server
// segment (`mcp__*__memory_search`) is skipped with a warning and approves
// nothing. So a server registered under a non-default name (a claude.ai
// connector, or a manual rename) needs its own explicit rule set; there is
// no single rule that covers an unknown name. See readonlyMcpTools().
export const READONLY_MCP_TOOL_NAMES = [
  'memory_search', 'memory_list', 'memory_count', 'memory_stats',
  'memory_is_novel', 'memory_conflicts',
];

// Returns the 6 read-only memory-tool allow-rules for a given MCP server
// name. Deliberately NOT `mcp__<server>__*` — that would also pre-approve
// destructive tools (memory_delete, memory_update, ...), defeating the
// point of a read-only allowlist.
export function readonlyMcpTools(serverName = 'memories') {
  return READONLY_MCP_TOOL_NAMES.map((tool) => `mcp__${serverName}__${tool}`);
}

// Back-compat export: the default-name rule set.
export const READONLY_MCP_TOOLS = readonlyMcpTools();

export function renderHooksJson(config, hooksDir) {
  const out = structuredClone(config);
  for (const entries of Object.values(out.hooks ?? {}))
    for (const entry of entries)
      for (const h of entry.hooks ?? [])
        if (h.type === 'command') h.command = join(hooksDir, basename(h.command));
  return out;
}

export async function copyHookScripts(srcDir, destDir) {
  await mkdir(destDir, { recursive: true });
  // Keep lifecycle additions (including version-gated Codex hooks) data-driven:
  // every shipped memory hook script is copied without maintaining a second
  // allowlist here. Sort for stable installs and deterministic pack tests.
  const names = (await readdir(srcDir)).filter(
    (n) => (n.startsWith('memory-') && n.endsWith('.sh')) || n === '_lib.sh' || n === 'response-hints.json',
  ).sort();
  for (const n of names) {
    await copyFile(join(srcDir, n), join(destDir, n));
    if (n.endsWith('.sh')) await chmod(join(destDir, n), 0o755);
  }
}
