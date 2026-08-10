export function snippet({ url, apiKey = '' }) {
  return JSON.stringify({
    mcpServers: {
      memories: {
        command: 'npx',
        args: ['-y', 'memories-mcp'],
        env: { MEMORIES_URL: url, MEMORIES_API_KEY: apiKey },
      },
    },
  }, null, 2);
}

export async function install(ctx) {
  ctx.log('Add this to your MCP client config:');
  ctx.log(snippet({ url: ctx.url, apiKey: ctx.apiKey }));
}

export async function uninstall(ctx) {
  ctx.log('Remove the "memories" entry from your MCP client config.');
}

export async function status() {
  return { installed: false, details: ['generic target has no local state'] };
}
