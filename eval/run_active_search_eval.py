#!/usr/bin/env python3
"""Run active memory-search behavior evals.

Unlike LongMemEval system mode, these prompts do not tell the agent to use
memory_search. The score is based on whether the normal product instructions
and hooks caused the agent to search when the user turn required prior context.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.active_search_eval import (
    ActiveSearchCase,
    is_memory_search_tool_name,
    score_turn,
    summarize_results,
)
from eval.cc_executor import AGENT_ENV_BLOCKLIST, CCExecutor
from eval.memories_client import MemoriesClient
from eval.setup_validation import DEFAULT_EVAL_MEMORIES_URL, resolve_eval_memories_url, validate_eval_setup


DEFAULT_CASES_PATH = Path(__file__).with_name("active_search_cases.json")
REPO_ROOT = Path(__file__).resolve().parent.parent


def install_claude_product_read_hooks(
    *,
    project_dir: str,
    hooks_dir: str | None = None,
    instructions_path: str | None = None,
) -> None:
    """Install this worktree's read hooks/instructions into an isolated project."""

    project = Path(project_dir)
    hook_root = Path(hooks_dir) if hooks_dir else REPO_ROOT / "plugin" / "hooks"
    instructions = Path(instructions_path) if instructions_path else REPO_ROOT / "plugin" / "CLAUDE.md"

    settings_dir = project / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    settings["hooks"] = {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": str(hook_root / "memory-recall.sh"),
                        "timeout": 5,
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": str(hook_root / "memory-query.sh"),
                        "timeout": 10,
                    }
                ],
            }
        ],
    }
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")

    if instructions.exists():
        shutil.copy2(instructions, project / "CLAUDE.md")


def install_codex_product_read_hooks(
    *,
    codex_home: str,
    hooks_dir: str,
    memories_url: str,
    api_key: str,
    mcp_server_path: str,
) -> None:
    """Install this worktree's Codex read hooks and MCP config into temp CODEX_HOME."""

    home = Path(codex_home)
    home.mkdir(parents=True, exist_ok=True)
    hook_root = Path(hooks_dir)
    hooks = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(hook_root / "memory-recall.sh"),
                            "timeout": 5,
                        }
                    ],
                }
            ],
            "UserPromptSubmit": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(hook_root / "memory-query.sh"),
                            "timeout": 10,
                        }
                    ],
                }
            ],
        }
    }
    (home / "hooks.json").write_text(json.dumps(hooks, indent=2, sort_keys=True), encoding="utf-8")
    (home / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "mcp__memories__memory_search",
                        "mcp__memories__memory_list",
                        "mcp__memories__memory_count",
                    ]
                }
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (home / "memories-eval-env").write_text(
        "\n".join(
            [
                f"export MEMORIES_URL={json.dumps(memories_url)}",
                f"export MEMORIES_API_KEY={json.dumps(api_key)}",
                "export MEMORIES_BACKENDS_FILE=__eval_single_backend__",
                "export MEMORIES_DISABLED=0",
                f"export MEMORIES_ACTIVE_SEARCH_LOG={json.dumps(str(home / 'active-search.jsonl'))}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (home / "config.toml").write_text(
        "\n".join(
            [
                'developer_instructions = """',
                "Use the Memories MCP tools as your memory layer.",
                "READ: Run memory_search before implementation-heavy responses, clarifying questions, or any turn that depends on prior decisions, prior sessions, project history, deferred work, conventions, or cross-session context. Hook-injected memories are useful hints, not a substitute for active search.",
                "SKIP: Do not call memory_search for self-contained prompts that do not depend on prior/project context, such as arithmetic, translation, formatting, or generic facts.",
                "Source prefixes: replace {project} with the current working directory basename. Search exact project-scoped prefixes first: codex/{project}, claude-code/{project}, learning/{project}, and wip/{project}. If hook candidate pointers list a source, use that exact source_prefix. Do not use broad family prefixes like codex/, claude-code/, learning/, wip/, or unscoped search until the exact project prefixes have been tried.",
                '"""',
                "",
                "[mcp_servers.memories]",
                'command = "node"',
                f"args = [{json.dumps(mcp_server_path)}]",
                "",
                "[mcp_servers.memories.env]",
                f"MEMORIES_URL = {json.dumps(memories_url)}",
                f"MEMORIES_API_KEY = {json.dumps(api_key)}",
                'MEMORIES_BACKENDS_FILE = "__eval_single_backend__"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def parse_codex_json_trace(stdout: str, stderr: str = "", returncode: int | None = None) -> tuple[str, dict[str, Any]]:
    """Parse `codex exec --json` output into final answer text and tool trace."""

    trace: dict[str, Any] = {
        "output_format": "codex-json",
        "event_count": 0,
        "parse_errors": 0,
        "tool_calls": [],
        "stdout_chars": len(stdout or ""),
        "stderr_chars": len(stderr or ""),
        "stderr_excerpt": (stderr or "")[:1000],
        "returncode": returncode,
        "error_kind": "",
    }
    answer = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            trace["parse_errors"] += 1
            continue
        if not isinstance(event, dict):
            continue
        trace["event_count"] += 1
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type", ""))
        if item_type == "agent_message" and isinstance(item.get("text"), str):
            answer = item["text"].strip()
        name = str(item.get("name") or item.get("tool_name") or "")
        if not name and item.get("type") == "mcp_tool_call":
            server = str(item.get("server") or "")
            tool = str(item.get("tool") or "")
            if server and tool:
                name = f"mcp__{server}__{tool}"
        if is_memory_search_tool_name(name):
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else item.get("input")
            if not isinstance(args, dict):
                args = {}
            call = {"name": name}
            if "query" in args:
                call["query"] = str(args["query"])
            if "source_prefix" in args:
                call["source_prefix"] = str(args["source_prefix"])
            trace["tool_calls"].append(call)
    if returncode not in (0, None):
        trace["error_kind"] = "agent_error"
    return answer, trace


class CodexExecutor:
    """Runs prompts through Codex exec in an isolated CODEX_HOME."""

    def __init__(self, *, codex_home: str, timeout: int = 180, model: str = "") -> None:
        self.codex_home = codex_home
        self.timeout = timeout
        self.model = model
        self.last_run_trace: dict[str, Any] = {}

    def run_prompt(self, prompt: str, project_dir: str, require_memories: bool = False) -> str:
        if require_memories:
            config_path = Path(self.codex_home) / "config.toml"
            if not config_path.exists() or "[mcp_servers.memories]" not in config_path.read_text(
                encoding="utf-8",
                errors="replace",
            ):
                raise RuntimeError(
                    "Active-search eval requires Memories MCP config in isolated CODEX_HOME"
                )
        cmd = [
            "codex",
            "exec",
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-C",
            project_dir,
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in AGENT_ENV_BLOCKLIST and key != "MCP_CONTEXT"
        }
        env["CODEX_HOME"] = self.codex_home
        env["MEMORIES_ENV_FILE"] = str(Path(self.codex_home) / "memories-eval-env")
        env["MEMORIES_ACTIVE_SEARCH_LOG"] = str(Path(self.codex_home) / "active-search.jsonl")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=project_dir,
                env=env,
            )
            answer, self.last_run_trace = parse_codex_json_trace(
                result.stdout or "",
                stderr=result.stderr or "",
                returncode=result.returncode,
            )
            return answer or (result.stderr or "").strip()
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            _, self.last_run_trace = parse_codex_json_trace(stdout, stderr=stderr, returncode=None)
            self.last_run_trace["error_kind"] = "timeout"
            return f"[TIMEOUT] Codex timed out after {self.timeout}s"


def load_cases(path: str | Path = DEFAULT_CASES_PATH) -> list[ActiveSearchCase]:
    """Load active-search cases from JSON."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[ActiveSearchCase] = []
    for item in raw:
        cases.append(
            ActiveSearchCase(
                case_id=str(item["case_id"]),
                agent=str(item.get("agent", "claude-code")),
                user_prompt=str(item["user_prompt"]),
                should_search=bool(item.get("should_search", True)),
                expected_source_prefixes=tuple(str(v) for v in item.get("expected_source_prefixes", [])),
                expected_answer_terms=tuple(str(v) for v in item.get("expected_answer_terms", [])),
                seed_memories=tuple(dict(v) for v in item.get("seed_memories", [])),
            )
        )
    return cases


def _replace_project(value: str, project: str) -> str:
    return value.replace("{project}", project)


def materialize_case(case: ActiveSearchCase, project: str) -> ActiveSearchCase:
    """Replace {project} placeholders with the isolated eval project name."""

    seed_memories: list[dict[str, Any]] = []
    for memory in case.seed_memories:
        materialized = dict(memory)
        if "source" in materialized:
            materialized["source"] = _replace_project(str(materialized["source"]), project)
        seed_memories.append(materialized)

    return ActiveSearchCase(
        case_id=case.case_id,
        agent=case.agent,
        user_prompt=case.user_prompt,
        should_search=case.should_search,
        expected_source_prefixes=tuple(
            _replace_project(prefix, project) for prefix in case.expected_source_prefixes
        ),
        expected_answer_terms=case.expected_answer_terms,
        seed_memories=tuple(seed_memories),
    )


def _project_name(project_dir: str) -> str:
    return Path(project_dir).resolve().name


def _case_prefixes(case: ActiveSearchCase) -> list[str]:
    prefixes = {str(memory.get("source", "")) for memory in case.seed_memories}
    prefixes.update(case.expected_source_prefixes)
    return sorted(prefix for prefix in prefixes if prefix)


def clear_case_memories(case: ActiveSearchCase, client: MemoriesClient) -> dict[str, int]:
    """Remove eval memories for a materialized case."""

    deleted: dict[str, int] = {}
    for prefix in _case_prefixes(case):
        deleted[prefix] = client.clear_by_prefix(prefix)
    return deleted


def run_case(
    case: ActiveSearchCase,
    *,
    client: MemoriesClient,
    executor: CCExecutor,
    project_dir: str,
) -> dict[str, Any]:
    """Seed one active-search case, run the raw user prompt, and score trace."""

    project = _project_name(project_dir)
    materialized = materialize_case(case, project=project)
    if materialized.seed_memories:
        client.seed_memories(list(materialized.seed_memories))

    response = executor.run_prompt(materialized.user_prompt, project_dir, require_memories=True)
    result = score_turn(materialized, response, getattr(executor, "last_run_trace", {}) or {})
    result["project"] = project
    result["seeded_memories"] = len(materialized.seed_memories)
    result["prompt_contains_memory_instruction"] = "memory_search" in materialized.user_prompt
    result["agent_trace"] = getattr(executor, "last_run_trace", {}) or {}
    return result


def run_live_claude_code(
    cases: list[ActiveSearchCase],
    *,
    client: MemoriesClient,
    memories_url: str,
    api_key: str,
    mcp_server_path: str,
    agent_model: str = "",
    agent_timeout: int = 180,
) -> dict[str, Any]:
    """Run cases through Claude Code with normal prompt text and MCP trace capture."""

    executor = CCExecutor(
        timeout=agent_timeout,
        memories_url=memories_url,
        memories_api_key=api_key,
        mcp_server_path=mcp_server_path,
        model=agent_model,
        capture_trace=True,
    )
    CCExecutor.cleanup_stale_auto_memory()

    results: list[dict[str, Any]] = []
    cleanup: list[dict[str, Any]] = []
    start = time.time()
    for case in cases:
        project_dir = executor.create_isolated_project(with_memories=True)
        install_claude_product_read_hooks(project_dir=project_dir)
        materialized = materialize_case(case, project=_project_name(project_dir))
        try:
            before_deleted = clear_case_memories(materialized, client)
            result = run_case(case, client=client, executor=executor, project_dir=project_dir)
            results.append(result)
        finally:
            after_deleted = clear_case_memories(materialized, client)
            cleanup.append({
                "case_id": case.case_id,
                "before_deleted": before_deleted,
                "after_deleted": after_deleted,
            })
            executor.cleanup_project(project_dir)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "eval": "active-search",
        "agent": "claude-code",
        "agent_model": agent_model or "default",
        "duration_seconds": round(time.time() - start, 3),
        "summary": summarize_results(results),
        "results": results,
        "cleanup": cleanup,
    }


def run_live_codex(
    cases: list[ActiveSearchCase],
    *,
    client: MemoriesClient,
    memories_url: str,
    api_key: str,
    mcp_server_path: str,
    agent_model: str = "",
    agent_timeout: int = 180,
) -> dict[str, Any]:
    """Run cases through Codex exec with worktree Codex hooks and MCP config."""

    start = time.time()
    root = Path(tempfile.mkdtemp(prefix="codex_active_eval_"))
    codex_home = root / ".codex"
    source_auth = Path.home() / ".codex" / "auth.json"
    if source_auth.exists():
        codex_home.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_auth, codex_home / "auth.json")
    install_codex_product_read_hooks(
        codex_home=str(codex_home),
        hooks_dir=str(REPO_ROOT / "integrations" / "codex" / "hooks"),
        memories_url=memories_url,
        api_key=api_key,
        mcp_server_path=mcp_server_path,
    )
    executor = CodexExecutor(codex_home=str(codex_home), timeout=agent_timeout, model=agent_model)

    results: list[dict[str, Any]] = []
    cleanup: list[dict[str, Any]] = []
    try:
        for case in cases:
            project_dir = tempfile.mkdtemp(prefix="codex_eval_")
            materialized = materialize_case(case, project=_project_name(project_dir))
            try:
                before_deleted = clear_case_memories(materialized, client)
                result = run_case(case, client=client, executor=executor, project_dir=project_dir)
                result["agent"] = "codex"
                results.append(result)
            finally:
                after_deleted = clear_case_memories(materialized, client)
                cleanup.append({
                    "case_id": case.case_id,
                    "before_deleted": before_deleted,
                    "after_deleted": after_deleted,
                })
                shutil.rmtree(project_dir, ignore_errors=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "eval": "active-search",
        "agent": "codex",
        "agent_model": agent_model or "default",
        "duration_seconds": round(time.time() - start, 3),
        "summary": summarize_results(results),
        "results": results,
        "cleanup": cleanup,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run active memory-search behavior evals")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="JSON case file")
    parser.add_argument("--output", default="", help="Output JSON path")
    parser.add_argument("--agent", default="claude-code", choices=["claude-code", "codex"], help="Agent runner")
    parser.add_argument("--agent-model", default="", help="Optional model passed to Claude Code")
    parser.add_argument("--agent-timeout", type=int, default=180, help="Agent timeout seconds")
    args = parser.parse_args()

    memories_url = resolve_eval_memories_url(DEFAULT_EVAL_MEMORIES_URL)
    api_key = os.environ.get("MEMORIES_API_KEY", "")
    mcp_server_path = os.environ.get(
        "EVAL_MCP_SERVER_PATH",
        str(Path(__file__).parent.parent / "mcp-server" / "index.js"),
    )
    setup_report = validate_eval_setup(
        memories_url=memories_url,
        api_key=api_key,
        require_api_key=True,
        mcp_server_path=mcp_server_path,
        require_mcp=True,
        require_claude=args.agent == "claude-code",
        allow_unsafe_target=os.environ.get("EVAL_ALLOW_UNSAFE_TARGET") == "1",
    )
    if not setup_report.ok:
        for error in setup_report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
    if args.agent == "codex" and shutil.which("codex") is None:
        print("ERROR: codex CLI not found in PATH; active-search eval cannot run.", file=sys.stderr)
        sys.exit(2)

    client = MemoriesClient(url=memories_url, api_key=api_key)
    ready_before = client.ready_status()
    if not ready_before.get("ready", ready_before.get("status_code") == 200):
        print("ERROR: Memories eval service not ready", file=sys.stderr)
        print(json.dumps(ready_before, sort_keys=True), file=sys.stderr)
        sys.exit(1)

    cases = load_cases(args.cases)
    if args.agent == "codex":
        report = run_live_codex(
            cases,
            client=client,
            memories_url=memories_url,
            api_key=api_key,
            mcp_server_path=mcp_server_path,
            agent_model=args.agent_model,
            agent_timeout=args.agent_timeout,
        )
    else:
        report = run_live_claude_code(
            cases,
            client=client,
            memories_url=memories_url,
            api_key=api_key,
            mcp_server_path=mcp_server_path,
            agent_model=args.agent_model,
            agent_timeout=args.agent_timeout,
        )
    report["setup_validation"] = {
        "ok": setup_report.ok,
        "info": list(setup_report.info),
        "warnings": list(setup_report.warnings),
        "errors": list(setup_report.errors),
    }
    report["ready_before"] = ready_before
    report["ready_after"] = client.ready_status()

    output_path = args.output or str(
        Path("eval/results") / f"active-search-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    print(f"active_search_report={output_path}")


if __name__ == "__main__":
    main()
