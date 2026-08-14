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
  MEMORIES_MCP_INSTRUCTIONS,
  parseProjectDeclaration,
  loadProjectDeclaration,
  resolveProjectContext,
  deriveLegacyProjectPrefixes,
} from '../lib-tools.mjs';

test('legacy project prefixes retain authorized kind-level descendants', () => {
  assert.deepEqual(
    deriveLegacyProjectPrefixes('shared-demo', [
      'codex/shared-demo/knowledge',
      'claude-code/shared-demo/state',
      'codex/other/knowledge',
      'codex/other/shared-demo',
      'project/shared-demo/knowledge',
      'person/alice/shared-demo/knowledge',
    ]),
    ['codex/shared-demo/knowledge', 'claude-code/shared-demo/state'],
  );
  assert.deepEqual(
    deriveLegacyProjectPrefixes('shared-demo', [
      'codex/shared-demo/knowledge',
      'codex/shared-demo',
    ]),
    ['codex/shared-demo'],
  );
});

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

test('project context retains managed prefixes and narrows legacy continuity to the declared project', async () => {
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
    const result = body.source_prefixes
      ? body.source_prefixes.flatMap((prefix) => responses[prefix] || [])
      : (responses[body.source_prefix] || []);
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
    const searchCalls = calls.filter((call) => call.url.endsWith('/search'));
    assert.equal(searchCalls.length, 1);
    assert.deepEqual(searchCalls[0].body.source_prefixes, [
      'project/shared-demo',
      'person/alice/shared-demo',
      'codex/shared-demo',
      'claude-code/shared-demo',
    ]);
    assert.equal(Object.hasOwn(searchCalls[0].body, 'source_prefix'), false);
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
    assert.equal(recallCalls.every((call) => (
      call.body.source_prefixes.join('|') === searchCalls[0].body.source_prefixes.join('|')
    )), true);
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

test('active memory_search intersects project scopes with kind-level ACLs before limiting', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-search-acl-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const searchBodies = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: ['project/shared-demo/decisions'],
      }), { status: 200 });
    }
    const body = JSON.parse(options.body);
    searchBodies.push(body);
    const results = body.source_prefixes?.length === 1
      && body.source_prefixes[0] === 'project/shared-demo/decisions'
      ? [{ id: 1, source: 'project/shared-demo/decisions', text: 'Allowed decision', similarity: 0.8 }]
      : [{ id: 2, source: 'project/shared-demo/knowledge', text: 'Crowding result', similarity: 0.99 }];
    return new Response(JSON.stringify({ results, count: results.length }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_search', arguments: { query: 'decision', k: 1 } });
    assert.equal(searchBodies.length, 1);
    assert.deepEqual(searchBodies[0].source_prefixes, ['project/shared-demo/decisions']);
    assert.equal(Object.hasOwn(searchBodies[0], 'source_prefix'), false);
    const rendered = result.content.map((item) => item.text || '').join('\n');
    assert.match(rendered, /Allowed decision/);
    assert.doesNotMatch(rendered, /Crowding result/);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_search retains a kind-level legacy ACL', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-search-legacy-kind-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const searchBodies = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: ['codex/shared-demo/knowledge'],
      }), { status: 200 });
    }
    const body = JSON.parse(options.body);
    searchBodies.push(body);
    return new Response(JSON.stringify({
      results: [{ id: 1, source: 'codex/shared-demo/knowledge', text: 'Legacy knowledge', similarity: 0.8 }],
      count: 1,
    }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_search', arguments: { query: 'knowledge', k: 1 } });
    assert.equal(searchBodies.length, 1);
    assert.deepEqual(searchBodies[0].source_prefixes, ['codex/shared-demo/knowledge']);
    assert.match(result.content.map((item) => item.text || '').join('\n'), /Legacy knowledge/);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_search ranks authorized scopes globally before applying k', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-search-rank-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const searchBodies = [];
  const fetchImpl = async (url, options = {}) => {
    const requestUrl = String(url);
    if (requestUrl.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: ['project/shared-demo/', 'person/alice/shared-demo/'],
      }), { status: 200 });
    }
    const body = JSON.parse(options.body);
    searchBodies.push(body);
    const results = body.source_prefixes
      ? [
        { id: 2, source: 'person/alice/shared-demo/knowledge', text: 'Exact private match', rrf_score: 0.0164 },
        { id: 1, source: 'project/shared-demo/knowledge', text: 'Weak shared match', rrf_score: 0.0112 },
      ]
      : [{ id: 1, source: 'project/shared-demo/knowledge', text: 'Weak shared match', rrf_score: 0.0164 }];
    return new Response(JSON.stringify({ results, count: results.length }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_search', arguments: { query: 'exact', k: 1 } });
    assert.equal(searchBodies.length, 1);
    assert.deepEqual(searchBodies[0].source_prefixes, [
      'project/shared-demo',
      'person/alice/shared-demo',
    ]);
    const rendered = result.content.map((item) => item.text || '').join('\n');
    assert.match(rendered, /Exact private match/);
    assert.doesNotMatch(rendered, /Weak shared match/);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_list without a source browses only project, current-person, and authorized legacy scopes', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-list-scoped-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const browseSources = [];
  const fetchImpl = async (url) => {
    const requestUrl = new URL(String(url));
    if (requestUrl.pathname.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: [
          'project/shared-demo/',
          'person/alice/shared-demo/',
          'project/other/',
          'codex/shared-demo',
        ],
      }), { status: 200 });
    }
    const source = requestUrl.searchParams.get('source');
    browseSources.push(source);
    const bySource = {
      'project/shared-demo': [
        { id: 1, source: 'project/shared-demo/knowledge', text: 'Shared fact' },
        { id: 9, source: 'project/shared-demo-extra/knowledge', text: 'Sibling project fact' },
      ],
      'person/alice/shared-demo': [
        { id: 2, source: 'person/alice/shared-demo/knowledge', text: 'Private fact' },
      ],
      'codex/shared-demo': [
        { id: 3, source: 'codex/shared-demo', text: 'Legacy fact' },
      ],
    };
    const scopedMemories = bySource[source] || [];
    const memories = requestUrl.searchParams.get('source_boundary') === 'true'
      ? scopedMemories.filter((memory) => memory.source === source || memory.source.startsWith(`${source}/`))
      : scopedMemories;
    if (requestUrl.pathname.endsWith('/memories/count')) {
      return new Response(JSON.stringify({ count: memories.length }), { status: 200 });
    }
    return new Response(JSON.stringify({ memories, total: memories.length, offset: 0, limit: 50 }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_list', arguments: { limit: 20 } });
    const rendered = result.content.map((item) => item.text || '').join('\n');
    assert.deepEqual(browseSources, [
      'project/shared-demo',
      'person/alice/shared-demo',
      'codex/shared-demo',
      'project/shared-demo',
      'person/alice/shared-demo',
      'codex/shared-demo',
    ]);
    assert.match(rendered, /Shared fact/);
    assert.match(rendered, /Private fact/);
    assert.match(rendered, /Legacy fact/);
    assert.doesNotMatch(rendered, /Sibling project fact/);
    assert.doesNotMatch(rendered, /project\/other/);

    browseSources.length = 0;
    const countResult = await client.callTool({ name: 'memory_count', arguments: {} });
    assert.deepEqual(browseSources, [
      'project/shared-demo',
      'person/alice/shared-demo',
      'codex/shared-demo',
    ]);
    assert.match(countResult.content[0].text, /^3 memories in project "shared-demo"\.$/);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_list intersects collaborative scopes with the managed key ACL', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-list-acl-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const prefixes = [
    'project/shared-demo/decisions',
    'person/alice/shared-demo/knowledge',
  ];
  const browseSources = [];
  const canRead = (source) => prefixes.some((prefix) => source === prefix || source.startsWith(`${prefix}/`));
  const fetchImpl = async (url) => {
    const requestUrl = new URL(String(url));
    if (requestUrl.pathname.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({ type: 'managed', principal_id: 'alice', prefixes }), { status: 200 });
    }
    const source = requestUrl.searchParams.get('source');
    browseSources.push(source);
    if (!canRead(source)) {
      return new Response(JSON.stringify({ detail: 'forbidden' }), { status: 403 });
    }
    if (requestUrl.pathname.endsWith('/memories/count')) {
      return new Response(JSON.stringify({ count: 1 }), { status: 200 });
    }
    return new Response(JSON.stringify({
      memories: [{ id: browseSources.length, source, text: `Fact from ${source}` }],
      total: 1,
      offset: 0,
      limit: 20,
    }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_list', arguments: { limit: 20 } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(browseSources, [...prefixes, ...prefixes]);
    const rendered = result.content.map((item) => item.text || '').join('\n');
    assert.match(rendered, /project\/shared-demo\/decisions/);
    assert.match(rendered, /person\/alice\/shared-demo\/knowledge/);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_list preserves managed admin access even with an empty prefix list', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-list-admin-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const browseSources = [];
  const fetchImpl = async (url) => {
    const requestUrl = new URL(String(url));
    if (requestUrl.pathname.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        role: 'admin',
        principal_id: 'alice',
        prefixes: [],
      }), { status: 200 });
    }
    const source = requestUrl.searchParams.get('source');
    browseSources.push(source);
    if (requestUrl.pathname.endsWith('/memories/count')) {
      return new Response(JSON.stringify({ count: 1 }), { status: 200 });
    }
    return new Response(JSON.stringify({
      memories: [{ id: browseSources.length, source: `${source}/knowledge`, text: `Fact from ${source}` }],
      total: 1,
      offset: 0,
      limit: 20,
    }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_list', arguments: { limit: 20 } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(browseSources, [
      'project/shared-demo',
      'person/alice/shared-demo',
      'project/shared-demo',
      'person/alice/shared-demo',
    ]);
  } finally {
    await client.close();
    await server.close();
  }
});

test('active memory_list and memory_count use server pagination beyond five thousand records', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-list-page-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  const calls = [];
  const total = 6001;
  const fetchImpl = async (url) => {
    const requestUrl = new URL(String(url));
    if (requestUrl.pathname.endsWith('/api/keys/me')) {
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: ['project/shared-demo/'],
      }), { status: 200 });
    }
    calls.push({
      path: requestUrl.pathname,
      source: requestUrl.searchParams.get('source'),
      offset: requestUrl.searchParams.get('offset'),
      limit: requestUrl.searchParams.get('limit'),
      sourceBoundary: requestUrl.searchParams.get('source_boundary'),
    });
    if (requestUrl.pathname.endsWith('/memories/count')) {
      return new Response(JSON.stringify({ count: total }), { status: 200 });
    }
    const offset = Number(requestUrl.searchParams.get('offset'));
    const limit = Number(requestUrl.searchParams.get('limit'));
    const memories = Array.from({ length: Math.min(limit, total - offset) }, (_, index) => ({
      id: offset + index,
      source: 'project/shared-demo/knowledge',
      text: `Fact ${offset + index}`,
    }));
    return new Response(JSON.stringify({ memories, total, offset, limit }), { status: 200 });
  };
  const server = buildServer({ cwd: dir, url: 'http://backend.test', apiKey: 'secret', fetchImpl, skipFileConfig: true });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_list', arguments: { offset: 5999, limit: 2 } });
    assert.equal(result.isError, undefined);
    assert.match(result.content[0].text, /Memories \(6000-6001 of 6001\)/);
    assert.match(result.content[0].text, /Fact 5999/);
    assert.match(result.content[0].text, /Fact 6000/);

    calls.length = 0;
    const countResult = await client.callTool({ name: 'memory_count', arguments: {} });
    assert.match(countResult.content[0].text, /^6001 memories in project "shared-demo"\.$/);
    assert.deepEqual(calls, [{
      path: '/memories/count',
      source: 'project/shared-demo',
      offset: null,
      limit: null,
      sourceBoundary: 'true',
    }]);
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

test('invalid project declaration fails closed instead of falling back to unscoped search', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-invalid-search-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: Shared Demo\nshared_memory: true\n');
  const calls = [];
  const server = buildServer({
    cwd: dir,
    url: 'http://backend.test',
    apiKey: 'secret',
    skipFileConfig: true,
    fetchImpl: async (url) => {
      calls.push(String(url));
      return new Response(JSON.stringify({ results: [], count: 0 }), { status: 200 });
    },
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({ name: 'memory_search', arguments: { query: 'must stay scoped' } });
    assert.equal(result.isError, true);
    assert.match(result.content[0].text, /project declaration|project_id/i);
    assert.deepEqual(calls, []);
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
        prefixes: [
          'project/shared-demo/',
          'person/alice/shared-demo/',
          'codex/shared-demo',
        ],
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
      searchBodies.filter((body) => body.query === 'second').map((body) => body.source_prefixes),
      [['project/shared-demo', 'person/alice/shared-demo', 'codex/shared-demo']],
    );
    assert.deepEqual(
      searchBodies.filter((body) => body.query === 'third').map((body) => body.source_prefixes),
      [['project/shared-demo', 'person/alice/shared-demo', 'codex/shared-demo']],
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
      return new Response(JSON.stringify({
        type: 'managed',
        principal_id: 'alice',
        prefixes: ['project/shared-demo/'],
      }), { status: 200 });
    }
    const body = options.body ? JSON.parse(options.body) : {};
    const results = memories.filter((memory) => (body.source_prefixes || [body.source_prefix]).some((prefix) => (
      memory.source === prefix || memory.source.startsWith(`${prefix}/`)
    )));
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

test('memory_missed uses the same single-backend project write gate as memory_add', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-missed-gate-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  await writeFile(
    join(dir, '.memories', 'backends.yaml'),
    'backends:\n  one:\n    url: http://one.test\n  two:\n    url: http://two.test\n',
  );
  const calls = [];
  const server = buildServer({
    cwd: dir,
    fetchImpl: async (url) => {
      calls.push(String(url));
      return new Response(JSON.stringify({ id: 1, source: 'project/shared-demo/knowledge' }), { status: 200 });
    },
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({
      name: 'memory_missed',
      arguments: { text: 'Shared fact', source: 'project/shared-demo/knowledge' },
    });
    assert.equal(result.isError, true);
    assert.match(result.content[0].text, /exactly one configured backend/i);
    assert.deepEqual(calls, []);
  } finally {
    await client.close();
    await server.close();
  }
});

test('declared project memory_update without a source fails closed before reading an ambiguous backend', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'mem-project-update-multi-'));
  await mkdir(join(dir, '.memories'), { recursive: true });
  await writeFile(join(dir, '.memories', 'project.yaml'), 'project_id: shared-demo\nshared_memory: true\n');
  await writeFile(
    join(dir, '.memories', 'backends.yaml'),
    'backends:\n  one:\n    url: http://one.test\n  two:\n    url: http://two.test\n',
  );
  const calls = [];
  const server = buildServer({
    cwd: dir,
    fetchImpl: async (url) => {
      calls.push(String(url));
      return new Response(JSON.stringify({ id: 41, source: 'project/shared-demo/knowledge' }), { status: 200 });
    },
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    const result = await client.callTool({
      name: 'memory_update',
      arguments: { id: 41, text: 'Updated shared fact' },
    });
    assert.equal(result.isError, true);
    assert.match(result.content[0].text, /exactly one configured backend/i);
    assert.deepEqual(calls, []);
  } finally {
    await client.close();
    await server.close();
  }
});

test('every MCP add operation is forced through the project-aware add chokepoint', async () => {
  const source = await readFile(new URL('../lib-tools.mjs', import.meta.url), 'utf8');
  const directAddOps = source.match(/,\s*["']add["']\s*\)/g) || [];
  assert.equal(directAddOps.length, 1, 'new add operations must use memoriesAddRequest');
  const helperStart = source.indexOf('async function memoriesAddRequest');
  const helperEnd = source.indexOf('\n  }', helperStart);
  const directAddIndex = source.search(/,\s*["']add["']\s*\)/);
  assert.ok(helperStart >= 0 && directAddIndex > helperStart && directAddIndex < helperEnd);
  assert.match(source, /memoriesAddRequest\("\/memory\/add"/);
  assert.match(source, /memoriesAddRequest\("\/memory\/missed"/);
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

test('buildServer publishes shared MCP instructions during initialize', async () => {
  assert.match(MEMORIES_MCP_INSTRUCTIONS.slice(0, 512), /exact project-scoped/i);

  const server = buildServer({ url: 'http://x', apiKey: '', client: 'x' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: 'test-client', version: '1.0.0' });
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    assert.match(client.getInstructions() ?? '', /exact project-scoped/i);
  } finally {
    await client.close();
    await server.close();
  }
});
