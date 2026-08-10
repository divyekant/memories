import { chmod, copyFile, mkdir, readFile, rm, writeFile, access } from 'node:fs/promises';
import { join } from 'node:path';
import { readJson, writeJson, addPermissions } from '../lib/json-file.mjs';
import { renderHooksJson, copyHookScripts, READONLY_MCP_TOOLS } from '../lib/hooks.mjs';
import { appendMarkedBlock, insertMarkedBlockAtRoot, removeMarkedBlock, hasTomlSection, hasTomlKey, ensureTomlStringKey, tomlEscape } from '../lib/toml.mjs';

const MARKER_NOTIFY = 'Memories Codex notify';
const MARKER_MCP = 'Memories Codex MCP';
const MARKER_DEV = 'Memories Codex developer instructions';

const DEVELOPER_INSTRUCTIONS = `developer_instructions = """
Use the Memories MCP tools as your memory layer with three responsibilities:

1. READ: Run memory_search before implementation-heavy responses, clarifying questions, or any turn that depends on prior decisions, prior sessions, project history, deferred work, conventions, or cross-session context. Hook-injected memories are useful hints, not a substitute for active search.
2. WRITE: Use memory_add for single clear facts (check memory_is_novel first). Use memory_extract for rich conversations, decision changes, or deferred work updates — it handles Add/Update/Delete/Noop automatically via AUDN. For scoped keys, always pass a non-empty source on memory_extract.
3. MAINTAIN: Use memory_delete for explicit forget requests. memory_extract handles most lifecycle updates automatically. For bulk cleanup with scoped keys, prefer prefix-based deletion patterns that stay inside authorized sources.

Source prefixes: replace {project} with the current working directory basename. Search exact project-scoped prefixes first: codex/{project}, claude-code/{project}, learning/{project}, and wip/{project}. If hook candidate pointers list a source, use that exact source_prefix. Do not use broad family prefixes like codex/, claude-code/, learning/, wip/, or unscoped search until the exact project prefixes have been tried. Use only authorized prefixes when scoped keys restrict access.
"""`;

const exists = (p) => access(p).then(() => true, () => false);

const paths = (ctx) => ({
  hooksSrc: join(ctx.assetsDir, 'codex/hooks'),
  hooksDest: join(ctx.home, '.codex/hooks/memory'),
  hooksJson: join(ctx.home, '.codex/hooks.json'),
  notifySrc: join(ctx.assetsDir, 'codex/memory-codex-notify.sh'),
  settings: join(ctx.home, '.codex/settings.json'),
  config: join(ctx.home, '.codex/config.toml'),
});

function mergeCodexHooks(existing, rendered) {
  const events = new Set([...Object.keys(rendered.hooks ?? {}), ...Object.keys(existing.hooks ?? {})]);
  const hooks = {};
  for (const k of events) {
    const combined = [...(rendered.hooks?.[k] ?? []), ...(existing.hooks?.[k] ?? [])];
    const seen = new Set();
    hooks[k] = combined.filter((e) => {
      const key = e.hooks?.[0]?.command ?? JSON.stringify(e);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  return { ...existing, hooks };
}

export async function install(ctx) {
  const p = paths(ctx);

  await copyHookScripts(p.hooksSrc, p.hooksDest);
  await copyFile(p.notifySrc, join(p.hooksDest, 'memory-codex-notify.sh'));
  await chmod(join(p.hooksDest, 'memory-codex-notify.sh'), 0o755);

  const hooksConfig = JSON.parse(await readFile(join(p.hooksSrc, 'hooks.json'), 'utf8'));
  const rendered = renderHooksJson(hooksConfig, p.hooksDest);
  const existingHooks = await readJson(p.hooksJson);
  await writeJson(p.hooksJson, mergeCodexHooks(existingHooks, rendered));

  let settings = await readJson(p.settings);
  settings = addPermissions(settings, READONLY_MCP_TOOLS);
  await writeJson(p.settings, settings);

  await mkdir(join(ctx.home, '.codex'), { recursive: true });
  let toml = (await exists(p.config)) ? await readFile(p.config, 'utf8') : '';
  if (!hasTomlSection(toml, 'mcp_servers.memories')) {
    const mcpBlock = `[mcp_servers.memories]
command = "npx"
args = ["-y", "memories-mcp"]

[mcp_servers.memories.env]
MEMORIES_URL = "${tomlEscape(ctx.url)}"
MEMORIES_API_KEY = "${tomlEscape(ctx.apiKey)}"
MEMORIES_CLIENT = "codex"`;
    toml = appendMarkedBlock(toml, MARKER_MCP, mcpBlock);
  }
  toml = ensureTomlStringKey(toml, 'mcp_servers.memories.env', 'MEMORIES_CLIENT', 'codex');

  if (!hasTomlKey(toml, 'developer_instructions')) {
    toml = insertMarkedBlockAtRoot(toml, MARKER_DEV, DEVELOPER_INSTRUCTIONS);
  }
  await writeFile(p.config, toml);

  ctx.log(`Codex wired (hooks: ${p.hooksDest})`);
}

export async function uninstall(ctx) {
  const p = paths(ctx);
  await rm(p.hooksDest, { recursive: true, force: true });

  if (await exists(p.hooksJson)) {
    const hooksJson = await readJson(p.hooksJson);
    if (hooksJson.hooks) {
      for (const [event, entries] of Object.entries(hooksJson.hooks)) {
        const kept = entries
          .map((e) => ({ ...e, hooks: (e.hooks ?? []).filter((h) => !String(h.command ?? '').includes('/hooks/memory/memory-')) }))
          .filter((e) => e.hooks.length > 0);
        if (kept.length) hooksJson.hooks[event] = kept; else delete hooksJson.hooks[event];
      }
    }
    await writeJson(p.hooksJson, hooksJson);
  }

  if (await exists(p.config)) {
    let toml = await readFile(p.config, 'utf8');
    toml = removeMarkedBlock(toml, MARKER_NOTIFY);
    toml = removeMarkedBlock(toml, MARKER_MCP);
    toml = removeMarkedBlock(toml, MARKER_DEV);
    await writeFile(p.config, toml);
  }

  ctx.log('Codex integration removed');
}

export async function status(ctx) {
  const p = paths(ctx);
  const hooks = await exists(p.hooksDest);
  const toml = (await exists(p.config)) ? await readFile(p.config, 'utf8') : '';
  const mcp = hasTomlSection(toml, 'mcp_servers.memories');
  return { installed: hooks && mcp, details: [`hooks: ${hooks}`, `mcp: ${mcp}`] };
}
