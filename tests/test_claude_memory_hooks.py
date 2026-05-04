"""Tests for Claude memory read hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parents[1] / "integrations" / "claude-code" / "hooks"
CODEX_HOOKS_DIR = Path(__file__).resolve().parents[1] / "integrations" / "codex" / "hooks"
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

# GET requests have no body — use null for jq compatibility
if [ -z "$body" ]; then body="null"; fi

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
                        "message": {"content": "Let's design notifications for the BillingService."},
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
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 11,
                        "source": "claude-code/memories",
                        "text": "Notification design is deferred until rate limiting is settled.",
                        "similarity": 0.86,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "response": {
                "results": [
                    {
                        "id": 99,
                        "source": "other/project",
                        "text": "Global memory about notification patterns.",
                        "similarity": 0.75,
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
    # Dual strategy: both scoped and unscoped results appear
    assert "Notification design is deferred until rate limiting is settled." in ctx
    assert "## Retrieved Memories" in ctx
    assert "MANDATORY FIRST ACTION" in ctx
    assert "MUST call memory_search" in ctx
    assert "not a substitute for active search" in ctx
    assert "## Follow-up Response Hint" in ctx
    assert "Search memories for the new topic" in ctx
    # Verify dual search: at least one scoped AND at least one unscoped
    search_calls = [call for call in calls if call["body"] is not None]
    prefixes = [call["body"].get("source_prefix", "") for call in search_calls]
    assert any(p == "" for p in prefixes), f"Expected unscoped search, got: {prefixes}"
    assert any(p == "claude-code/memories" for p in prefixes), f"Expected scoped search, got: {prefixes}"
    assert any(p == "codex/memories" for p in prefixes), f"Expected cross-client scoped search, got: {prefixes}"
    # Transcript context identifiers should appear in query (BillingService from transcript)
    assert any("BillingService" in call["body"]["query"] for call in search_calls), \
        f"Expected transcript identifier in query, queries were: {[c['body']['query'][:80] for c in search_calls]}"


def test_memory_query_redacts_details_for_active_search_required_prompts(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 42,
                        "source": "codex/memories",
                        "text": "Decision: release is gated by setup validation and production write isolation.",
                        "similarity": 0.91,
                    }
                ],
                "count": 1,
            },
        },
    ]

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "Did we already decide how release should be gated?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "MUST call memory_search" in ctx
    assert "candidate memory id=42" in ctx
    assert "setup validation and production write isolation" not in ctx


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
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 4,
                        "source": "codex/memories",
                        "text": "Codex had relevant prior project context for this repository.",
                        "similarity": 0.85,
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
    assert "IMPORTANT: ALWAYS search memories BEFORE responding" in ctx
    assert "MANDATORY FIRST ACTION" in ctx
    assert "claude-code/memories" in ctx
    assert "codex/memories" in ctx
    assert "learning/memories" in ctx
    assert "wip/memories" in ctx

    memory_file = home_dir / ".claude" / "projects" / "-Users-example-memories" / "memory" / "MEMORY.md"
    assert memory_file.exists()
    memory_text = memory_file.read_text()
    assert "## Synced from Memories" in memory_text
    assert "Claude read hooks should search project-scoped memories" in memory_text
    assert "## Memory Playbook" not in memory_text

    search_calls = [call for call in calls if call["body"] is not None]
    prefixes = [call["body"].get("source_prefix", "") for call in search_calls]
    # 4th call is the dedicated deferred-work surfacing search
    assert prefixes == [
        "claude-code/memories",
        "codex/memories",
        "learning/memories",
        "wip/memories",
        "wip/memories",
    ]

    # Deferred work section should appear when wip results exist
    assert "Deferred Work" in ctx


def test_memory_recall_playbook_contains_mandatory_directives(tmp_path: Path) -> None:
    """Playbook should use strong mandatory language, not soft suggestions."""
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 1,
                        "source": "claude-code/memories",
                        "text": "Test memory for playbook verification.",
                        "similarity": 0.92,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {"cwd": "/Users/example/memories"}
    result, _, _ = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]

    assert "IMPORTANT: ALWAYS search memories BEFORE responding" in ctx
    assert "MANDATORY FIRST ACTION" in ctx
    assert "ToolSearch" in ctx
    assert "You MUST call memory_search" in ctx
    assert "keyword-matched, not semantic" in ctx
    assert "Prior decisions aren't in code" in ctx


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
            "source_prefix": "claude-code/memories",
            "response": {"results": [], "count": 0},
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
    search_calls = [call for call in calls if call["body"] is not None]
    prefixes = [call["body"].get("source_prefix", "") for call in search_calls]
    # 4th call is the dedicated deferred-work surfacing search
    assert prefixes == [
        "codex/memories",
        "claude-code/memories",
        "learning/memories",
        "wip/memories",
        "wip/memories",
    ]


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


def test_build_keyword_bag_strips_filler_keeps_domain_terms(tmp_path: Path) -> None:
    """build_keyword_bag should strip filler words and keep domain terms + identifiers."""
    # Extract just the function from the script and call it directly
    test_script = tmp_path / "test_bag.sh"
    test_script.write_text(
        f"""#!/bin/bash
set -euo pipefail
# Extract and source only the build_keyword_bag function
eval "$(sed -n '/^build_keyword_bag()/,/^}}/p' "{QUERY_SCRIPT}")"
build_keyword_bag "ok so the UserPrefs module uses fetch_config and the MAX_RETRIES constant for v2.1.0 of PR-42" "myproject"
"""
    )
    test_script.chmod(0o755)

    env = os.environ.copy()
    result = subprocess.run(
        ["bash", str(test_script)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    output = result.stdout.strip()

    # Should contain the project name
    assert "myproject" in output, f"Expected 'myproject' in output, got: {output!r}\nstderr: {result.stderr}"
    # Should contain camelCase identifier
    assert "UserPrefs" in output, f"Expected 'UserPrefs' in output, got: {output!r}"
    # Should contain snake_case identifier
    assert "fetch_config" in output, f"Expected 'fetch_config' in output, got: {output!r}"
    # Should contain SCREAMING_SNAKE constant
    assert "MAX_RETRIES" in output, f"Expected 'MAX_RETRIES' in output, got: {output!r}"
    # Should contain version reference
    assert "v2.1.0" in output, f"Expected 'v2.1.0' in output, got: {output!r}"
    # Should contain PR reference
    assert "PR-42" in output, f"Expected 'PR-42' in output, got: {output!r}"
    # Should NOT contain filler words
    for filler in ["ok", "so", "the", "uses", "and", "for", "of"]:
        # Check it's not present as a standalone word in output
        words = output.lower().split()
        assert filler not in words, f"Filler word '{filler}' should not be in output: {output!r}"


def test_dual_search_strategy_unscoped_and_all_default_prefixes(tmp_path: Path) -> None:
    """Dual search fires unscoped plus all default project source families."""
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "response": {
                "results": [
                    {
                        "id": 101,
                        "source": "other/project",
                        "text": "Global unscoped result about deploy patterns.",
                        "similarity": 0.82,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 102,
                        "source": "claude-code/memories",
                        "text": "Project-scoped result about deploy hooks.",
                        "similarity": 0.88,
                    }
                ],
                "count": 1,
            },
        },
    ]

    # Use a prompt that does NOT trigger intent-prefix biasing (not fix/debug/how/setup)
    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "explain the deploy pipeline and the WebhookHandler architecture",
    }

    result, calls, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0, f"Script failed: {result.stderr}"
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]

    # Both results should appear
    assert "Global unscoped result" in ctx
    assert "Project-scoped result" in ctx

    # Verify search calls: at least one unscoped and each default project family.
    search_calls = [call for call in calls if call["body"] is not None]
    prefixes = [call["body"].get("source_prefix", "") for call in search_calls]
    assert any(p == "" for p in prefixes), f"Expected at least one unscoped search, got prefixes: {prefixes}"
    assert any(p == "claude-code/memories" for p in prefixes), f"Expected claude-code/memories scoped search, got: {prefixes}"
    assert any(p == "codex/memories" for p in prefixes), f"Expected codex/memories scoped search, got: {prefixes}"
    assert any(p == "learning/memories" for p in prefixes), f"Expected learning/memories scoped search, got: {prefixes}"
    assert any(p == "wip/memories" for p in prefixes), f"Expected wip/memories scoped search, got: {prefixes}"


def test_memory_hooks_honor_disabled_flag(tmp_path: Path) -> None:
    """MEMORIES_DISABLED lets eval and sandboxed agents suppress global hooks."""
    payload = {"cwd": "/Users/example/memories", "prompt": "What should I remember?"}

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_DISABLED": "1"},
    )

    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_codex_memory_hooks_honor_disabled_flag(tmp_path: Path) -> None:
    """Codex hooks must also suppress global recall when eval disables memories."""
    payload = {"cwd": "/Users/example/memories", "source": "startup"}

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_DISABLED": "1"},
    )

    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""
