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

test('project context retains managed prefixes and allows only exact legacy project prefixes', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-prefixes-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const context = await resolveProjectContext({
    cwd: dir,
    backends: [{ name: 'shared', url: 'http://backend.test', apiKey: 'secret' }],
    fetchImpl: async () => new Response(JSON.stringify({
      type: 'managed',
      principal_id: 'alice',
      prefixes: [
        'project/shared-demo',
        'person/alice/shared-demo',
        'codex/shared-demo',
        'claude-code/{project}',
        'learning/shared-demo/*',
        'wip/',
        'other/not-this-project',
        'person/bob/shared-demo',
        'codex/shared-demo/knowledge',
      ],
    }), { status: 200 }),
  });

  assert.equal(context.active, true);
  assert.deepEqual(context.prefixes, [
    'project/shared-demo',
    'person/alice/shared-demo',
    'codex/shared-demo',
    'claude-code/{project}',
    'learning/shared-demo/*',
    'wip/',
    'other/not-this-project',
    'person/bob/shared-demo',
    'codex/shared-demo/knowledge',
  ]);
  assert.deepEqual(context.legacySourcePrefixes, ['codex/shared-demo', 'claude-code/shared-demo']);
  assert.deepEqual(context.legacy_source_prefixes, ['codex/shared-demo', 'claude-code/shared-demo']);
});

test('active memory_search routes project and private namespaces before authorized legacy prefixes', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-search-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const calls = [];
  let activeSearches = 0;
  let maxActiveSearches = 0;
  const responses = {
    'project/shared-demo': [
      { id: 1, source: 'project/shared-demo/knowledge', text: 'newer weak fact', author: 'alice', origin_client: 'codex', similarity: 0.2, document_at: '2026-08-12T00:00:00Z' },
      { id: 91, source: 'project/shared-demo-extra/knowledge', text: 'sibling project leak', similarity: 0.99 },
    ],
    'person/alice/shared-demo': [
      { id: 2, source: 'person/alice/shared-demo/knowledge', text: 'older strong fact', similarity: 0.9, document_at: '2026-08-10T00:00:00Z' },
      { id: 92, source: 'person/alice/shared-demo-extra/knowledge', text: 'sibling private leak', similarity: 0.98 },
    ],
    'codex/shared-demo': [
      { id: 3, source: 'codex/shared-demo', text: 'legacy fact', similarity: 0.8 },
      { id: 93, source: 'codex/shared-demo-extra', text: 'sibling legacy leak', similarity: 0.97 },
    ],
    'claude-code/shared-demo': [{ id: 1, source: 'project/shared-demo/knowledge', text: 'shared fact', author: 'alice', origin_client: 'codex', similarity: 0.95 }],
  };
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: requestUrl, body });
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: [
          'project/shared-demo',
          'person/alice/shared-demo',
          'codex/shared-demo',
          'claude-code/{project}',
          'learning/shared-demo/*',
          'wip/',
        ],
      }), { status: 200 });
    }
    activeSearches += 1;
    maxActiveSearches = Math.max(maxActiveSearches, activeSearches);
    await new Promise((resolve) => setTimeout(resolve, 20));
    const result = body.source_prefix === 'project/shared-demo' && body.source_boundary !== true
      ? responses['project/shared-demo'].filter((item) => item.source.includes('shared-demo-extra'))
      : (responses[body.source_prefix] || []);
    activeSearches -= 1;
    return new Response(JSON.stringify({ results: result, count: result.length }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({
      name: 'memory_search',
      arguments: { query: 'shared', k: 3 },
    });
    assert.equal(result.isError, undefined);
    const searchPrefixes = calls.filter((call) => call.url.endsWith('/search')).map((call) => call.body.source_prefix);
    assert.deepEqual(searchPrefixes, [
      'project/shared-demo',
      'person/alice/shared-demo',
      'codex/shared-demo',
      'claude-code/shared-demo',
    ]);
    assert.equal(
      calls.filter((call) => call.url.endsWith('/search')).every((call) => call.body.source_boundary === true),
      true,
    );
    assert.ok(maxActiveSearches > 1, 'project prefix requests must run concurrently');
    assert.equal(searchPrefixes.includes(''), false);
    const text = result.content.map((item) => item.text || '').join('\n');
    assert.match(text, /author=alice/);
    assert.match(text, /origin-client=codex/);
    assert.match(text, /Found 3 memories/);
    assert.equal((text.match(/\bid=/g) || []).length, 3);
    assert.equal(text.includes('sibling project leak'), false);
    assert.equal(text.includes('sibling private leak'), false);
    assert.equal(text.includes('sibling legacy leak'), false);
    assert.equal(text.includes('Confidence:'), false);

    const beforeRecallTools = calls.length;
    await client.callTool({
      name: 'memory_timeline',
      arguments: { query: 'shared timeline', k: 3 },
    });
    const evidenceResult = await client.callTool({
      name: 'memory_evidence',
      arguments: { query: 'shared evidence', k: 3 },
    });
    const recallCalls = calls.slice(beforeRecallTools).filter((call) => call.url.endsWith('/search'));
    assert.ok(recallCalls.length > 0);
    assert.equal(calls.slice(beforeRecallTools).some((call) => call.url.endsWith('/search/evidence')), false);
    assert.equal(recallCalls.some((call) => !searchPrefixes.includes(call.body.source_prefix)), false);
    const evidenceText = evidenceResult.content.map((item) => item.text || '').join('\n');
    assert.match(evidenceText, /Current candidate:\n\[2\].*older strong fact/s);
    assert.match(evidenceText, /\[supporting\] project\/shared-demo\/knowledge 2026-08-12/);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_search preserves an explicit source_prefix without project fan-out', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-search-explicit-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: requestUrl, body });
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice', prefixes: ['codex/shared-demo'] }), { status: 200 });
    }
    return new Response(JSON.stringify({ results: [{ id: 10, source: body.source_prefix, text: 'explicit' }], count: 1 }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({
      name: 'memory_search',
      arguments: { query: 'explicit', k: 5, source_prefix: 'codex/shared-demo' },
    });
    assert.equal(result.isError, undefined);
    assert.deepEqual(calls.filter((call) => call.url.endsWith('/search')).map((call) => call.body.source_prefix), ['codex/shared-demo']);
  } finally {
    await client.close();
    await server.close();
  }
});

test('inactive memory_search keeps one unscoped legacy request and skips principal lookup', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-search-inactive-'));
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: requestUrl, body });
    return new Response(JSON.stringify({ results: [], count: 0 }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_search', arguments: { query: 'legacy', k: 5 } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(calls.map((call) => call.url), ['http://backend.test/search']);
    assert.deepEqual(calls[0].body, { query: 'legacy', k: 5, hybrid: true, feedback_weight: 0.1, graph_weight: 0.1 });
    assert.equal(Object.hasOwn(calls[0].body, 'source_prefix'), false);
  } finally {
    await client.close();
    await server.close();
  }
});

test('project mode does not invent localhost when skipFileConfig has no explicit backend', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-no-backend-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  let principalCalls = 0;
  const context = await resolveProjectContext({
    cwd: dir,
    skipFileConfig: true,
    fetchImpl: async () => {
      principalCalls += 1;
      throw new Error('must not probe localhost');
    },
  });
  assert.equal(context.active, false);
  assert.equal(context.reason, 'no_backends');
  assert.equal(principalCalls, 0);
});

test('active memory_extract substitutes the private project source and rejects project input before fetch', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-extract-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: requestUrl, body });
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice', prefixes: ['person/alice/shared-demo'] }), { status: 200 });
    }
    if (requestUrl.endsWith('/memory/extract')) return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200 });
    return new Response(JSON.stringify({ status: 'completed', result: { extracted_count: 0, stored_count: 0, updated_count: 0, deleted_count: 0, actions: [] } }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const rejected = await client.callTool({ name: 'memory_extract', arguments: { messages: 'shared', source: 'project/shared-demo/knowledge' } });
    assert.equal(rejected.isError, true);
    assert.equal(calls.length, 0);

    const extracted = await client.callTool({ name: 'memory_extract', arguments: { messages: 'private', source: 'codex/shared-demo' } });
    assert.equal(extracted.isError, undefined);
    const extractPost = calls.find((call) => call.url.endsWith('/memory/extract'));
    assert.equal(extractPost.body.source, 'person/alice/shared-demo/knowledge');
    assert.equal(calls.filter((call) => call.url.endsWith('/api/keys/me')).length, 1);
  } finally {
    await client.close();
    await server.close();
  }
});

test('project mode binds principal lookup and requests to the worktree backend config', async () => {
  const repo = await mkdtemp(join(tmpdir(), 'mem-project-bind-repo-'));
  await mkdir(join(repo, '.memories'), { recursive: true });
  await writeFile(join(repo, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  await writeFile(join(repo, '.memories', 'backends.yaml'), 'backends:\n  main:\n    url: http://main-backend.test\n    api_key: main-secret\n');
  await writeFile(join(repo, 'README.md'), 'fixture\n');
  const git = (args) => execFileSync('git', args, {
    cwd: repo,
    env: { ...process.env, GIT_AUTHOR_NAME: 'test', GIT_AUTHOR_EMAIL: 'test@example.com', GIT_COMMITTER_NAME: 'test', GIT_COMMITTER_EMAIL: 'test@example.com' },
    stdio: 'ignore',
  });
  git(['init', '-q']);
  git(['add', '.']);
  git(['-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'init']);
  const worktree = join(repo, '.claude', 'worktrees', 'divergent');
  await mkdir(join(repo, '.claude', 'worktrees'), { recursive: true });
  git(['worktree', 'add', '-q', '-b', 'fixture-divergent', worktree]);
  await mkdir(join(worktree, '.memories'), { recursive: true });
  await writeFile(join(worktree, '.memories', 'backends.yaml'), 'backends:\n  worktree:\n    url: http://worktree-backend.test\n    api_key: worktree-secret\n');

  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: requestUrl, body, headers: options.headers });
    if (requestUrl.endsWith('/api/keys/me')) return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice', prefixes: ['project/shared-demo'] }), { status: 200 });
    return new Response(JSON.stringify({ results: [], count: 0 }), { status: 200 });
  };
  const server = buildServer({ cwd: worktree, fetchImpl });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    await client.callTool({ name: 'memory_search', arguments: { query: 'bound', k: 1 } });
    assert.ok(calls.length > 0);
    assert.ok(calls.every((call) => call.url.startsWith('http://worktree-backend.test/')));
    assert.equal(calls.some((call) => call.url.startsWith('http://main-backend.test/')), false);
    assert.equal(calls[0].headers['X-API-Key'], 'worktree-secret');
  } finally {
    await client.close();
    await server.close();
  }
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

test('project context bounds a stalled authenticated principal lookup', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-principal-timeout-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const started = Date.now();
  const context = await resolveProjectContext({
    cwd: dir,
    backends: [{ name: 'shared', url: 'http://backend.test', apiKey: 'secret' }],
    principalTimeoutMs: 25,
    fetchImpl: async (_url, options = {}) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => resolve(new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
      }), { status: 200 })), 200);
      options.signal?.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new Error('aborted'));
      });
    }),
  });

  assert.equal(context.active, false);
  assert.equal(context.reason, 'principal_unreachable');
  assert.match(context.diagnostic, /timed out/i);
  assert.ok(Date.now() - started < 500);
});

test('project-aware tools retry an inactive principal resolution and cache recovery', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-retry-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  let principalCalls = 0;
  const searchBodies = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    if (requestUrl.endsWith('/api/keys/me')) {
      principalCalls += 1;
      if (principalCalls === 1) return new Response('unavailable', { status: 503 });
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: ['codex/shared-demo'],
      }), { status: 200 });
    }
    const body = options.body ? JSON.parse(options.body) : {};
    searchBodies.push(body);
    return new Response(JSON.stringify({ results: [], count: 0 }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const unavailable = await client.callTool({ name: 'memory_search', arguments: { query: 'first', k: 3 } });
    await client.callTool({ name: 'memory_search', arguments: { query: 'second', k: 3 } });
    await client.callTool({ name: 'memory_search', arguments: { query: 'third', k: 3 } });

    assert.equal(principalCalls, 2);
    assert.equal(unavailable.isError, true);
    assert.match(unavailable.content[0].text, /collaborative project memory is unavailable/i);
    assert.equal(searchBodies.some((body) => body.query === 'first'), false);
    assert.deepEqual(
      searchBodies.filter((body) => body.query === 'second').map((body) => body.source_prefix),
      ['project/shared-demo', 'person/alice/shared-demo', 'codex/shared-demo'],
    );
    assert.deepEqual(
      searchBodies.filter((body) => body.query === 'third').map((body) => body.source_prefix),
      ['project/shared-demo', 'person/alice/shared-demo', 'codex/shared-demo'],
    );
  } finally {
    await client.close();
    await server.close();
  }
});

test('legacy server caches a missing project declaration for its lifetime', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-missing-cache-'));
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    calls.push({ url: requestUrl, body: options.body ? JSON.parse(options.body) : null });
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: [],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ results: [], count: 0 }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    await client.callTool({ name: 'memory_search', arguments: { query: 'first', k: 3 } });
    await mkdir(join(dir, '.memories'), { recursive: true });
    await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
    await client.callTool({ name: 'memory_search', arguments: { query: 'second', k: 3 } });

    assert.equal(calls.filter((call) => call.url.endsWith('/api/keys/me')).length, 0);
    assert.deepEqual(
      calls.filter((call) => call.url.endsWith('/search')).map((call) => call.body),
      [
        { query: 'first', k: 3, hybrid: true, feedback_weight: 0.1, graph_weight: 0.1 },
        { query: 'second', k: 3, hybrid: true, feedback_weight: 0.1, graph_weight: 0.1 },
      ],
    );
  } finally {
    await client.close();
    await server.close();
  }
});

test('project evidence recency ignores metadata-only updated_at timestamps', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-evidence-created-at-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const memories = [
    {
      id: 1,
      source: 'project/shared-demo/knowledge',
      text: 'port is 8900',
      similarity: 0.95,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-08-13T00:00:00Z',
      pinned: true,
    },
    {
      id: 2,
      source: 'project/shared-demo/knowledge',
      text: 'port is now 9000',
      similarity: 0.85,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    },
  ];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice', prefixes: [] }), { status: 200 });
    }
    const body = options.body ? JSON.parse(options.body) : {};
    const results = memories.filter((memory) => (
      memory.source === body.source_prefix || memory.source.startsWith(`${body.source_prefix}/`)
    ));
    return new Response(JSON.stringify({ results, count: results.length }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({
      name: 'memory_evidence',
      arguments: { query: 'current port', k: 5 },
    });
    const rendered = result.content.map((item) => item.text || '').join('\n');
    assert.match(rendered, /Current candidate:\n\[2\] project\/shared-demo\/knowledge 2026-08-01/);
    assert.match(rendered, /Older evidence:\n\[1\] project\/shared-demo\/knowledge 2026-01-01/);
  } finally {
    await client.close();
    await server.close();
  }
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

test('declared project memory_add rejects ambiguous multi-backend routing before any write', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-add-multi-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  await writeFile(
    join(dir, '.memories', 'backends.yaml'),
    'backends:\n  one:\n    url: http://one.test\n  two:\n    url: http://two.test\n',
  );
  const calls = [];
  const server = buildServer({
    cwd: dir,
    fetchImpl: async (url, options = {}) => {
      calls.push({ url: String(url), body: options.body });
      return new Response(JSON.stringify({ id: 1, action: 'added' }), { status: 200 });
    },
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const rejected = await client.callTool({
      name: 'memory_add',
      arguments: { text: 'Shared decision', source: 'project/shared-demo/decisions' },
    });
    assert.equal(rejected.isError, true);
    assert.match(rejected.content[0].text, /collaborative project memory is unavailable/i);
    assert.equal(calls.length, 0);
  } finally {
    await client.close();
    await server.close();
  }
});

test('declared project memory_add binds the write to its authenticated project backend', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-add-bound-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), body: options.body ? JSON.parse(options.body) : null });
    if (String(url).endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice' }), { status: 200 });
    }
    return new Response(JSON.stringify({ id: 1, action: 'added' }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://shared.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const added = await client.callTool({
      name: 'memory_add',
      arguments: { text: 'Shared decision', source: 'project/shared-demo/decisions' },
    });
    assert.equal(added.isError, undefined);
    assert.deepEqual(calls.map((call) => call.url), [
      'http://shared.test/api/keys/me',
      'http://shared.test/memory/add',
    ]);

    const mismatch = await client.callTool({
      name: 'memory_add',
      arguments: { text: 'Wrong project', source: 'project/other/decisions' },
    });
    assert.equal(mismatch.isError, true);
    assert.equal(calls.length, 2);
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

test('memory_extract rejects every project namespace before fetch but keeps projectx legacy behavior', async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), body: options.body ? JSON.parse(options.body) : null });
    if (String(url).endsWith('/memory/extract')) {
      return new Response(JSON.stringify({ job_id: 'extract-1' }), { status: 200 });
    }
    return new Response(JSON.stringify({
      status: 'completed',
      result: { extracted_count: 0, stored_count: 0, updated_count: 0, deleted_count: 0, actions: [] },
    }), { status: 200 });
  };
  const server = buildServer({ url: 'http://x', apiKey: '', client: 'manual', fetchImpl });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    for (const source of ['project/shared-demo/knowledge', 'project/shared-demo/other']) {
      const rejected = await client.callTool({
        name: 'memory_extract',
        arguments: { messages: 'Shared fact', source },
      });
      assert.equal(rejected.isError, true);
      const errorText = (rejected.content || []).map((item) => item.text || '').join('\n');
      assert.match(errorText, /automatic extraction/i);
      assert.match(errorText, /memory_add/i);
    }
    assert.equal(calls.length, 0, 'project extraction must fail before any backend request');

    const legacy = await client.callTool({
      name: 'memory_extract',
      arguments: { messages: 'Legacy fact', source: 'projectx/shared-demo/knowledge' },
    });
    assert.equal(legacy.isError, undefined);
    assert.equal(calls[0].body.source, 'projectx/shared-demo/knowledge');
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
