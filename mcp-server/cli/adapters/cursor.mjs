import { join } from 'node:path';
import * as claudeCode from './claude-code.mjs';
import { readJson, writeJson, registerMcp } from '../lib/json-file.mjs';

const mcpPath = (ctx) => join(ctx.home, '.cursor/mcp.json');

export async function install(ctx) {
  await claudeCode.install(ctx); // Cursor reads ~/.claude/settings.json via Third-party skills
  const { settings, skipped } = registerMcp(await readJson(mcpPath(ctx)), { url: ctx.url, apiKey: ctx.apiKey });
  await writeJson(mcpPath(ctx), settings);
  if (skipped) ctx.log('Cursor MCP entry already present — left untouched');
  ctx.log('[ACTION REQUIRED] Enable Third-party skills in Cursor: Settings → Features → Third-party skills → ON, then restart Cursor.');
}

export async function uninstall(ctx) {
  const settings = await readJson(mcpPath(ctx));
  const m = settings.mcpServers?.memories;
  const ours = m && (
    (m.command === 'npx' && (m.args ?? []).includes('memories-mcp'))
    || (m.args ?? []).some((a) => String(a).includes('mcp-server/index.js'))
  );
  if (ours) {
    delete settings.mcpServers.memories;
    if (Object.keys(settings.mcpServers).length === 0) delete settings.mcpServers;
    await writeJson(mcpPath(ctx), settings);
  }
  ctx.log('Cursor MCP entry removed (shared ~/.claude pieces are managed by the claude-code target)');
}

export async function status(ctx) {
  const installed = Boolean((await readJson(mcpPath(ctx))).mcpServers?.memories);
  return { installed, details: [`~/.cursor/mcp.json: ${installed}`] };
}
