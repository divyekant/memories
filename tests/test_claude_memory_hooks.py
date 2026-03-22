"""Tests for Claude memory read hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parents[1] / "integrations" / "claude-code" / "hooks"
QUERY_SCRIPT = HOOKS_DIR / "memory-query.sh"
RECALL_SCRIPT = HOOKS_DIR / "memory-recall.sh"
EXTRACT_SCRIPT = HOOKS_DIR / "memory-extract.sh"


def _write_fake_curl(bin_dir: Path) -> Path:
    script = bin_dir / "curl"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_CURL_CALLS:?}"
: "${FAKE_CURL_RESPONSES:?}"

url=""
body=""
pending_data=0

for arg in "$@"; do
  if [ "$pending_data" -eq 1 ]; then
    body="$arg"
    pending_data=0
    continue
  fi

  case "$arg" in
    -d|--data|--data-raw|--data-binary)
      pending_data=1
      ;;
    http://*|https://*)
      url="$arg"
      ;;
  esac
done

jq -nc --arg url "$url" --argjson body "$body" '{url: $url, body: $body}' >> "$FAKE_CURL_CALLS"

jq -c --arg url "$url" --argjson body "$body" '
  ([
    .[]
    | . as $rule
    | select(($rule.url_suffix == null) or ($url | endswith($rule.url_suffix)))
    | select(($rule.source_prefix // "") == (($body.source_prefix // "")))
    | select(
        ($rule.query_contains // null) == null
        or (($body.query // "") | contains($rule.query_contains))
      )
    | $rule.response
  ][0]) // {"results": [], "count": 0}
' "$FAKE_CURL_RESPONSES"
"""
    )
    script.chmod(0o755)
    return script


def _run_hook(
    script: Path,
    tmp_path: Path,
    payload: dict[str, object],
    responses: list[dict[str, object]],
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    calls_file = tmp_path / "curl-calls.jsonl"
    responses_file = tmp_path / "curl-responses.json"
    responses_file.write_text(json.dumps(responses))

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home_dir),
            "MEMORIES_URL": "http://127.0.0.1:9999",
            "MEMORIES_API_KEY": "test-key",
            "MEMORIES_ENV_FILE": str(tmp_path / "missing-env"),
            "FAKE_CURL_CALLS": str(calls_file),
            "FAKE_CURL_RESPONSES": str(responses_file),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    calls: list[dict[str, object]] = []
    if calls_file.exists():
        calls = [json.loads(line) for line in calls_file.read_text().splitlines() if line.strip()]

    return result, calls, home_dir


def _install_hook_fixture(home_dir: Path, filename: str) -> Path:
    hook_dir = home_dir / ".codex" / "hooks" / "memory"
    hook_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOKS_DIR / filename, hook_dir / filename)
    shutil.copy2(HOOKS_DIR / "_lib.sh", hook_dir / "_lib.sh")
    if (HOOKS_DIR / "response-hints.json").exists():
        shutil.copy2(HOOKS_DIR / "response-hints.json", hook_dir / "response-hints.json")
    return hook_dir / filename


def test_memory_query_uses_transcript_context_for_short_followups(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {"content": "Let's design notifications for the billing service."},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": "We should revisit rate limiting before locking the webhook shape."},
                    }
                ),
            ]
        )
        + "\n"
    )

    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "wip/memories",
            "query_contains": "notifications",
            "response": {
                "results": [
                    {
                        "id": 11,
                        "source": "wip/memories",
                        "text": "Notification design is deferred until rate limiting is settled.",
                        "similarity": 0.86,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "response": {
                "results": [
                    {
                        "id": 99,
                        "source": "other/project",
                        "text": "Unrelated global memory that should not appear.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        },
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "what about retries?",
        "transcript_path": str(transcript),
    }

    result, calls, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Notification design is deferred until rate limiting is settled." in ctx
    assert "Unrelated global memory" not in ctx
    assert "## Retrieved Memories" in ctx
    assert "## Follow-up Response Hint" in ctx
    assert "Search memories for the new topic" in ctx
    assert all(call["body"].get("source_prefix", "") != "" for call in calls)
    assert any("notifications" in call["body"]["query"] for call in calls)


def test_memory_query_falls_back_to_global_search_when_scoped_is_empty(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "query_contains": "redis connection failure workaround",
            "response": {
                "results": [
                    {
                        "id": 21,
                        "source": "infra/shared",
                        "text": "Redis connection issues were fixed by setting REDIS_URL explicitly.",
                        "similarity": 0.8,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "look up the redis connection failure workaround",
    }

    result, calls, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Redis connection issues were fixed by setting REDIS_URL explicitly." in ctx
    assert any(call["body"].get("source_prefix", "") == "" for call in calls)


def test_memory_query_adds_confirmation_hint_for_confirmation_followups(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "query_contains": "does that still apply?",
            "response": {
                "results": [
                    {
                        "id": 31,
                        "source": "claude-code/memories",
                        "text": "SQLite is preferred over Redis for the local cache in single-node deployments.",
                        "similarity": 0.88,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "does that still apply?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "## Follow-up Response Hint" in ctx
    assert "prior decision or fact still holds" in ctx
    assert "yes, still applies because" in ctx


def test_memory_query_adds_continuation_hint_for_resume_prompts(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "query_contains": "We're still on the local cache setup.",
            "response": {
                "results": [
                    {
                        "id": 41,
                        "source": "claude-code/memories",
                        "text": "memories decision: SQLite is preferred over Redis for the local cache in single-node deployments.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "We're still on the local cache setup.",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "## Context Continuation Hint" in ctx
    assert "confirming a current choice" in ctx
    assert "Do not ask to reconfirm" in ctx


def test_memory_query_adds_switch_now_hint_for_change_prompts(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "query_contains": "should we switch to Redis now?",
            "response": {
                "results": [
                    {
                        "id": 51,
                        "source": "claude-code/memories",
                        "text": "memories decision: keep the build cache manifest in SQLite until multiple workers need shared invalidation.",
                        "similarity": 0.91,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "should we switch to Redis now?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "## Follow-up Response Hint" in ctx
    assert "considering a switch" in ctx
    assert "evaluate the proposed switch" in ctx


def test_memory_query_adds_for_now_hint_for_simple_followups(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "query_contains": "is file-based storage okay for now?",
            "response": {
                "results": [
                    {
                        "id": 61,
                        "source": "claude-code/memories",
                        "text": "memories decision: keep field-note drafts in local Markdown files until cross-device sync is required.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "is file-based storage okay for now?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "## Follow-up Response Hint" in ctx
    assert "current state provisionally" in ctx
    assert "boundary condition" in ctx


def test_memory_recall_scopes_results_and_writes_memory_file(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 1,
                        "source": "claude-code/memories",
                        "text": "Claude read hooks should search project-scoped memories before broad global search.",
                        "similarity": 0.92,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_suffix": "/search",
            "source_prefix": "learning/memories",
            "response": {
                "results": [
                    {
                        "id": 2,
                        "source": "learning/memories",
                        "text": "Short follow-up prompts need transcript context to retrieve the right memories.",
                        "similarity": 0.88,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_suffix": "/search",
            "source_prefix": "wip/memories",
            "response": {
                "results": [
                    {
                        "id": 3,
                        "source": "wip/memories",
                        "text": "Deferred: tighten Claude session-start recall before broader automation work.",
                        "similarity": 0.84,
                    }
                ],
                "count": 1,
            },
        },
    ]

    payload = {"cwd": "/Users/example/memories"}

    result, calls, home_dir = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "## Relevant Memories" in ctx
    assert "## Memory Playbook" in ctx
    assert "answer that directly with words like" in ctx
    assert "carry that clause forward in the answer" in ctx
    assert "claude-code/memories" in ctx
    assert "learning/memories" in ctx
    assert "wip/memories" in ctx

    memory_file = home_dir / ".claude" / "projects" / "-Users-example-memories" / "memory" / "MEMORY.md"
    assert memory_file.exists()
    memory_text = memory_file.read_text()
    assert "## Synced from Memories" in memory_text
    assert "Claude read hooks should search project-scoped memories" in memory_text
    assert "## Memory Playbook" not in memory_text

    prefixes = [call["body"].get("source_prefix", "") for call in calls]
    # 4th call is the dedicated deferred-work surfacing search
    assert prefixes == ["claude-code/memories", "learning/memories", "wip/memories", "wip/memories"]

    # Deferred work section should appear when wip results exist
    assert "Deferred Work" in ctx


def test_memory_recall_replaces_existing_synced_block_without_duplication(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 1,
                        "source": "claude-code/memories",
                        "text": "SQLite stays preferred for the local cache.",
                        "similarity": 0.92,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {"cwd": "/Users/example/memories"}
    _, _, home_dir = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses)

    memory_file = home_dir / ".claude" / "projects" / "-Users-example-memories" / "memory" / "MEMORY.md"
    original = memory_file.read_text()
    assert original.count("<!-- SYNCED-FROM-MEMORIES-MCP -->") == 1

    result, _, _ = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    updated = memory_file.read_text()
    assert updated.count("<!-- SYNCED-FROM-MEMORIES-MCP -->") == 1
    assert updated.count("## Synced from Memories") == 1


def test_memory_recall_uses_codex_source_prefixes_when_installed_under_codex(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    installed_recall = _install_hook_fixture(home_dir, "memory-recall.sh")

    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 1,
                        "source": "codex/memories",
                        "text": "Codex sessions should recall project decisions from codex/{project}.",
                        "similarity": 0.92,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_suffix": "/search",
            "source_prefix": "learning/memories",
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "wip/memories",
            "response": {"results": [], "count": 0},
        },
    ]

    payload = {"cwd": "/Users/example/memories"}
    result, calls, _ = _run_hook(installed_recall, tmp_path, payload, responses)

    assert result.returncode == 0
    prefixes = [call["body"].get("source_prefix", "") for call in calls]
    # 4th call is the dedicated deferred-work surfacing search
    assert prefixes == ["codex/memories", "learning/memories", "wip/memories", "wip/memories"]


def test_memory_extract_uses_codex_source_when_installed_under_codex(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    installed_extract = _install_hook_fixture(home_dir, "memory-extract.sh")

    payload = {
        "cwd": "/Users/example/memories",
        "last_assistant_message": "Assistant: remembered and stored the decision.",
    }

    result, calls, _ = _run_hook(installed_extract, tmp_path, payload, responses=[])

    assert result.returncode == 0
    assert calls
    body = calls[0]["body"]
    assert body["source"] == "codex/memories"
