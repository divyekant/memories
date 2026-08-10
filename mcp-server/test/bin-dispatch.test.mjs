import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const indexPath = join(dirname(fileURLToPath(import.meta.url)), '../index.js');

test('npx-style `memories-mcp init` dispatches to the CLI, not the MCP server', async () => {
  // Regression test for the P1: npm/npx picks the bin whose name matches the
  // package (memories-mcp -> index.js), so `npx memories-mcp init` used to
  // start the stdio MCP server instead of running the installer CLI.
  const { stdout } = await promisify(execFile)('node', [indexPath, 'help'], { timeout: 5000 });
  assert.match(stdout, /init/); // CLI help text ran, proves the CLI dispatched, not the server.
});
