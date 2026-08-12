import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import {
  buildServer,
  parseProjectDeclaration,
  loadProjectDeclaration,
  resolveProjectContext,
} from '../lib-tools.mjs';

test('project declaration parser accepts only the strict shared-memory contract', () => {
  const fixtures = [
    {
      name: 'valid',
      source: 'project_id: shared-demo\nshared_memory: true\n',
      expected: { ok: true, projectId: 'shared-demo', sharedMemory: true },
    },
    {
      name: 'missing shared_memory',
      source: 'project_id: shared-demo\n',
      expectedReason: 'missing_field',
    },
    {
      name: 'malformed yaml',
      source: 'project_id: [shared-demo\nshared_memory: true\n',
      expectedReason: 'malformed',
    },
    {
      name: 'unknown field',
      source: 'project_id: shared-demo\nshared_memory: true\npromotion: true\n',
      expectedReason: 'unknown_field',
    },
    {
      name: 'false opt in',
      source: 'project_id: shared-demo\nshared_memory: false\n',
      expectedReason: 'shared_memory_not_true',
    },
    {
      name: 'invalid slug',
      source: 'project_id: Shared Demo\nshared_memory: true\n',
      expectedReason: 'invalid_project_id',
    },
    {
      name: 'hash without YAML comment separation',
      source: 'project_id: shared-demo#suffix\nshared_memory: true\n',
      expectedReason: 'invalid_project_id',
    },
  ];

  for (const fixture of fixtures) {
    const parsed = parseProjectDeclaration(fixture.source);
    if (fixture.expected) {
      assert.deepEqual(parsed, fixture.expected, fixture.name);
    } else {
      assert.equal(parsed.ok, false, fixture.name);
      assert.equal(parsed.reason, fixture.expectedReason, fixture.name);
    }
  }
});

test('project declaration hash comment semantics match both packaged hooks', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-comments-'));
  const declaration = join(dir, 'project.yaml');
  const libs = [
    join(process.cwd(), 'assets', 'claude-code', 'hooks', '_lib.sh'),
    join(process.cwd(), 'assets', 'codex', 'hooks', '_lib.sh'),
  ];
  for (const source of [
    'project_id: shared-demo#suffix\nshared_memory: true\n',
    'project_id: shared-demo # suffix\nshared_memory: true\n',
  ]) {
    await writeFile(declaration, source);
    const nodeParsed = parseProjectDeclaration(source);
    for (const lib of libs) {
      const shellParsed = JSON.parse(execFileSync('bash', ['-c',
        'source "$1" 2>/dev/null; _memories_parse_project_yaml "$2"',
        '_', lib, declaration,
      ], { encoding: 'utf8' }));
      assert.equal(shellParsed.ok, nodeParsed.ok, source);
      assert.equal(shellParsed.reason, nodeParsed.reason, source);
      if (nodeParsed.ok) {
        assert.equal(shellParsed.project_id, nodeParsed.projectId, source);
      }
    }
  }
});

test('project declaration resolves from the main repository boundary for worktrees', async () => {
  const repo = await mkdtemp(join(tmpdir(), 'mem-project-repo-'));
  await mkdir(join(repo, '.memories'), { recursive: true });
  await writeFile(join(repo, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  await writeFile(join(repo, 'README.md'), 'fixture\n');
  const git = (args) => execFileSync('git', args, {
    cwd: repo,
    env: { ...process.env, GIT_AUTHOR_NAME: 'test', GIT_AUTHOR_EMAIL: 'test@example.com', GIT_COMMITTER_NAME: 'test', GIT_COMMITTER_EMAIL: 'test@example.com' },
    stdio: 'ignore',
  });
  git(['init', '-q']);
  git(['add', '.']);
  git(['-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'init']);
  const worktree = join(repo, '.claude', 'worktrees', 'temporary-name');
  await mkdir(join(repo, '.claude', 'worktrees'), { recursive: true });
  git(['worktree', 'add', '-q', '-b', 'fixture-worktree', worktree]);

  const declaration = await loadProjectDeclaration({ cwd: worktree });
  assert.deepEqual(declaration, { ok: true, projectId: 'shared-demo', sharedMemory: true });
});

test('project context resolves a managed principal only after strict config and one backend', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-context-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const seen = [];
  const context = await resolveProjectContext({
    cwd: dir,
    backends: [{ name: 'shared', url: 'http://backend.test', apiKey: 'secret' }],
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice' }), { status: 200 });
    },
  });

  assert.equal(context.active, true);
  assert.equal(context.projectId, 'shared-demo');
  assert.equal(context.principalId, 'alice');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, 'http://backend.test/api/keys/me');
  assert.equal(seen[0].options.headers['X-API-Key'], 'secret');
});

test('project context resolves backend config from the main repository boundary in a worktree', async () => {
  const repo = await mkdtemp(join(tmpdir(), 'mem-project-backend-repo-'));
  await mkdir(join(repo, '.memories'), { recursive: true });
  await writeFile(join(repo, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  await writeFile(join(repo, '.memories', 'backends.yaml'), 'backends:\n  shared:\n    url: http://backend.test\n    api_key: secret\n');
  await writeFile(join(repo, 'README.md'), 'fixture\n');
  const git = (args) => execFileSync('git', args, {
    cwd: repo,
    env: { ...process.env, GIT_AUTHOR_NAME: 'test', GIT_AUTHOR_EMAIL: 'test@example.com', GIT_COMMITTER_NAME: 'test', GIT_COMMITTER_EMAIL: 'test@example.com' },
    stdio: 'ignore',
  });
  git(['init', '-q']);
  git(['add', '.']);
  git(['-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'init']);
  const worktree = join(repo, '.claude', 'worktrees', 'temporary-name');
  await mkdir(join(repo, '.claude', 'worktrees'), { recursive: true });
  git(['worktree', 'add', '-q', '-b', 'fixture-worktree', worktree]);

  const context = await resolveProjectContext({
    cwd: worktree,
    fetchImpl: async (url, options) => {
      assert.equal(url, 'http://backend.test/api/keys/me');
      assert.equal(options.headers['X-API-Key'], 'secret');
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice' }), { status: 200 });
    },
  });
  assert.equal(context.active, true);
  assert.equal(context.projectId, 'shared-demo');
  assert.equal(context.principalId, 'alice');
});

test('project context disables itself before principal lookup for multiple backends', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-multi-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  let calls = 0;
  const context = await resolveProjectContext({
    cwd: dir,
    backends: [
      { name: 'one', url: 'http://one.test', apiKey: 'one' },
      { name: 'two', url: 'http://two.test', apiKey: 'two' },
    ],
    fetchImpl: async () => {
      calls += 1;
      throw new Error('must not probe an ambiguous backend set');
    },
  });

  assert.equal(context.active, false);
  assert.equal(context.reason, 'multiple_backends');
  assert.equal(calls, 0);
  assert.deepEqual(Object.keys(context).sort(), ['active', 'diagnostic', 'reason']);
});

test('project context treats env, missing, invalid, and unreachable principals as inactive', async () => {
  const fixtures = [
    { body: { type: 'env' }, reason: 'env_principal' },
    { body: { type: 'managed' }, reason: 'missing_principal' },
    { body: { type: 'managed', principal_id: 'Not A Slug' }, reason: 'invalid_principal' },
    { body: {}, reason: 'invalid_principal_type' },
    { body: { type: 'unknown', principal_id: 'alice' }, reason: 'invalid_principal_type' },
  ];
  for (const fixture of fixtures) {
    const dir = await mkdtemp(join(tmpdir(), 'mem-project-principal-'));
    await mkdir(join(dir, '.memories'), { recursive: true });
    await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
    const context = await resolveProjectContext({
      cwd: dir,
      backends: [{ name: 'shared', url: 'http://backend.test', apiKey: 'secret' }],
      fetchImpl: async () => new Response(JSON.stringify(fixture.body), { status: 200 }),
    });
    assert.equal(context.active, false, fixture.reason);
    assert.equal(context.reason, fixture.reason, fixture.reason);
  }

  const dir = await mkdtemp(join(tmpdir(), 'mem-project-unreachable-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const unreachable = await resolveProjectContext({
    cwd: dir,
    backends: [{ name: 'shared', url: 'http://backend.test', apiKey: 'secret' }],
    fetchImpl: async () => { throw new Error('offline'); },
  });
  assert.equal(unreachable.active, false);
  assert.equal(unreachable.reason, 'principal_unreachable');
});

test('project context fails closed for missing, malformed, and non-object backend config', async () => {
  const fixtures = [
    { name: 'missing', source: null, reason: 'no_backends' },
    { name: 'malformed', source: 'backends: [\n', reason: 'backend_config_invalid' },
    { name: 'non-object', source: 'true\n', reason: 'backend_config_invalid' },
    {
      name: 'sequence-after-backend',
      source: 'backends:\n  shared:\n    url: http://backend.test\n  - invalid\n',
      reason: 'backend_config_invalid',
    },
  ];
  const previousConfigFile = process.env.MEMORIES_BACKENDS_FILE;
  try {
    for (const fixture of fixtures) {
      const dir = await mkdtemp(join(tmpdir(), `mem-project-backend-${fixture.name}-`));
      await mkdir(join(dir, '.memories'), { recursive: true });
      await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
      const configPath = join(dir, '.memories', 'backends.yaml');
      if (fixture.source !== null) await writeFile(configPath, fixture.source);
      process.env.MEMORIES_BACKENDS_FILE = fixture.source === null ? configPath : configPath;
      let calls = 0;
      const context = await resolveProjectContext({
        cwd: dir,
        fetchImpl: async () => {
          calls += 1;
          throw new Error('backend lookup must not run');
        },
      });
      assert.equal(context.active, false, fixture.name);
      assert.equal(context.reason, fixture.reason, fixture.name);
      assert.equal(calls, 0, fixture.name);
    }
  } finally {
    if (previousConfigFile === undefined) delete process.env.MEMORIES_BACKENDS_FILE;
    else process.env.MEMORIES_BACKENDS_FILE = previousConfigFile;
  }
});

test('buildServer threads ctx.client into the X-Memories-Client header via fetchImpl', async () => {
  let seenHeaders;
  const fetchImpl = async (_url, opts) => {
    seenHeaders = opts.headers;
    return new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 });
  };

  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x', fetchImpl });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });

  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    await client.callTool({ name: 'memory_search', arguments: { query: 'hi' } });
    assert.equal(seenHeaders['X-Memories-Client'], 'x');
  } finally {
    await client.close();
    await server.close();
  }
});

test('memory_add documents and enforces one explicit project kind without a preflight write', async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), body: JSON.parse(options.body) });
    return new Response(JSON.stringify({ id: calls.length, action: 'added' }), { status: 200 });
  };
  const server = buildServer({ url: 'http://x', apiKey: 'secret', client: 'codex', fetchImpl });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });

  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const tools = await client.listTools();
    const add = tools.tools.find((tool) => tool.name === 'memory_add');
    assert.match(add.description, /exactly once/i);
    assert.match(add.description, /decisions.*knowledge.*state.*operations/i);

    const added = await client.callTool({
      name: 'memory_add',
      arguments: { text: 'Shared decision', source: 'project/shared-demo/decisions' },
    });
    assert.equal(added.isError, undefined);
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].body, {
      text: 'Shared decision',
      source: 'project/shared-demo/decisions',
      on_duplicate: 'supersede',
    });

    const invalid = await client.callTool({
      name: 'memory_add',
      arguments: { text: 'Bad namespace', source: 'project/shared-demo/other' },
    });
    assert.equal(invalid.isError, true);
    assert.equal(calls.length, 1, 'invalid project source must not call the backend');
  } finally {
    await client.close();
    await server.close();
  }
});

test('memory_extract guidance keeps automatic extraction private to the contributor namespace', async () => {
  const server = buildServer({ url: 'http://x', apiKey: '', client: 'manual', fetchImpl: async () => {
    throw new Error('not expected');
  } });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const tools = await client.listTools();
    const extract = tools.tools.find((tool) => tool.name === 'memory_extract');
    assert.match(extract.description, /automatic extraction remains private/i);
    assert.match(extract.description, /person\/<principal>\/<project>\/knowledge/i);
    assert.match(extract.description, /memory_add exactly once/i);
  } finally {
    await client.close();
    await server.close();
  }
});

// ---------------------------------------------------------------------------
// skipFileConfig — ctx wins over .memories/backends.yaml (item 3)
// ---------------------------------------------------------------------------

test('buildServer skipFileConfig: ctx url/apiKey win over a .memories/backends.yaml in cwd', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-libtools-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(
    join(dir, '.memories', 'backends.yaml'),
    'backends:\n  bogus:\n    url: http://bogus-backend.invalid\n    api_key: nope\n'
  );

  const originalCwd = process.cwd();
  let seenUrl;
  const fetchImpl = async (url) => {
    seenUrl = String(url);
    return new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 });
  };

  process.chdir(dir);
  try {
    const server = buildServer({ url: 'http://ctx-wins.invalid', apiKey: '', client: 'x', skipFileConfig: true, fetchImpl });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const client = new Client({ name: 'test-client', version: '1.0.0' });
    await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
    try {
      await client.callTool({ name: 'memory_search', arguments: { query: 'hi' } });
    } finally {
      await client.close();
      await server.close();
    }
  } finally {
    process.chdir(originalCwd);
  }

  assert.match(seenUrl, /^http:\/\/ctx-wins\.invalid/, `expected request to go to ctx-wins host, got ${seenUrl}`);
});

// ---------------------------------------------------------------------------
// version — read from package.json instead of a hardcoded string (item 6)
// ---------------------------------------------------------------------------

test('buildServer default version equals package.json version (via MCP initialize serverInfo)', async () => {
  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
    assert.equal(client.getServerVersion().version, pkg.version);
  } finally {
    await client.close();
    await server.close();
  }
});

test('buildServer ctx.version overrides the package.json default', async () => {
  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x', version: '9.9.9-test' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    assert.equal(client.getServerVersion().version, '9.9.9-test');
  } finally {
    await client.close();
    await server.close();
  }
});
