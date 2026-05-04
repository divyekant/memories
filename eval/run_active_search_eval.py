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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.active_search_eval import ActiveSearchCase, score_turn, summarize_results
from eval.cc_executor import CCExecutor
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
                        "timeout": 3,
                    }
                ],
            }
        ],
    }
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")

    if instructions.exists():
        shutil.copy2(instructions, project / "CLAUDE.md")


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

    response = executor.run_prompt(materialized.user_prompt, project_dir)
    result = score_turn(materialized, response, getattr(executor, "last_run_trace", {}) or {})
    result["project"] = project
    result["seeded_memories"] = len(materialized.seed_memories)
    result["prompt_contains_memory_instruction"] = "memory_search" in materialized.user_prompt
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run active memory-search behavior evals")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="JSON case file")
    parser.add_argument("--output", default="", help="Output JSON path")
    parser.add_argument("--agent", default="claude-code", choices=["claude-code"], help="Agent runner")
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
        require_claude=True,
        allow_unsafe_target=os.environ.get("EVAL_ALLOW_UNSAFE_TARGET") == "1",
    )
    if not setup_report.ok:
        for error in setup_report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)

    client = MemoriesClient(url=memories_url, api_key=api_key)
    ready_before = client.ready_status()
    if not ready_before.get("ready", ready_before.get("status_code") == 200):
        print("ERROR: Memories eval service not ready", file=sys.stderr)
        print(json.dumps(ready_before, sort_keys=True), file=sys.stderr)
        sys.exit(1)

    cases = load_cases(args.cases)
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
