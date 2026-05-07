"""Tests for the OpenCode Memories plugin runtime helpers."""

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = REPO_ROOT / "integrations" / "opencode" / "plugin" / "memories.js"


def _run_node(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def _plugin_import() -> str:
    return f"""
import {{ pathToFileURL }} from 'node:url';
const imported = await import(pathToFileURL({json.dumps(str(PLUGIN))}).href);
const pluginModule = imported.default || imported;
const server = pluginModule.server || imported.server;
const helpers = server.__test;
"""


def _node_json(script: str) -> object:
    result = _run_node(script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_opencode_default_prefixes_include_cross_client_project_scope() -> None:
    output = _node_json(
        _plugin_import()
        + """
console.log(JSON.stringify(helpers.defaultSourcePrefixes('demo')));
"""
    )

    assert output == [
        "opencode/demo",
        "claude-code/demo",
        "codex/demo",
        "learning/demo",
        "wip/demo",
    ]


def test_default_extract_source_uses_opencode_project_prefix() -> None:
    output = _node_json(
        _plugin_import()
        + """
console.log(JSON.stringify(helpers.defaultExtractSource('demo')));
"""
    )

    assert output == "opencode/demo"


def test_source_prefix_quality_classifies_exact_broad_and_other() -> None:
    output = _node_json(
        _plugin_import()
        + """
const prefixes = [
  'opencode/demo',
  'opencode/demo/session',
  'claude-code/demo',
  'codex/demo',
  'learning/demo',
  'wip/demo',
  '',
  'opencode',
  'opencode/',
  'claude-code',
  'claude-code/',
  'codex',
  'codex/',
  'learning',
  'learning/',
  'wip',
  'wip/',
  'other/demo',
  'opencode/other',
];
console.log(JSON.stringify(prefixes.map((prefix) => [prefix, helpers.sourcePrefixQuality(prefix, 'demo')])));
"""
    )

    assert dict(output) == {
        "opencode/demo": "exact_project",
        "opencode/demo/session": "exact_project",
        "claude-code/demo": "exact_project",
        "codex/demo": "exact_project",
        "learning/demo": "exact_project",
        "wip/demo": "exact_project",
        "": "broad_or_unscoped",
        "opencode": "broad_or_unscoped",
        "opencode/": "broad_or_unscoped",
        "claude-code": "broad_or_unscoped",
        "claude-code/": "broad_or_unscoped",
        "codex": "broad_or_unscoped",
        "codex/": "broad_or_unscoped",
        "learning": "broad_or_unscoped",
        "learning/": "broad_or_unscoped",
        "wip": "broad_or_unscoped",
        "wip/": "broad_or_unscoped",
        "other/demo": "other",
        "opencode/other": "other",
    }


def test_render_recall_context_uses_active_search_language() -> None:
    output = _node_json(
        _plugin_import()
        + """
console.log(JSON.stringify(helpers.renderRecallContext({ project: 'demo', results: [], activeSearchRequired: true })));
"""
    )

    assert "MANDATORY FIRST ACTION" in output
    assert 'memory_search' in output
    assert 'source_prefix="opencode/demo"' in output
    assert 'source_prefix="claude-code/demo"' in output
    assert "memory_get is not a substitute for memory_search" in output


def test_plugin_exports_expected_hooks() -> None:
    output = _node_json(
        _plugin_import()
        + """
const hooks = server({}, { project: 'demo' });
console.log(JSON.stringify(Object.keys(hooks).sort()));
"""
    )

    assert output == [
        "experimental.chat.system.transform",
        "tool.execute.after",
    ]


def test_plugin_top_level_exports_only_server() -> None:
    output = _node_json(
        _plugin_import()
        + """
console.log(JSON.stringify(Object.keys(pluginModule).sort()));
"""
    )

    assert output == ["id", "server"]


def test_project_name_handles_opencode_project_object() -> None:
    output = _node_json(
        _plugin_import()
        + """
console.log(JSON.stringify([
  helpers.projectName({ project: { worktree: '/tmp/my-project' } }),
  helpers.projectName({ directory: '/tmp/my-project' }),
  helpers.projectName({ project: { name: 'named-project', worktree: '/tmp/ignored' } }),
]));
"""
    )

    assert output == ["my-project", "my-project", "named-project"]


def test_project_name_prefers_worktree_over_directory_subdir() -> None:
    output = _node_json(
        _plugin_import()
        + """
console.log(JSON.stringify(helpers.projectName({
  project: { worktree: '/tmp/my-project' },
  directory: '/tmp/my-project/subdir',
})));
"""
    )

    assert output == "my-project"


def test_tool_telemetry_logs_opencode_client(tmp_path: Path) -> None:
    log_path = tmp_path / "active-search.jsonl"
    output = _node_json(
        _plugin_import()
        + f"""
await helpers.observeToolCall('memory_search', {{ source_prefix: 'opencode/demo', query: 'do not log me' }}, 'sess-1', {{
  project: 'demo',
  activeSearchLog: {json.dumps(str(log_path))},
  now: () => '2026-05-06T00:00:00.000Z',
}});
const line = (await import('node:fs')).readFileSync({json.dumps(str(log_path))}, 'utf8').trim();
console.log(JSON.stringify(JSON.parse(line)));
"""
    )

    assert output == {
        "client": "opencode",
        "event": "tool_call",
        "tool_name": "memory_search",
        "source_prefix": "opencode/demo",
        "source_prefix_quality": "exact_project",
        "session_id": "sess-1",
        "project": "demo",
        "ts": "2026-05-06T00:00:00.000Z",
    }


def test_tool_after_hook_logs_real_opencode_tool_name(tmp_path: Path) -> None:
    log_path = tmp_path / "active-search.jsonl"
    output = _node_json(
        _plugin_import()
        + f"""
const hooks = server({{}}, {{
  project: 'demo',
  activeSearchLog: {json.dumps(str(log_path))},
  now: () => '2026-05-06T00:00:00.000Z',
}});
await hooks['tool.execute.after']({{ tool: 'memory_search', sessionID: 'sess-3', args: {{ source_prefix: 'opencode/demo' }} }}, {{}});
const line = (await import('node:fs')).readFileSync({json.dumps(str(log_path))}, 'utf8').trim();
console.log(JSON.stringify(JSON.parse(line)));
"""
    )

    assert output["tool_name"] == "memory_search"
    assert output["session_id"] == "sess-3"
    assert output["source_prefix"] == "opencode/demo"


def test_tool_after_hook_logs_memories_mcp_tool_name(tmp_path: Path) -> None:
    log_path = tmp_path / "active-search.jsonl"
    output = _node_json(
        _plugin_import()
        + f"""
const hooks = server({{}}, {{
  project: 'demo',
  activeSearchLog: {json.dumps(str(log_path))},
  now: () => '2026-05-06T00:00:00.000Z',
}});
await hooks['tool.execute.after']({{ tool: 'mcp__memories__memory_search', sessionID: 'sess-4', args: {{ source_prefix: 'opencode/demo' }} }}, {{}});
const line = (await import('node:fs')).readFileSync({json.dumps(str(log_path))}, 'utf8').trim();
console.log(JSON.stringify(JSON.parse(line)));
"""
    )

    assert output["tool_name"] == "mcp__memories__memory_search"
    assert output["session_id"] == "sess-4"


def test_non_memory_tool_does_not_write_default_telemetry(tmp_path: Path) -> None:
    log_path = tmp_path / "active-search.jsonl"
    output = _node_json(
        _plugin_import()
        + f"""
const fs = await import('node:fs');
const hooks = server({{}}, {{ project: 'demo', activeSearchLog: {json.dumps(str(log_path))} }});
await hooks['tool.execute.after']({{ tool: 'bash', sessionID: 'sess', args: {{}} }}, {{}});
console.log(JSON.stringify(fs.existsSync({json.dumps(str(log_path))})));
"""
    )

    assert output is False


def test_plugin_does_not_register_extraction_hook_by_default() -> None:
    output = _node_json(
        _plugin_import()
        + """
const hooks = server({}, { project: 'demo' });
console.log(JSON.stringify({ keys: Object.keys(hooks).sort(), source: server.toString() }));
"""
    )

    assert "experimental.text.complete" not in output["keys"]
    assert "experimental.session.compacting" not in output["keys"]
    assert "memory_extract" not in output["source"]


def test_transform_appends_recall_context_without_telemetry_text_leak(tmp_path: Path) -> None:
    log_path = tmp_path / "active-search.jsonl"
    output = _node_json(
        _plugin_import()
        + f"""
const requests = [];
const fetchImpl = async (url, init) => {{
  requests.push({{ url, headers: init.headers }});
  return {{
    ok: true,
    json: async () => ({{ results: [{{ id: 7, text: 'memory-secret', source: 'opencode/demo' }}] }}),
  }};
}};
const hooks = server({{}}, {{
  project: 'demo',
  fetchImpl,
  memoriesUrl: 'http://memories.local',
  memoriesApiKey: 'test-key',
  activeSearchLog: {json.dumps(str(log_path))},
  now: () => '2026-05-06T00:00:00.000Z',
}});
const hookInput = {{
  messages: [{{ role: 'user', content: 'prompt-secret' }}],
  sessionID: 'sess-2',
}};
const hookOutput = {{ system: ['base system'] }};
await hooks['experimental.chat.system.transform'](hookInput, hookOutput);
let log = '';
try {{ log = (await import('node:fs')).readFileSync({json.dumps(str(log_path))}, 'utf8'); }} catch (error) {{}}
console.log(JSON.stringify({{ system: hookOutput.system, log, requests }}));
"""
    )

    assert isinstance(output["system"], list)
    assert output["system"][0] == "base system"
    assert "OpenCode Memories Recall Context" in output["system"][1]
    assert 'source_prefix="opencode/demo"' in output["system"][1]
    assert "memory-secret" in output["system"][1]
    assert "prompt-secret" not in output["log"]
    assert "memory-secret" not in output["log"]
    assert output["requests"]
    assert all(request["url"].endswith("/search") for request in output["requests"])
    assert all(request["headers"]["X-API-Key"] == "test-key" for request in output["requests"])


def test_transform_reads_default_env_file_under_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env_dir = home / ".config" / "memories"
    env_dir.mkdir(parents=True)
    (env_dir / "env").write_text(
        "MEMORIES_URL=http://env-memories.local\nMEMORIES_API_KEY=env-key\n",
        encoding="utf-8",
    )

    output = _node_json(
        _plugin_import()
        + f"""
const requests = [];
const fetchImpl = async (url, init) => {{
  requests.push({{ url, headers: init.headers }});
  return {{ ok: true, json: async () => ({{ results: [] }}) }};
}};
const hooks = server({{}}, {{ project: 'demo', home: {json.dumps(str(home))}, fetchImpl }});
await hooks['experimental.chat.system.transform']({{ messages: [{{ role: 'user', content: 'remembered setup' }}] }}, {{ system: [] }});
console.log(JSON.stringify(requests));
"""
    )

    assert output
    assert all(request["url"].startswith("http://env-memories.local/") for request in output)
    assert all(request["headers"]["X-API-Key"] == "env-key" for request in output)


def test_observe_tool_call_writes_default_active_search_log_under_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    log_path = home / ".config" / "memories" / "active-search.jsonl"

    output = _node_json(
        _plugin_import()
        + f"""
await helpers.observeToolCall('memory_search', {{ source_prefix: 'opencode/demo' }}, 'sess-default-log', {{
  project: 'demo',
  home: {json.dumps(str(home))},
  now: () => '2026-05-06T00:00:00.000Z',
}});
const fs = await import('node:fs');
const line = fs.readFileSync({json.dumps(str(log_path))}, 'utf8').trim();
console.log(JSON.stringify(JSON.parse(line)));
"""
    )

    assert output["client"] == "opencode"
    assert output["event"] == "tool_call"
    assert output["tool_name"] == "memory_search"
    assert output["session_id"] == "sess-default-log"
    assert output["project"] == "demo"


def test_active_search_metrics_zero_in_default_env_disables_default_log(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env_dir = home / ".config" / "memories"
    env_dir.mkdir(parents=True)
    (env_dir / "env").write_text("MEMORIES_ACTIVE_SEARCH_METRICS=0\n", encoding="utf-8")
    log_path = env_dir / "active-search.jsonl"

    output = _node_json(
        _plugin_import()
        + f"""
await helpers.observeToolCall('memory_search', {{ source_prefix: 'opencode/demo' }}, 'sess-disabled', {{
  project: 'demo',
  home: {json.dumps(str(home))},
}});
const fs = await import('node:fs');
console.log(JSON.stringify(fs.existsSync({json.dumps(str(log_path))})));
"""
    )

    assert output is False


def test_transform_degrades_to_context_when_fetch_throws() -> None:
    output = _node_json(
        _plugin_import()
        + """
const fetchImpl = async () => { throw new Error('network down'); };
const hooks = server({}, { project: 'demo', fetchImpl, memoriesUrl: 'http://memories.local' });
const hookOutput = { system: [] };
await hooks['experimental.chat.system.transform']({ messages: [{ role: 'user', content: 'prompt-secret' }] }, hookOutput);
console.log(JSON.stringify(hookOutput.system));
"""
    )

    assert len(output) == 1
    assert "OpenCode Memories Recall Context" in output[0]
    assert "Recent recalled memories" not in output[0]


def test_transform_fetches_latest_user_text_from_opencode_session_messages() -> None:
    output = _node_json(
        _plugin_import()
        + """
const bodies = [];
const fetchImpl = async (url, init) => {
  bodies.push(JSON.parse(init.body));
  return { ok: true, json: async () => ({ results: [] }) };
};
const client = {
  session: {
    messages: async (request) => ({
      data: [
        { info: { role: 'assistant' }, parts: [{ type: 'text', text: 'ignore me' }] },
        { info: { role: 'user' }, parts: [{ type: 'text', text: 'latest user question' }] },
      ],
      request,
    }),
  },
};
const hooks = server({ client, project: { worktree: '/tmp/demo' }, directory: '/tmp/demo' }, { fetchImpl, memoriesUrl: 'http://memories.local' });
const hookOutput = { system: [] };
await hooks['experimental.chat.system.transform']({ sessionID: 'sess-4', model: {} }, hookOutput);
console.log(JSON.stringify({ bodies, system: hookOutput.system }));
"""
    )

    assert output["bodies"]
    assert all(body["query"] == "latest user question" for body in output["bodies"])
    assert "OpenCode Memories Recall Context" in output["system"][0]


def test_transform_skips_http_search_when_query_is_empty() -> None:
    output = _node_json(
        _plugin_import()
        + """
let fetchCalls = 0;
const fetchImpl = async () => { fetchCalls += 1; throw new Error('fetch must not be called'); };
const hooks = server({}, { project: 'demo', fetchImpl, memoriesUrl: 'http://memories.local' });
const hookOutput = { system: [] };
await hooks['experimental.chat.system.transform']({ sessionID: 'sess-empty', model: {} }, hookOutput);
console.log(JSON.stringify({ fetchCalls, system: hookOutput.system }));
"""
    )

    assert output["fetchCalls"] == 0
    assert len(output["system"]) == 1
    assert "OpenCode Memories Recall Context" in output["system"][0]


def test_transform_aborts_slow_recall_fetches_and_appends_context() -> None:
    output = _node_json(
        _plugin_import()
        + """
const signals = [];
const fetchImpl = (url, init) => {
  signals.push(init.signal);
  return new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  });
};
const hooks = server({}, { project: 'demo', fetchImpl, memoriesUrl: 'http://memories.local', fetchTimeoutMs: 1 });
const hookOutput = { system: [] };
await hooks['experimental.chat.system.transform']({ messages: [{ role: 'user', content: 'prompt-secret' }] }, hookOutput);
console.log(JSON.stringify({
  signalCount: signals.length,
  aborted: signals.map((signal) => Boolean(signal && signal.aborted)),
  system: hookOutput.system,
}));
"""
    )

    assert output["signalCount"] == 5
    assert all(output["aborted"])
    assert len(output["system"]) == 1
    assert "OpenCode Memories Recall Context" in output["system"][0]
