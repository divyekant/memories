import { chmod, copyFile, mkdir, readdir } from 'node:fs/promises';
import { basename, join } from 'node:path';

export const READONLY_MCP_TOOLS = [
  'mcp__memories__memory_search', 'mcp__memories__memory_list',
  'mcp__memories__memory_count', 'mcp__memories__memory_stats',
  'mcp__memories__memory_is_novel', 'mcp__memories__memory_is_useful',
  'mcp__memories__memory_conflicts',
];

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
  const names = (await readdir(srcDir)).filter(
    (n) => (n.startsWith('memory-') && n.endsWith('.sh')) || n === '_lib.sh' || n === 'response-hints.json',
  );
  for (const n of names) {
    await copyFile(join(srcDir, n), join(destDir, n));
    if (n.endsWith('.sh')) await chmod(join(destDir, n), 0o755);
  }
}
