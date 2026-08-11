import { cp, mkdir, readFile, rm, writeFile, access } from 'node:fs/promises';
import { join } from 'node:path';
import { readJson, writeJson, mergeHookSettings, addPermissions, removePermissions, registerMcp } from '../lib/json-file.mjs';
import { renderHooksJson, copyHookScripts, readonlyMcpTools } from '../lib/hooks.mjs';
import { installStatePath, recordPermissions, readRecordedPermissions, clearRecordedPermissions } from '../lib/install-state.mjs';
import { appendMarkedBlock, removeMarkedBlock } from '../lib/toml.mjs';
import { ensureEnvVar } from '../lib/env-file.mjs';

export const MARKER = 'Memories Claude rules';
const exists = (p) => access(p).then(() => true, () => false);

const paths = (ctx) => ({
  hooksSrc: join(ctx.assetsDir, 'claude-code/hooks'),
  hooksDest: join(ctx.home, '.claude/hooks/memory'),
  settings: join(ctx.home, '.claude/settings.json'),
  skillsSrc: join(ctx.assetsDir, 'claude-code/skills'),
  skillMemories: join(ctx.home, '.claude/skills/memories'),
  skillSetup: join(ctx.home, '.claude/skills/memories-setup'),
  claudeMd: join(ctx.home, '.claude/CLAUDE.md'),
  rulesSrc: join(ctx.assetsDir, 'claude-code/CLAUDE.md'),
  envFile: join(ctx.home, '.config/memories/env'),
});

export async function install(ctx) {
  const p = paths(ctx);
  await copyHookScripts(p.hooksSrc, p.hooksDest);
  const hooksConfig = JSON.parse(await readFile(join(p.hooksSrc, 'hooks.json'), 'utf8'));
  let settings = await readJson(p.settings);
  settings = mergeHookSettings(settings, renderHooksJson(hooksConfig, p.hooksDest));
  // Default 'memories' plus any --mcp-name overrides, deduped by
  // readonlyMcpTools() per name and again by addPermissions' Set.
  const mcpNames = ctx.mcpNames?.length ? ctx.mcpNames : ['memories'];
  const desiredRules = mcpNames.flatMap(readonlyMcpTools);
  // Record only rules we actually introduce: a rule the user already had is
  // theirs, and uninstall must leave it behind.
  const alreadyPresent = new Set(settings.permissions?.allow ?? []);
  const addedRules = desiredRules.filter((rule) => !alreadyPresent.has(rule));
  settings = addPermissions(settings, desiredRules);
  const { settings: withMcp, skipped } = registerMcp(settings, { url: ctx.url, apiKey: ctx.apiKey });
  await writeJson(p.settings, withMcp);
  if (skipped) ctx.log('MCP entry "memories" already present — left untouched');

  await mkdir(p.skillMemories, { recursive: true });
  await cp(join(p.skillsSrc, 'memories/SKILL.md'), join(p.skillMemories, 'SKILL.md'), { dereference: true });
  await mkdir(p.skillSetup, { recursive: true });
  await cp(join(p.skillsSrc, 'setup/SKILL.md'), join(p.skillSetup, 'SKILL.md'), { dereference: true });

  const rules = await readFile(p.rulesSrc, 'utf8');
  const existing = (await exists(p.claudeMd)) ? await readFile(p.claudeMd, 'utf8') : '';
  await mkdir(join(ctx.home, '.claude'), { recursive: true });
  await writeFile(p.claudeMd, appendMarkedBlock(existing, MARKER, rules.trimEnd()));

  await ensureEnvVar(p.envFile, 'MEMORIES_URL', ctx.url);
  if (ctx.apiKey) await ensureEnvVar(p.envFile, 'MEMORIES_API_KEY', ctx.apiKey);
  await recordPermissions(installStatePath(ctx.home), 'claude-code', addedRules);
  ctx.log(`Claude Code wired (hooks: ${p.hooksDest})`);
}

export async function uninstall(ctx) {
  const p = paths(ctx);
  // Captured before the removals below erase the evidence.
  const wasInstalled = (await exists(p.hooksDest)) || (await exists(p.skillMemories));
  const recordedRules = await readRecordedPermissions(installStatePath(ctx.home), 'claude-code');
  await rm(p.hooksDest, { recursive: true, force: true });
  if (await exists(p.settings)) {
    let settings = await readJson(p.settings);
    const m = settings.mcpServers?.memories;
    const ours = m && (
      (m.command === 'npx' && (m.args ?? []).includes('memories-mcp'))
      || (m.args ?? []).some((a) => String(a).includes('mcp-server/index.js'))
    );
    if (ours) {
      delete settings.mcpServers.memories;
      if (Object.keys(settings.mcpServers).length === 0) delete settings.mcpServers;
    }
    if (settings.hooks) {
      for (const [event, entries] of Object.entries(settings.hooks)) {
        const kept = entries
          .map((e) => ({ ...e, hooks: (e.hooks ?? []).filter((h) => !String(h.command ?? '').includes('/hooks/memory/memory-')) }))
          .filter((e) => e.hooks.length > 0);
        if (kept.length) settings.hooks[event] = kept; else delete settings.hooks[event];
      }
      if (Object.keys(settings.hooks).length === 0) delete settings.hooks;
    }
    // Remove only rules this install recorded as its own. Installs predating
    // the manifest have no record, so fall back to the default-name rule set
    // — the only names any earlier version ever wrote — and only when there
    // is evidence we installed here at all. A machine we never touched, or a
    // third party's `mcp__<other>__memory_search`, is left alone.
    const owned = new Set(recordedRules ?? (wasInstalled ? readonlyMcpTools() : []));
    settings = removePermissions(settings, (rule) => owned.has(rule));
    await writeJson(p.settings, settings);
  }
  await rm(p.skillMemories, { recursive: true, force: true });
  await rm(p.skillSetup, { recursive: true, force: true });
  if (await exists(p.claudeMd)) await writeFile(p.claudeMd, removeMarkedBlock(await readFile(p.claudeMd, 'utf8'), MARKER));
  // Last: a throw anywhere above must leave the record intact so a retry can
  // still identify what we own.
  await clearRecordedPermissions(installStatePath(ctx.home), 'claude-code');
  ctx.log('Claude Code integration removed');
}

export async function status(ctx) {
  const p = paths(ctx);
  const hooks = await exists(join(p.hooksDest, 'memory-recall.sh'));
  const settings = await readJson(p.settings);
  const mcp = Boolean(settings.mcpServers?.memories);
  return { installed: hooks && mcp, details: [`hooks: ${hooks}`, `mcp: ${mcp}`] };
}
