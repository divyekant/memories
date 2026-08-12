"""Tests for Claude memory read hooks."""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parents[1] / "integrations" / "claude-code" / "hooks"
CODEX_HOOKS_DIR = Path(__file__).resolve().parents[1] / "integrations" / "codex" / "hooks"
QUERY_SCRIPT = HOOKS_DIR / "memory-query.sh"
RECALL_SCRIPT = HOOKS_DIR / "memory-recall.sh"
REHYDRATE_SCRIPT = HOOKS_DIR / "memory-rehydrate.sh"
EXTRACT_SCRIPT = HOOKS_DIR / "memory-extract.sh"
OBSERVE_SCRIPT = HOOKS_DIR / "memory-observe.sh"


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
pending_write=0
write_out=""

for arg in "$@"; do
  if [ "$pending_data" -eq 1 ]; then
    body="$arg"
    pending_data=0
    continue
  fi
  if [ "$pending_write" -eq 1 ]; then
    write_out="$arg"
    pending_write=0
    continue
  fi

  case "$arg" in
    -d|--data|--data-raw|--data-binary)
      pending_data=1
      ;;
    -w|--write-out)
      pending_write=1
      ;;
    http://*|https://*)
      url="$arg"
      ;;
  esac
done

# GET requests have no body — use null for jq compatibility
if [ -z "$body" ]; then body="null"; fi

jq -nc --arg url "$url" --argjson body "$body" '{url: $url, body: $body}' >> "$FAKE_CURL_CALLS"

# Rule matching, shared shape across the three lookups below:
#   url_suffix      — optional, match-any when absent
#   url_contains     — optional, match-any when absent. Lets a rule target a
#                       specific HOST regardless of endpoint or prefix (e.g.
#                       "this backend is down for every request it gets").
#   source_prefix    — only enforced when the rule specifies the key at all;
#                       absent means match any prefix (a GET has body=null,
#                       so a per-host rule needs this to also match POSTs
#                       with a real source_prefix).
#   query_contains   — optional, match-any when absent.
#   delay            — optional seconds to sleep before returning a matching
#                      response, for bounded-time lifecycle tests.
# fail: true on the winning rule simulates a connection failure — no output,
# nonzero exit — for real per-host failure, not just a canned error body.
delay_seconds=$(jq -r --arg url "$url" --argjson body "$body" '
  ([
    .[]
    | . as $rule
    | select(($rule.url_suffix == null) or ($url | endswith($rule.url_suffix)))
    | select(($rule.url_contains == null) or ($url | contains($rule.url_contains)))
    | select(($rule | has("source_prefix") | not) or (($rule.source_prefix // "") == (($body.source_prefix // ""))))
    | select(
        ($rule.query_contains // null) == null
        or (($body.query // "") | contains($rule.query_contains))
      )
    | ($rule.delay // 0)
  ][0]) // 0
' "$FAKE_CURL_RESPONSES")
case "$delay_seconds" in
  ''|0|0.0|null) ;;
  *) sleep "$delay_seconds" ;;
esac
fail_flag=$(jq -r --arg url "$url" --argjson body "$body" '
  ([
    .[]
    | . as $rule
    | select(($rule.url_suffix == null) or ($url | endswith($rule.url_suffix)))
    | select(($rule.url_contains == null) or ($url | contains($rule.url_contains)))
    | select(($rule | has("source_prefix") | not) or (($rule.source_prefix // "") == (($body.source_prefix // ""))))
    | select(
        ($rule.query_contains // null) == null
        or (($body.query // "") | contains($rule.query_contains))
      )
    | ($rule.fail // false)
  ][0]) // false
' "$FAKE_CURL_RESPONSES")

if [ "$fail_flag" = "true" ]; then
  exit 7
fi

response_body=$(jq -c --arg url "$url" --argjson body "$body" '
  ([
    .[]
    | . as $rule
    | select(($rule.url_suffix == null) or ($url | endswith($rule.url_suffix)))
    | select(($rule.url_contains == null) or ($url | contains($rule.url_contains)))
    | select(($rule | has("source_prefix") | not) or (($rule.source_prefix // "") == (($body.source_prefix // ""))))
    | select(
        ($rule.query_contains // null) == null
        or (($body.query // "") | contains($rule.query_contains))
      )
    | $rule.response
  ][0]) // {"results": [], "count": 0}
' "$FAKE_CURL_RESPONSES")

status_code=$(jq -r --arg url "$url" --argjson body "$body" '
  ([
    .[]
    | . as $rule
    | select(($rule.url_suffix == null) or ($url | endswith($rule.url_suffix)))
    | select(($rule.url_contains == null) or ($url | contains($rule.url_contains)))
    | select(($rule | has("source_prefix") | not) or (($rule.source_prefix // "") == (($body.source_prefix // ""))))
    | select(
        ($rule.query_contains // null) == null
        or (($body.query // "") | contains($rule.query_contains))
      )
    | ($rule.status // 200)
  ][0]) // 200
' "$FAKE_CURL_RESPONSES")

printf '%s' "$response_body"
# Mimic curl's -w/--write-out: only append the status line when the caller
# actually asked for %{http_code}, same as real curl would.
case "$write_out" in
  *'%{http_code}'*)
    printf '\\n%s' "$status_code"
    ;;
esac
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


def test_codex_memory_query_reads_current_rollout_payload_for_short_followups(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Resume the BillingTelemetry rollout."}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "The next step is session attribution."}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 41,
                        "source": "codex/memories",
                        "text": "BillingTelemetry requires session-level attribution.",
                        "similarity": 0.91,
                    }
                ],
                "count": 1,
            },
        }
    ]
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "codex-short-context",
        "cwd": "/Users/example/memories",
        "prompt": "Continue",
        "transcript_path": str(transcript),
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-query.sh",
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "BillingTelemetry requires session-level attribution." in ctx
    assert any("BillingTelemetry" in call["body"]["query"] for call in calls)
    event = json.loads(metrics_log.read_text(encoding="utf-8").splitlines()[0])
    assert event["event"] == "prompt_evaluated"
    assert event["search_count"] == 5


def test_codex_memory_query_does_not_silently_skip_short_prompt_without_context(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "codex-short-no-context",
        "cwd": "/Users/example/memories",
        "prompt": "Continue",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-query.sh",
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0, result.stderr
    assert len([call for call in calls if call["url"].endswith("/search")]) == 6
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MANDATORY FIRST ACTION" in ctx
    event = json.loads(metrics_log.read_text(encoding="utf-8").splitlines()[0])
    assert event["event"] == "prompt_evaluated"
    assert event["session_id"] == "codex-short-no-context"
    assert event["search_count"] == 6
    assert event["hook_results_injected"] is False


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
    assert "Use exact source prefixes shown below" in ctx
    assert "candidate memory from codex/memories" in ctx
    assert "memory_get" in ctx
    assert "id=42" not in ctx
    assert "setup validation and production write isolation" not in ctx


def test_memory_query_active_search_trigger_covers_remember_prompts(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 77,
                        "source": "claude-code/memories",
                        "text": "Temporal eval gating requires setup validation.",
                        "similarity": 0.86,
                    }
                ],
                "count": 1,
            },
        },
    ]
    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "Do you remember how we handled temporal eval gating?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "MUST call memory_search" in ctx
    assert "candidate memory from claude-code/memories" in ctx
    assert "Temporal eval gating requires setup validation" not in ctx


def test_memory_query_logs_active_search_prompt_metrics_without_text(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
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
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "prompt": "Did we already decide how release should be gated?",
    }

    result, _, _ = _run_hook(
        QUERY_SCRIPT,
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "prompt_evaluated"
    assert event["client"] == "claude-code"
    assert event["session_id"] == "session-1"
    assert event["project"] == "memories"
    assert event["active_search_required"] is True
    assert event["candidate_count"] == 1
    assert event["hook_results_injected"] is True
    assert event["source_prefixes"] == ["codex/memories"]
    assert len(event["prompt_hash"]) == 64
    assert "release should be gated" not in json.dumps(event)
    assert "setup validation" not in json.dumps(event)


def test_memory_query_logs_metrics_write_failures_to_hook_log(tmp_path: Path) -> None:
    hook_log = tmp_path / "hook.log"
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "",
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 42,
                        "source": "claude-code/memories",
                        "text": "A private release decision.",
                        "similarity": 0.91,
                    }
                ],
                "count": 1,
            },
        },
    ]
    payload = {
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "prompt": "Do you remember the release decision?",
    }

    result, _, _ = _run_hook(
        QUERY_SCRIPT,
        tmp_path,
        payload,
        responses,
        extra_env={
            "MEMORIES_ACTIVE_SEARCH_LOG": str(tmp_path),
            "MEMORIES_LOG": str(hook_log),
        },
    )

    assert result.returncode == 0
    assert "Active-search metrics log unavailable" in hook_log.read_text(encoding="utf-8")


def test_memory_query_logs_required_prompt_metrics_even_without_candidates(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    responses = [
        {"url_suffix": "/search", "source_prefix": "", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "claude-code/memories", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "codex/memories", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "learning/memories", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "wip/memories", "response": {"results": [], "count": 0}},
    ]

    payload = {
        "session_id": "session-empty",
        "cwd": "/Users/example/memories",
        "prompt": "What did we decide about the release gate?",
    }

    result, _, _ = _run_hook(
        QUERY_SCRIPT,
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    # Prior-work prompt with zero candidates still gets the full directive
    # mandate (gated injection), just without a Retrieved Memories block.
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "MANDATORY FIRST ACTION" in ctx
    assert "MUST call memory_search" in ctx
    assert "## Retrieved Memories" not in ctx
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["active_search_required"] is True
    assert events[0]["candidate_count"] == 0
    assert events[0]["hook_results_injected"] is False
    assert events[0]["source_prefixes"] == []


FULL_MANDATE_PREAMBLE = (
    "IMPORTANT: The following memories from prior sessions are relevant to this prompt. "
    "These represent prior decisions and context that MUST be considered before responding. "
    "Do not contradict stored decisions without explicitly acknowledging the change.\n\n"
    "Active search requirement: hook-injected memories are keyword-matched starting points, "
    "not a substitute for active search.\n\n"
    "MANDATORY FIRST ACTION: if this prompt asks about prior decisions, project history, "
    "deferred work, conventions, or continuation of prior work, load the tool if needed with "
    'ToolSearch("+memory_search"), then MUST call memory_search before '
    "answering. Do not answer from injected memories alone. Do not use memory_get as a "
    "substitute for memory_search. Use exact source prefixes shown below before broad family "
    "prefixes or unscoped search.\n\n## Retrieved Memories\n"
)

CODEX_FULL_MANDATE_PREAMBLE = (
    "IMPORTANT: hook-injected memories are keyword-matched starting points, not a substitute "
    "for active search.\n\n"
    "MANDATORY FIRST ACTION: if this prompt asks about prior decisions, project history, "
    "deferred work, conventions, or continuation of prior work, you MUST call memory_search "
    "before answering. Do not answer from injected memories alone. Do not use memory_get as a "
    "substitute for memory_search. Use exact source prefixes shown below before broad family "
    "prefixes or unscoped search.\n\n## Retrieved Memories\n"
)

_CANDIDATE_RESPONSES = [
    {
        "url_suffix": "/search",
        "source_prefix": "claude-code/memories",
        "response": {
            "results": [
                {
                    "id": 7,
                    "source": "claude-code/memories",
                    "text": "Hook playbook injection is gated by candidate count and prior-work shape.",
                    "similarity": 0.9,
                }
            ],
            "count": 1,
        },
    }
]


def test_memory_query_full_mandate_wording_unchanged_when_candidates_exist(tmp_path: Path) -> None:
    """Prior-work prompt with candidates: the directive mandate stays byte-identical (no softening)."""

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "how does the deploy pipeline work with the WebhookHandler?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, _CANDIDATE_RESPONSES)

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith(FULL_MANDATE_PREAMBLE)


def test_memory_query_memories_without_mandate_for_non_prior_work_prompt(tmp_path: Path) -> None:
    """Non-prior-work prompt with candidates: memories block + short preamble, no mandate."""

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "explain the deploy pipeline and the WebhookHandler architecture",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, _CANDIDATE_RESPONSES)

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("Memories from prior sessions matched this prompt")
    assert "## Retrieved Memories" in ctx
    assert "MANDATORY FIRST ACTION" not in ctx
    assert "MUST" not in ctx


def test_memory_query_minimal_reminder_for_self_contained_prompt(tmp_path: Path) -> None:
    """Self-contained prompts with no candidates get at most a 1-2 line reminder."""

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "Translate the phrase good morning into French please.",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses=[])

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "memory_search" in ctx
    assert "MANDATORY FIRST ACTION" not in ctx
    assert "## Retrieved Memories" not in ctx
    assert "IMPORTANT" not in ctx
    assert len(ctx) < 400, f"minimal reminder too long ({len(ctx)} chars): {ctx!r}"
    assert ctx.count("\n") <= 1, f"minimal reminder must be 1-2 lines: {ctx!r}"


def test_memory_query_full_mandate_without_candidates_for_prior_work_prompt(tmp_path: Path) -> None:
    """Prior-work shapes outside the active-search regex still gate the full mandate."""

    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "Weren't we going to migrate the embedder to the new model?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses=[])

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MANDATORY FIRST ACTION" in ctx
    assert "MUST call memory_search" in ctx
    assert "ToolSearch" in ctx
    assert "## Retrieved Memories" not in ctx
    assert "memory_get" in ctx


def test_codex_memory_query_full_mandate_wording_unchanged_when_candidates_exist(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 8,
                        "source": "codex/memories",
                        "text": "Codex hook variant uses native hooks.json wiring.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        }
    ]
    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "how does the deploy pipeline work with the WebhookHandler?",
    }

    result, _, _ = _run_hook(CODEX_HOOKS_DIR / "memory-query.sh", tmp_path, payload, responses)

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith(CODEX_FULL_MANDATE_PREAMBLE)
    assert "ToolSearch" not in ctx

    memories_payload = {
        "cwd": "/Users/example/memories",
        "prompt": "explain the deploy pipeline and the WebhookHandler architecture",
    }
    result2, _, _ = _run_hook(CODEX_HOOKS_DIR / "memory-query.sh", tmp_path, memories_payload, responses)
    assert result2.returncode == 0
    ctx2 = json.loads(result2.stdout)["hookSpecificOutput"]["additionalContext"]
    assert ctx2.startswith("Memories from prior sessions matched this prompt")
    assert "## Retrieved Memories" in ctx2
    assert "MANDATORY FIRST ACTION" not in ctx2
    assert "ToolSearch" not in ctx2


def test_codex_memory_query_minimal_reminder_omits_toolsearch(tmp_path: Path) -> None:
    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "Translate the phrase good morning into French please.",
    }

    result, _, _ = _run_hook(CODEX_HOOKS_DIR / "memory-query.sh", tmp_path, payload, responses=[])

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "memory_search" in ctx
    assert "ToolSearch" not in ctx
    assert "MANDATORY FIRST ACTION" not in ctx
    assert "## Retrieved Memories" not in ctx
    assert ctx.count("\n") <= 1


def test_codex_memory_query_full_mandate_without_candidates_omits_toolsearch(tmp_path: Path) -> None:
    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "What did we decide about the release gate?",
    }

    result, _, _ = _run_hook(CODEX_HOOKS_DIR / "memory-query.sh", tmp_path, payload, responses=[])

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MANDATORY FIRST ACTION" in ctx
    assert "MUST call memory_search" in ctx
    assert "ToolSearch" not in ctx
    assert "## Retrieved Memories" not in ctx


def test_memory_observe_logs_memory_search_tool_metrics_without_query_text(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "tool_name": "mcp__memories__memory_search",
        "tool_input": {
            "query": "private release gate query",
            "source_prefix": "codex/memories/feature",
        },
    }

    result, _, _ = _run_hook(
        OBSERVE_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "tool_call"
    assert event["client"] == "claude-code"
    assert event["session_id"] == "session-1"
    assert event["project"] == "memories"
    assert event["tool_name"] == "mcp__memories__memory_search"
    assert event["source_prefix"] == "codex/memories/feature"
    assert event["source_prefix_quality"] == "exact_project"
    assert "private release gate query" not in json.dumps(event)


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
    assert "IMPORTANT: Search memories BEFORE responding" in ctx
    assert "ACTIVE SEARCH ACTION" in ctx
    assert "self-contained prompts" in ctx
    assert "claude-code/memories" in ctx
    assert "codex/memories" in ctx
    assert "learning/memories" in ctx
    assert "wip/memories" in ctx
    assert "candidate memory id=1" in ctx
    assert "Claude read hooks should search project-scoped memories" not in ctx
    assert "Deferred: tighten Claude session-start recall" not in ctx

    memory_file = home_dir / ".claude" / "projects" / "-Users-example-memories" / "memory" / "MEMORY.md"
    assert memory_file.exists()
    memory_text = memory_file.read_text()
    assert "## Synced from Memories" in memory_text
    assert "candidate memory id=1" in memory_text
    assert "Claude read hooks should search project-scoped memories" not in memory_text
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

    assert "IMPORTANT: Search memories BEFORE responding" in ctx
    assert "ACTIVE SEARCH ACTION" in ctx
    assert "self-contained prompts" in ctx
    assert "ToolSearch" in ctx
    assert "You MUST call memory_search" in ctx
    assert "Use exact source prefixes from candidate pointers first" in ctx
    assert "family-wide" in ctx
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


def test_memory_rehydrate_syncs_pointers_not_full_memory_text(tmp_path: Path) -> None:
    memory_file = (
        tmp_path
        / "home"
        / ".claude"
        / "projects"
        / "Users-example-memories"
        / "memory"
        / "MEMORY.md"
    )
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text(
        "# Manual note\n\n<!-- SYNCED-FROM-MEMORIES-MCP -->\n## Synced from Memories\n- stale\n",
        encoding="utf-8",
    )
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 9,
                        "source": "claude-code/memories",
                        "text": "Compaction should not rehydrate full memory text into auto-memory.",
                        "similarity": 0.91,
                    }
                ],
                "count": 1,
            },
        }
    ]

    payload = {"cwd": "/Users/example/memories", "compact_summary": "memory rehydrate behavior"}
    result, _, _ = _run_hook(REHYDRATE_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    updated = memory_file.read_text(encoding="utf-8")
    assert "# Manual note" in updated
    assert "candidate memory id=9" in updated
    assert "Compaction should not rehydrate full memory text" not in updated


def test_memory_rehydrate_merges_batches_without_similarity_field(tmp_path: Path) -> None:
    """Hybrid search returns rrf_score, not similarity. Merging a second batch
    sorted with `-.similarity // -.rrf_score` throws, because jq negates
    .similarity before the alternative is considered — so a null similarity is
    an error, not a fallback. Two prefixes are required: the merge only runs
    once RESULTS is already non-empty."""
    memory_file = (
        tmp_path / "home" / ".claude" / "projects" / "Users-example-memories" / "memory" / "MEMORY.md"
    )
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("# Manual note\n", encoding="utf-8")

    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {"results": [{"id": 11, "source": "claude-code/memories", "rrf_score": 0.4}], "count": 1},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "learning/memories",
            "response": {"results": [{"id": 22, "source": "learning/memories", "rrf_score": 0.9}], "count": 1},
        },
    ]

    payload = {"cwd": "/Users/example/memories", "compact_summary": "rehydrate merge"}
    result, _, _ = _run_hook(
        REHYDRATE_SCRIPT,
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_SOURCE_PREFIXES": "claude-code/memories,learning/memories"},
    )

    assert result.returncode == 0, result.stderr
    assert "cannot be negated" not in result.stderr
    updated = memory_file.read_text(encoding="utf-8")
    # Both batches survive the merge, ranked by the score that is present.
    assert "candidate memory id=22" in updated
    assert "candidate memory id=11" in updated


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
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=1" in ctx
    assert "Codex sessions should recall project decisions" not in ctx
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


def test_codex_memory_recall_logs_session_metrics_and_attributes_searches(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 77,
                        "source": "codex/memories",
                        "text": "Session recall telemetry is enabled.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        }
    ]
    payload = {
        "session_id": "codex-session-recall",
        "cwd": "/Users/example/memories",
        "source": "resume",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0, result.stderr
    search_calls = [call for call in calls if call["url"].endswith("/search")]
    assert len(search_calls) == 5
    assert {call["body"]["source"] for call in search_calls} == {"hook:codex:memory-recall"}
    event = json.loads(metrics_log.read_text(encoding="utf-8").splitlines()[0])
    assert event == {
        "ts": event["ts"],
        "event": "session_recall",
        "client": "codex",
        "session_id": "codex-session-recall",
        "project": "memories",
        "session_source": "resume",
        "candidate_count": 1,
        "candidate_ids": [77],
        "source_prefixes": ["codex/memories"],
        "search_count": 5,
    }


def test_codex_precompact_submits_transcript_extraction(tmp_path: Path) -> None:
    transcript = tmp_path / "precompact.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "Decision: keep the Codex hook payload snake_case."}}),
                json.dumps({"type": "assistant", "message": {"content": "The lifecycle adapter will preserve Codex source semantics."}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "session_id": "codex-precompact",
        "transcript_path": str(transcript),
        "cwd": "/Users/example/memories",
        "hook_event_name": "PreCompact",
        "trigger": "auto",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-flush.sh",
        tmp_path,
        payload,
        responses=[],
    )

    assert result.returncode == 0, result.stderr
    extract_calls = [call for call in calls if str(call["url"]).endswith("/memory/extract")]
    assert len(extract_calls) == 1
    assert extract_calls[0]["body"]["context"] == "pre_compact"
    assert extract_calls[0]["body"]["source"] == "codex/memories"
    assert "snake_case" in extract_calls[0]["body"]["messages"]


def test_codex_postcompact_is_schema_valid_silent_hook(tmp_path: Path) -> None:
    payload = {
        "session_id": "codex-postcompact",
        "cwd": "/Users/example/memories",
        "hook_event_name": "PostCompact",
        "trigger": "Codex compaction preserved the project source prefix.",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-rehydrate.sh",
        tmp_path,
        payload,
        responses=[],
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert set(output) <= {"continue", "stopReason", "suppressOutput", "systemMessage"}
    assert output == {"suppressOutput": True}
    assert calls == []


def test_codex_session_start_compact_remains_the_recall_injection_surface(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 951,
                        "source": "codex/memories",
                        "text": "Compact sessions rehydrate through SessionStart recall.",
                        "similarity": 0.95,
                    }
                ],
                "count": 1,
            },
        }
    ]
    payload = {
        "session_id": "codex-session-compact",
        "cwd": "/Users/example/memories",
        "hook_event_name": "SessionStart",
        "source": "compact",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        payload,
        responses=responses,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "Compact sessions rehydrate through SessionStart recall." not in output["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=951" in output["hookSpecificOutput"]["additionalContext"]
    assert any(call["body"].get("source_prefix") == "codex/memories" for call in calls if call["body"] is not None)


def test_codex_subagent_start_returns_project_scoped_additional_context(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "response": {
                "results": [
                    {
                        "id": 952,
                        "source": "codex/memories",
                        "text": "Subagents inherit the Codex project memory context.",
                        "similarity": 0.94,
                    }
                ],
                "count": 1,
            },
        }
    ]
    payload = {
        "session_id": "codex-subagent-start",
        "cwd": "/Users/example/memories",
        "hook_event_name": "SubagentStart",
        "agent_id": "agent-1",
        "agent_type": "explorer",
        "trigger": "spawn",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-subagent-recall.sh",
        tmp_path,
        payload,
        responses=responses,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert "Subagents inherit the Codex project memory context." in output["hookSpecificOutput"]["additionalContext"]
    search_calls = [call for call in calls if str(call["url"]).endswith("/search")]
    assert any(call["body"].get("source_prefix") == "codex/memories" for call in search_calls)


def test_codex_subagent_stop_prefers_child_transcript_and_last_message(tmp_path: Path) -> None:
    parent_transcript = tmp_path / "parent.jsonl"
    parent_transcript.write_text(
        json.dumps({"type": "assistant", "message": {"content": "Parent-only content must not be extracted."}}) + "\n",
        encoding="utf-8",
    )
    child_transcript = tmp_path / "subagent.jsonl"
    child_transcript.write_text(
        json.dumps({"type": "assistant", "message": {"content": "Subagent decision: retain the hook timeout."}}) + "\n",
        encoding="utf-8",
    )
    payload = {
        "session_id": "codex-subagent-stop",
        "transcript_path": str(parent_transcript),
        "agent_transcript_path": str(child_transcript),
        "cwd": "/Users/example/memories",
        "hook_event_name": "SubagentStop",
        "agent_id": "agent-1",
        "agent_type": "explorer",
        "last_assistant_message": "Subagent final message: the timeout remains bounded.",
    }

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-subagent-capture.sh",
        tmp_path,
        payload,
        responses=[],
    )

    assert result.returncode == 0, result.stderr
    extract_calls = [call for call in calls if str(call["url"]).endswith("/memory/extract")]
    assert len(extract_calls) == 1
    assert extract_calls[0]["body"]["context"] == "subagent_stop"
    assert "hook timeout" in extract_calls[0]["body"]["messages"]
    assert "final message" in extract_calls[0]["body"]["messages"]
    assert "Parent-only content" not in extract_calls[0]["body"]["messages"]


def test_codex_session_end_enqueues_once_without_polling_across_backends(tmp_path: Path) -> None:
    transcript = tmp_path / "session-end.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant", "message": {"content": "Session decision: preserve the queued extraction."}}) + "\n",
        encoding="utf-8",
    )
    payload = {
        "session_id": "codex-session-end",
        "transcript_path": str(transcript),
        "cwd": "/Users/example/memories",
        "hook_event_name": "SessionEnd",
        "trigger": "exit",
        "last_assistant_message": "Session final message: extraction is queued.",
    }

    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  first:\n"
        "    url: https://first-extract.example\n"
        "    api_key: test-key\n"
        "    scenario: dev\n"
        "  second:\n"
        "    url: https://second-extract.example\n"
        "    api_key: test-key\n"
        "    scenario: dev\n"
    )
    started = time.monotonic()
    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-commit.sh",
        tmp_path,
        payload,
        responses=[
            {
                "url_suffix": "/memory/extract",
                "url_contains": "first-extract.example",
                "delay": 2,
                "response": {"job_id": "queued-first"},
            },
            {
                "url_suffix": "/memory/extract",
                "url_contains": "second-extract.example",
                "delay": 2,
                "response": {"job_id": "queued-second"},
            },
        ],
        extra_env={"MEMORIES_URL": "", "MEMORIES_BACKENDS_FILE": str(backends_file)},
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 3
    extract_calls = [call for call in calls if str(call["url"]).endswith("/memory/extract")]
    assert len(extract_calls) == 1
    assert "first-extract.example" in extract_calls[0]["url"]
    assert extract_calls[0]["body"]["context"] == "session_end"
    assert all("status" not in str(call["url"]) for call in calls)
    assert all("poll" not in str(call["url"]) for call in calls)


def test_memory_extract_drops_system_reminder_content_items(tmp_path: Path) -> None:
    """Hook-injected <system-reminder> items (recalled memories) must not be
    sent to the extraction endpoint — that re-ingests them every session."""
    transcript = tmp_path / "transcript.jsonl"
    injected = (
        "<system-reminder>\n## Retrieved Memories\n"
        "- [claude-code/memories] We chose Qdrant over FAISS for payload filtering\n"
        "</system-reminder>"
    )
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {"type": "text", "text": injected},
                                {"type": "text", "text": "please wire the novelty gate into extraction"},
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "Decision: gate ADDs behind EXTRACT_NOVELTY_GATE."}
                            ]
                        },
                    }
                ),
            ]
        )
    )

    payload = {"cwd": "/Users/example/memories", "transcript_path": str(transcript)}
    result, calls, _ = _run_hook(EXTRACT_SCRIPT, tmp_path, payload, responses=[])

    assert result.returncode == 0
    assert calls
    body = calls[0]["body"]
    assert "Qdrant over FAISS" not in body["messages"]
    assert "system-reminder" not in body["messages"]
    assert "novelty gate" in body["messages"]
    assert "EXTRACT_NOVELTY_GATE" in body["messages"]


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


def test_codex_memory_hooks_unconfigured_url_is_silent_noop(tmp_path: Path) -> None:
    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=[],
        extra_env={"MEMORIES_URL": ""},
    )
    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_codex_memory_recall_payload_cwd_backend_config_activates_without_project_env(tmp_path: Path) -> None:
    project_dir = tmp_path / "payload-recall"
    (project_dir / ".memories").mkdir(parents=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  payload_recall:\n"
        "    url: https://payload-recall.example\n"
        "    api_key: test-key\n"
    )
    responses = [
        {
            "url_contains": "payload-recall.example",
            "url_suffix": "/search",
            "response": {
                "results": [
                    {
                        "id": 940,
                        "source": "codex/payload-recall",
                        "text": "Payload-local recall configuration activates the hook.",
                        "similarity": 0.95,
                    }
                ],
                "count": 1,
            },
        }
    ]

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "CODEX_PROJECT_DIR": "",
            "CLAUDE_PROJECT_DIR": "",
        },
    )

    assert result.returncode == 0, result.stderr
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert search_calls
    assert all("payload-recall.example" in str(c["url"]) for c in search_calls)


def test_codex_memory_query_payload_cwd_backend_config_activates_without_project_env(tmp_path: Path) -> None:
    project_dir = tmp_path / "payload-query"
    (project_dir / ".memories").mkdir(parents=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  payload_query:\n"
        "    url: https://payload-query.example\n"
        "    api_key: test-key\n"
    )
    responses = [
        {
            "url_contains": "payload-query.example",
            "url_suffix": "/search",
            "response": {
                "results": [
                    {
                        "id": 941,
                        "source": "codex/payload-query",
                        "text": "Payload-local query configuration activates the hook.",
                        "similarity": 0.94,
                    }
                ],
                "count": 1,
            },
        }
    ]

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-query.sh",
        tmp_path,
        {"cwd": str(project_dir), "prompt": "hello"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "CODEX_PROJECT_DIR": "",
            "CLAUDE_PROJECT_DIR": "",
        },
    )

    assert result.returncode == 0, result.stderr
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert search_calls
    assert all("payload-query.example" in str(c["url"]) for c in search_calls)


def test_codex_memory_hooks_enabled_false_wins_over_url(tmp_path: Path) -> None:
    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=[],
        extra_env={"MEMORIES_ENABLED": "false"},
    )
    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_codex_memory_hooks_explicit_backends_file_wins_over_project_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  root:\n"
        "    url: https://project-root.example\n"
        "    api_key: test-key\n"
    )
    explicit_file = tmp_path / "explicit-backends.yaml"
    explicit_file.write_text(
        "backends:\n"
        "  explicit:\n"
        "    url: https://explicit-override.example\n"
        "    api_key: test-key\n"
    )

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=[],
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(explicit_file),
            "CODEX_PROJECT_DIR": str(project_dir),
        },
    )

    assert result.returncode == 0
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert search_calls
    urls = [str(c["url"]) for c in search_calls]
    assert all(url.startswith("https://explicit-override.example") for url in urls)
    assert not any("project-root.example" in url for url in urls)


def test_codex_memory_hooks_routed_health_excludes_non_routed_dead_backend(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  down_first:\n"
        "    url: https://down-first.example\n"
        "    api_key: test-key\n"
        "  reachable:\n"
        "    url: https://reachable.example\n"
        "    api_key: test-key\n"
        "routing:\n"
        "  search: [reachable]\n"
    )
    responses = [
        {"url_contains": "localhost:8900", "fail": True},
        {"url_contains": "down-first.example", "fail": True},
        {
            "url_contains": "reachable.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "response": {
                "results": [
                    {
                        "id": 701,
                        "source": "codex/project",
                        "text": "Reached the routed Codex backend.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        },
    ]

    result, calls, home_dir = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
        },
    )

    assert result.returncode == 0, result.stderr
    down_calls = [c for c in calls if "down-first.example" in str(c["url"])]
    localhost_calls = [c for c in calls if "localhost:8900" in str(c["url"])]
    reachable_calls = [c for c in calls if "reachable.example" in str(c["url"])]
    assert down_calls == []
    assert localhost_calls == []
    assert reachable_calls
    assert any(str(c["url"]).endswith("/search") for c in reachable_calls)
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=701" in ctx
    assert "codex/project" in ctx
    assert "down_first" not in ctx
    assert not (home_dir / ".config" / "memories" / "backend-down").exists()


def test_codex_memory_hooks_preexisting_open_breaker_does_not_crash(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    breaker_dir = home_dir / ".config" / "memories"
    breaker_dir.mkdir(parents=True, exist_ok=True)
    (breaker_dir / "backend-down").write_text(str(int(time.time())))

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=[],
    )

    assert result.returncode == 0, result.stderr
    assert "unbound variable" not in result.stderr
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "not reachable" in ctx or "Memory Playbook" in ctx
    assert calls == []


def test_codex_memory_hooks_per_backend_breaker_isolation(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  failing:\n"
        "    url: https://failing.example\n"
        "    api_key: test-key\n"
        "  healthy:\n"
        "    url: https://healthy.example\n"
        "    api_key: test-key\n"
    )
    responses = [
        {"url_contains": "failing.example", "fail": True},
        {
            "url_contains": "healthy.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "response": {
                "results": [
                    {
                        "id": 702,
                        "source": "codex/project",
                        "text": "Healthy backend remains available.",
                        "similarity": 0.91,
                    }
                ],
                "count": 1,
            },
        },
    ]

    result, calls, home_dir = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
        },
    )

    assert result.returncode == 0, result.stderr
    assert any("healthy.example" in str(c["url"]) for c in calls)
    assert not any(str(c["url"]).endswith("/search") and "failing.example" in str(c["url"]) for c in calls)
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=702" in ctx
    named_breakers = list((home_dir / ".config" / "memories").glob("backend-down.*"))
    assert len(named_breakers) == 1
    assert named_breakers[0].name != "backend-down.failing"
    assert not (home_dir / ".config" / "memories" / "backend-down").exists()


def test_codex_memory_hooks_tiny_budget_exhausted_from_start_exits_cleanly(tmp_path: Path) -> None:
    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=[],
        extra_env={"MEMORIES_HOOK_BUDGET_MS": "1"},
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Memory Playbook" in ctx
    assert "budget exhausted" in ctx.lower() or "not reachable" in ctx.lower()
    assert calls == []


def test_codex_memory_recall_401_shows_credential_warning_not_reachability(tmp_path: Path) -> None:
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": prefix,
            "status": 401,
            "response": {"results": [], "count": 0},
        }
        for prefix in (
            "codex/memories",
            "claude-code/memories",
            "learning/memories",
            "wip/memories",
        )
    ]

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": "/Users/example/memories", "source": "startup"},
        responses=responses,
    )

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "rejected the API key" in ctx
    assert "MEMORIES_API_KEY" in ctx
    assert "Check that the service is running" not in ctx
    assert calls


def test_codex_memory_recall_multi_backend_401_keeps_candidate_and_identity(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  healthy_first:\n"
        "    url: https://healthy-first.example\n"
        "    api_key: test-key\n"
        "  auth_second:\n"
        "    url: https://auth-second.example\n"
        "    api_key: rejected-key\n"
    )
    responses = [
        {
            "url_contains": "healthy-first.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "response": {
                "results": [
                    {
                        "id": 901,
                        "source": "codex/project",
                        "text": "Healthy candidate survives an auth failure on a peer.",
                        "similarity": 0.95,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_contains": "auth-second.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "status": 401,
            "response": {"results": [], "count": 0},
        },
    ]

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
        },
    )

    assert result.returncode == 0, result.stderr
    assert any("healthy-first.example" in str(call["url"]) for call in calls)
    assert any("auth-second.example" in str(call["url"]) for call in calls)
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=901" in ctx
    assert "auth_second" in ctx
    assert "https://auth-second.example" in ctx
    assert "https://healthy-first.example" not in ctx
    assert "memory recall and extraction are disabled" not in ctx.lower()
    assert "MEMORIES_API_KEY" not in ctx


def test_codex_memory_recall_search_health_warning_does_not_claim_extraction_down(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  search_dead:\n"
        "    url: https://search-dead.example\n"
        "    api_key: test-key\n"
    )
    responses = [{"url_contains": "search-dead.example", "fail": True}]

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
        },
    )

    assert result.returncode == 0, result.stderr
    assert any("search-dead.example/health" in str(call["url"]) for call in calls)
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "recall/search is unavailable this session" in ctx.lower()
    assert "extraction" not in ctx.lower()


def test_codex_memory_recall_fanout_backend_names_are_collision_free(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  foo-bar:\n"
        "    url: https://foo-bar.example\n"
        "    api_key: test-key\n"
        "  foo_bar:\n"
        "    url: https://foo-bar-underscore.example\n"
        "    api_key: test-key\n"
    )
    responses = [
        {
            "url_contains": "foo-bar.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "response": {
                "results": [
                    {
                        "id": 902,
                        "source": "codex/project",
                        "text": "Candidate from the hyphenated backend.",
                        "similarity": 0.94,
                    }
                ],
                "count": 1,
            },
        },
        {
            "url_contains": "foo-bar-underscore.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "response": {
                "results": [
                    {
                        "id": 903,
                        "source": "codex/project",
                        "text": "Candidate from the underscored backend.",
                        "similarity": 0.93,
                    }
                ],
                "count": 1,
            },
        },
    ]

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
        },
    )

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=902" in ctx
    assert "candidate memory id=903" in ctx


def test_codex_memory_query_named_backend_401_guidance_uses_backend_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  named_auth:\n"
        "    url: https://named-auth.example\n"
        "    api_key: ${NAMED_AUTH_KEY}\n"
    )
    responses = [
        {
            "url_contains": "named-auth.example",
            "url_suffix": "/search",
            "source_prefix": "codex/project",
            "status": 401,
            "response": {"results": [], "count": 0},
        }
    ]

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-query.sh",
        tmp_path,
        {"cwd": str(project_dir), "prompt": "hello"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
            "NAMED_AUTH_KEY": "rejected-key",
        },
    )

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "named_auth" in ctx
    assert "https://named-auth.example" in ctx
    assert "configured" in ctx.lower() or "environment variable" in ctx.lower()
    assert "MEMORIES_API_KEY" not in ctx


def test_codex_memory_query_search_reachability_is_independent_of_extract_routing(tmp_path: Path) -> None:
    project_dir = tmp_path / "split-routing"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  search_dead:\n"
        "    url: https://search-dead.example\n"
        "    api_key: test-key\n"
        "  extract_healthy:\n"
        "    url: https://extract-healthy.example\n"
        "    api_key: test-key\n"
        "routing:\n"
        "  search: [search_dead]\n"
        "  extract: [extract_healthy]\n"
    )
    responses = [
        {"url_contains": "search-dead.example", "url_suffix": "/search", "fail": True},
    ]

    result, calls, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-query.sh",
        tmp_path,
        {"cwd": str(project_dir), "prompt": "hello"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
        },
    )

    assert result.returncode == 0, result.stderr
    assert any("search-dead.example/search" in str(call["url"]) for call in calls)
    assert not any("extract-healthy.example" in str(call["url"]) for call in calls)
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "recall/search is unavailable" in ctx.lower()
    assert "capture" not in ctx.lower()
    assert "extraction" not in ctx.lower()


def test_codex_memory_recall_configured_default_401_guidance_uses_custom_env(tmp_path: Path) -> None:
    project_dir = tmp_path / "configured-default-recall"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  default:\n"
        "    url: https://configured-default-recall.example\n"
        "    api_key: ${CUSTOM_DEFAULT_KEY}\n"
    )
    responses = [
        {
            "url_contains": "configured-default-recall.example",
            "url_suffix": "/search",
            "status": 401,
            "response": {"results": [], "count": 0},
        }
    ]

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-recall.sh",
        tmp_path,
        {"cwd": str(project_dir), "source": "startup"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
            "CUSTOM_DEFAULT_KEY": "rejected-key",
            "MEMORIES_API_KEY": "fallback-key-must-not-be-recommended",
        },
    )

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "default" in ctx
    assert "https://configured-default-recall.example" in ctx
    assert "configured api_key" in ctx
    assert "CUSTOM_DEFAULT_KEY" in ctx or "environment variable" in ctx.lower()
    assert "MEMORIES_API_KEY" not in ctx


def test_codex_memory_query_configured_default_401_guidance_uses_custom_env(tmp_path: Path) -> None:
    project_dir = tmp_path / "configured-default-query"
    project_dir.mkdir()
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  default:\n"
        "    url: https://configured-default-query.example\n"
        "    api_key: ${CUSTOM_DEFAULT_KEY}\n"
    )
    responses = [
        {
            "url_contains": "configured-default-query.example",
            "url_suffix": "/search",
            "status": 401,
            "response": {"results": [], "count": 0},
        }
    ]

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-query.sh",
        tmp_path,
        {"cwd": str(project_dir), "prompt": "hello"},
        responses=responses,
        extra_env={
            "MEMORIES_URL": "",
            "MEMORIES_BACKENDS_FILE": str(backends_file),
            "CUSTOM_DEFAULT_KEY": "rejected-key",
            "MEMORIES_API_KEY": "fallback-key-must-not-be-recommended",
        },
    )

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "default" in ctx
    assert "https://configured-default-query.example" in ctx
    assert "configured api_key" in ctx
    assert "CUSTOM_DEFAULT_KEY" in ctx or "environment variable" in ctx.lower()
    assert "MEMORIES_API_KEY" not in ctx


def test_memory_hooks_unconfigured_url_is_silent_noop(tmp_path: Path) -> None:
    """A repo that ships .claude/settings.json but no MEMORIES_URL must be a true
    no-op for contributors who never opted in: exit 0, no stdout, no curl call."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": ""},
    )

    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_memory_hooks_enabled_false_silences_configured_url(tmp_path: Path) -> None:
    """MEMORIES_ENABLED=false wins over a configured MEMORIES_URL — explicit opt-out."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ENABLED": "false"},
    )

    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_memory_hooks_enabled_true_runs_without_url(tmp_path: Path) -> None:
    """MEMORIES_ENABLED=true forces the hook to run even with no MEMORIES_URL set —
    explicit opt-in, falls back to the localhost default and calls out."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ENABLED": "true", "MEMORIES_URL": ""},
    )

    assert result.returncode == 0
    assert len(calls) > 0


def test_memory_hooks_disabled_wins_over_enabled_true(tmp_path: Path) -> None:
    """MEMORIES_DISABLED=1 still wins even when fully configured and MEMORIES_ENABLED=true."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_DISABLED": "1", "MEMORIES_ENABLED": "true"},
    )

    assert result.returncode == 0
    assert calls == []
    assert result.stdout.strip() == ""


def test_memory_hooks_backends_file_only_runs_without_url(tmp_path: Path) -> None:
    """REGRESSION: multi-backend installs configure via MEMORIES_BACKENDS_FILE
    instead of MEMORIES_URL. The activation gate must treat that as configured
    and run the hook, not silently no-op it."""
    backends_file = tmp_path / "backends.yaml"
    backends_file.write_text(
        "backends:\n"
        "  primary:\n"
        "    url: http://127.0.0.1:9999\n"
        "    api_key: test-key\n"
    )

    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}
    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": "", "MEMORIES_BACKENDS_FILE": str(backends_file)},
    )

    assert result.returncode == 0
    assert len(calls) > 0, "MEMORIES_BACKENDS_FILE-only install must run, not no-op"


def test_memory_hooks_global_backends_yaml_only_runs_without_url(tmp_path: Path) -> None:
    """REGRESSION: same as above but via the default global config location
    (~/.config/memories/backends.yaml), with no MEMORIES_BACKENDS_FILE set."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    # _run_hook creates HOME before invoking the script; write the global
    # config there ourselves since extra_env can't create files.
    home_dir = tmp_path / "home"
    (home_dir / ".config" / "memories").mkdir(parents=True, exist_ok=True)
    (home_dir / ".config" / "memories" / "backends.yaml").write_text(
        "backends:\n"
        "  primary:\n"
        "    url: http://127.0.0.1:9999\n"
        "    api_key: test-key\n"
    )

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": ""},
    )

    assert result.returncode == 0
    assert len(calls) > 0, "global backends.yaml-only install must run, not no-op"


def test_memory_hooks_project_backends_yaml_only_runs_without_url(tmp_path: Path) -> None:
    """REGRESSION: same as above but via the per-project .memories/backends.yaml,
    discovered through CLAUDE_PROJECT_DIR (the variable Claude Code exports to
    every hook process at spawn time, before the hook has parsed its stdin
    payload's .cwd field)."""
    project_dir = tmp_path / "project"
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  primary:\n"
        "    url: http://127.0.0.1:9999\n"
        "    api_key: test-key\n"
    )

    payload = {"cwd": str(project_dir), "prompt": "hello"}
    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": "", "CLAUDE_PROJECT_DIR": str(project_dir)},
    )

    assert result.returncode == 0
    assert len(calls) > 0, "per-project backends.yaml-only install must run, not no-op"


def test_memory_hooks_unconfigured_creates_no_log_file(tmp_path: Path) -> None:
    """The auto-detected-inactive path (no MEMORIES_ENABLED, no backend config)
    must be a genuine no-op: no ~/.config/memories/hook.log, not even the
    directory, must be created. Logging here would grow hook.log forever for
    someone who never opted in, since it fires on every session/prompt/tool
    event and the file is never rotated (rotation only runs on the configured
    path, past the exit)."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    result, calls, home_dir = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": ""},
    )

    assert result.returncode == 0
    assert calls == []
    assert not (home_dir / ".config" / "memories").exists(), (
        "auto-detected-inactive hooks must not create ~/.config/memories at all"
    )


def test_memory_hooks_gate_and_loader_agree_on_project_subdirectory(tmp_path: Path) -> None:
    """REGRESSION (PR #85 review, P1): the gate resolved CLAUDE_PROJECT_DIR
    while _load_backends resolved only the payload's cwd. Launched from (or
    moved into) a project SUBDIRECTORY, the gate found the project-root
    backends.yaml and activated the hook, but the loader missed that same
    file (it only ever looked at cwd) and silently fell back to an empty
    MEMORIES_URL — querying http://localhost:8900 instead of the configured
    backend. That's worse than the no-op being fixed: the hook runs and
    silently talks to the wrong place. Both must now resolve through the
    same _resolve_backends_file, so every request goes to the configured
    backend and none go to localhost."""
    project_dir = tmp_path / "project"
    nested_dir = project_dir / "nested"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  primary:\n"
        "    url: https://configured.example\n"
        "    api_key: test-key\n"
    )

    payload = {"cwd": str(nested_dir), "prompt": "hello"}
    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": "", "CLAUDE_PROJECT_DIR": str(project_dir)},
    )

    assert result.returncode == 0
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert len(search_calls) > 0, "the hook must actually run (gate must activate)"
    urls = [c["url"] for c in search_calls]
    assert all(url.startswith("https://configured.example") for url in urls), (
        f"every /search request must go to the configured backend, got: {urls}"
    )
    assert not any("localhost:8900" in url for url in urls), (
        f"no /search request may silently fall back to localhost, got: {urls}"
    )


def test_memory_hooks_health_check_and_warnings_target_resolved_backend(tmp_path: Path) -> None:
    """REGRESSION (PR #85 review, follow-up P1): fixing the gate/loader
    mismatch above wasn't sufficient by itself. _health_check still probed
    the bare MEMORIES_URL default (localhost:8900) instead of the resolved
    backend, so a backends.yaml-only install got a false "not reachable"
    warning naming a backend it never configured — and could trip the
    shared circuit breaker on that bogus failure, silently skipping the
    real (reachable) backend's searches too. This is an end-to-end style
    check: it asserts on the resolved TARGET (every recorded URL, and the
    session-start warning text), not just "some curl happened" — the prior
    regression test would not have caught this, since the hook DID run and
    DID hit the right backend for /search once the loader fix landed; only
    the separate /health probe (and any text naming a host) still pointed
    at localhost."""
    project_dir = tmp_path / "project"
    nested_dir = project_dir / "nested"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  primary:\n"
        "    url: https://configured.example\n"
        "    api_key: test-key\n"
    )

    payload = {"cwd": str(nested_dir), "prompt": "hello"}
    result, calls, home_dir = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": "", "CLAUDE_PROJECT_DIR": str(project_dir)},
    )

    assert result.returncode == 0

    health_calls = [c for c in calls if str(c["url"]).endswith("/health")]
    assert len(health_calls) > 0, "expected at least one /health probe"
    health_urls = [c["url"] for c in health_calls]
    assert all(url.startswith("https://configured.example") for url in health_urls), (
        f"the health probe must target the resolved backend, not the localhost "
        f"default, got: {health_urls}"
    )
    assert not any("localhost:8900" in url for url in health_urls), (
        f"the health probe must never silently fall back to localhost, got: {health_urls}"
    )

    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert len(search_calls) > 0
    assert all(c["url"].startswith("https://configured.example") for c in search_calls)

    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "localhost:8900" not in ctx, (
        f"no session-start warning may name a backend the user never configured, got: {ctx}"
    )

    # Per the fake curl mock (always "succeeds" regardless of target), a
    # correctly-targeted probe must not trip the shared circuit breaker.
    assert not (home_dir / ".config" / "memories" / "backend-down").exists()


def test_memory_hooks_partial_backend_failure_does_not_zero_out_healthy_routed_backend(
    tmp_path: Path,
) -> None:
    """REGRESSION (PR #85 review, 4th pass — the bug CLASS, not another
    symptom patch). Two backends: down_first (declared FIRST, on a dead
    host) and reachable (declared second, alive), with
    routing.search: [reachable] explicitly excluding down_first from search.

    The always-succeeding fake curl mock used by every prior regression test
    in this file COULD NOT have caught this: it never fails, so nothing ever
    tripped the breaker in those tests regardless of which URL was probed.
    This is why the mock was extended with per-host "fail" rules — the
    reviewer's exact point.

    Before this fix: _resolve_primary_backend_url took .[0] of
    _load_backends' RAW declaration order (down_first — routing.search had
    excluded it, but declaration order doesn't know that), _health_check
    probed THAT, it failed, tripped the single shared breaker, and
    _search_memories_multi's very own breaker-open check then skipped its
    already-fault-tolerant fan-out entirely — reachable was NEVER contacted,
    despite being alive and the only backend routing.search actually wanted.

    After this fix: health considers the ROUTED set (_get_backends_for_op
    "search"), which is just [reachable] here — down_first is not part of
    it and must never be probed or searched at all."""
    project_dir = tmp_path / "project"
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  down_first:\n"
        "    url: https://down-first.example\n"
        "    api_key: test-key\n"
        "  reachable:\n"
        "    url: https://reachable.example\n"
        "    api_key: test-key\n"
        "routing:\n"
        "  search: [reachable]\n"
    )

    responses = [
        # Every request to down-first.example — /health or /search, any
        # prefix — is a connection failure. If the fix regresses and this
        # host gets probed or searched at all, it fails loudly rather than
        # quietly "succeeding" like the old mock would have.
        {"url_contains": "down-first.example", "fail": True},
        {
            "url_contains": "reachable.example",
            "url_suffix": "/search",
            "source_prefix": "claude-code/project",
            "response": {
                "results": [
                    {
                        "id": 1,
                        "source": "claude-code/project",
                        "text": "Reached the routed, healthy backend.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        },
    ]

    payload = {"cwd": str(project_dir), "prompt": "hello"}
    result, calls, home_dir = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=responses,
        extra_env={"MEMORIES_URL": "", "CLAUDE_PROJECT_DIR": str(project_dir)},
    )

    assert result.returncode == 0

    down_calls = [c for c in calls if "down-first.example" in str(c["url"])]
    reachable_calls = [c for c in calls if "reachable.example" in str(c["url"])]

    assert down_calls == [], (
        f"down_first is excluded by routing.search and must never be "
        f"contacted at all (health or search), got: {down_calls}"
    )
    assert len(reachable_calls) > 0, "the routed, healthy backend must be contacted"
    reachable_search_calls = [c for c in reachable_calls if str(c["url"]).endswith("/search")]
    assert len(reachable_search_calls) > 0, "the routed backend must actually be searched"

    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    # memory-recall.sh renders candidate pointers, not raw text (see other
    # tests in this file) — the reachable backend's result (id=1) must be
    # the one surfaced.
    assert "candidate memory id=1" in ctx and "claude-code/project" in ctx, (
        f"results from the healthy routed backend must be injected, got: {ctx}"
    )
    assert "down_first" not in ctx and "down-first.example" not in ctx, (
        f"no warning may name a backend that was never in the routed search "
        f"set, got: {ctx}"
    )
    assert not (home_dir / ".config" / "memories" / "backend-down").exists(), (
        "a single non-routed dead backend must not trip the shared breaker"
    )


def test_memory_hooks_preexisting_open_breaker_does_not_crash(tmp_path: Path) -> None:
    """REGRESSION (PR #85 review, P1-A — CRASH, reviewer reproduced). A
    previous hook invocation already tripped the (single-backend, "default"
    name) breaker file. _health_check's early return on an already-open
    breaker never set MEMORIES_HEALTH_DOWN_NAMES, and memory-recall.sh
    interpolates it immediately after `_health_check` returns 1 — under
    `set -u`. So the FAST path — the common case once a backend is actually
    down — crashed: exit 1, "unbound variable", zero hook output. Worse than
    the bug it replaced. This is the case no prior test covered: every other
    regression test in this file starts from a CLEAN breaker state."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    # _run_hook creates HOME at tmp_path/"home" with mkdir(exist_ok=True) —
    # pre-seed the breaker file into that same path before invoking it, to
    # simulate "a previous hook invocation already opened the breaker."
    home_dir = tmp_path / "home"
    breaker_dir = home_dir / ".config" / "memories"
    breaker_dir.mkdir(parents=True, exist_ok=True)
    (breaker_dir / "backend-down").write_text(str(int(time.time())))

    result, calls, _ = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses=[])

    assert result.returncode == 0, (
        f"must not crash when the breaker is already open on entry; "
        f"stderr: {result.stderr!r}"
    )
    assert "unbound variable" not in result.stderr, result.stderr
    # Must still produce a well-formed hook response, not empty/broken
    # output from a mid-script crash.
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "not reachable" in ctx or "Memory Playbook" in ctx
    # The already-open breaker must mean no curl calls were attempted for
    # that (fast-skipped) backend.
    assert calls == []


def test_memory_hooks_slow_backend_stays_within_session_start_budget(tmp_path: Path) -> None:
    """REGRESSION (PR #85 review, P1-B — BUDGET, reviewer reproduced). The
    blind spot in every prior partial-failure test (including the one right
    above this) was that they only ever simulated INSTANT failures (a
    closed port -> immediate ECONNREFUSED). A backend that accepts the TCP
    connection and never responds is a completely different cost shape: the
    only thing that would catch it is measuring real wall-clock time against
    a real hanging socket, which is what this test does (no fake-curl mock
    — real system curl, real sockets).

    Two backends, no explicit routing (both are in the routed search set,
    matching the reviewer's exact repro): one real local HTTP server that
    answers instantly, one real TCP listener that accepts and never writes
    a byte back. memory-recall.sh makes ~5-6 sequential
    _search_memories_multi calls per session (one per source prefix, plus a
    fallback, plus a dedicated WIP search); hooks.json gives this hook a 5s
    budget. Before the per-backend breaker fix, each of those calls would
    re-discover the hang via its own fresh --max-time timeout, in serial —
    trivially blowing the budget even though the healthy backend answered
    every single time."""
    # A real backend that answers immediately.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, body: dict[str, object]) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            self._reply({"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self._reply(
                {
                    "results": [
                        {
                            "id": 7,
                            "source": "claude-code/project",
                            "text": "Reached the healthy backend despite the hang.",
                            "similarity": 0.85,
                        }
                    ],
                    "count": 1,
                }
            )

        def log_message(self, *_args: object) -> None:  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    reachable_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # A real listener that accepts the connection and never responds.
    hang_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    hang_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    hang_sock.bind(("127.0.0.1", 0))
    hang_sock.listen(20)
    hang_port = hang_sock.getsockname()[1]
    stop_event = threading.Event()
    dangling_conns: list[socket.socket] = []

    def _accept_and_hang() -> None:
        hang_sock.settimeout(0.2)
        while not stop_event.is_set():
            try:
                conn, _addr = hang_sock.accept()
                dangling_conns.append(conn)  # never read, never write, never close
            except socket.timeout:
                continue
            except OSError:
                break

    hang_thread = threading.Thread(target=_accept_and_hang, daemon=True)
    hang_thread.start()

    try:
        project_dir = tmp_path / "project"
        (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
        (project_dir / ".memories" / "backends.yaml").write_text(
            "backends:\n"
            f"  hangs:\n"
            f"    url: http://127.0.0.1:{hang_port}\n"
            f"    api_key: test-key\n"
            f"  reachable:\n"
            f"    url: http://127.0.0.1:{reachable_port}\n"
            f"    api_key: test-key\n"
        )

        home_dir = tmp_path / "home"
        home_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home_dir),
                "MEMORIES_URL": "",
                "MEMORIES_API_KEY": "test-key",
                "MEMORIES_ENV_FILE": str(tmp_path / "missing-env"),
                "CLAUDE_PROJECT_DIR": str(project_dir),
            }
        )

        payload = {"cwd": str(project_dir), "prompt": "hello"}

        start = time.monotonic()
        result = subprocess.run(
            [str(RECALL_SCRIPT)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=20,  # pytest-level safety net; the real assertion is below
        )
        elapsed = time.monotonic() - start
    finally:
        stop_event.set()
        hang_thread.join(timeout=2)
        server.shutdown()
        server_thread.join(timeout=2)
        for c in dangling_conns:
            try:
                c.close()
            except OSError:
                pass
        hang_sock.close()

    print(f"\n[timing] slow-backend hook run took {elapsed:.3f}s (budget 5.0s)")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert elapsed < 5.0, (
        f"SessionStart's hooks.json budget is 5s; a hanging backend must not "
        f"blow it — took {elapsed:.2f}s. stderr: {result.stderr}"
    )

    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=7" in ctx and "claude-code/project" in ctx, (
        f"results from the healthy backend must still be injected despite "
        f"the hang, got: {ctx}"
    )


def test_memory_hooks_health_ok_search_hangs_stays_within_budget(tmp_path: Path) -> None:
    """(a) REGRESSION (PR #85 review, round 7 — reviewer's new P1). The
    per-backend breaker (7c3d5d8) cannot help on the FIRST occurrence of a
    backend whose /health responds promptly but whose /search hangs:
    nothing yet knows THAT backend is bad on THAT endpoint, so the first
    search fan-out still pays a full, flat --max-time against it before the
    breaker can trip. Reviewer measured 5.65s against a 5s budget even with
    a healthy local peer. The round-6 slow-backend test (right above this
    one) missed this specific shape because its hanging listener stalled
    /health too, letting the 2s health probe open the breaker before any
    search was ever attempted — this test isolates the health-OK/search-
    hang split specifically, real sockets, real curl, no mock."""

    class _HangOnSearchHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            data = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            # Read the request, then never respond — the client's own
            # --max-time is the only thing that will ever end this.
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            time.sleep(30)

        def log_message(self, *_args: object) -> None:
            pass

    class _HealthyHandler(http.server.BaseHTTPRequestHandler):
        def _reply(self, body: dict[str, object]) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._reply({"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self._reply(
                {
                    "results": [
                        {
                            "id": 55,
                            "source": "claude-code/project",
                            "text": "Reached the healthy backend despite the search-hang split.",
                            "similarity": 0.85,
                        }
                    ],
                    "count": 1,
                }
            )

        def log_message(self, *_args: object) -> None:
            pass

    hang_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HangOnSearchHandler)
    hang_server.daemon_threads = True
    hang_port = hang_server.server_address[1]
    hang_thread = threading.Thread(target=hang_server.serve_forever, daemon=True)
    hang_thread.start()

    healthy_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler)
    healthy_server.daemon_threads = True
    healthy_port = healthy_server.server_address[1]
    healthy_thread = threading.Thread(target=healthy_server.serve_forever, daemon=True)
    healthy_thread.start()

    try:
        project_dir = tmp_path / "project"
        (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
        (project_dir / ".memories" / "backends.yaml").write_text(
            "backends:\n"
            f"  hangs_on_search:\n"
            f"    url: http://127.0.0.1:{hang_port}\n"
            f"    api_key: test-key\n"
            f"  reachable:\n"
            f"    url: http://127.0.0.1:{healthy_port}\n"
            f"    api_key: test-key\n"
        )

        home_dir = tmp_path / "home"
        home_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home_dir),
                "MEMORIES_URL": "",
                "MEMORIES_API_KEY": "test-key",
                "MEMORIES_ENV_FILE": str(tmp_path / "missing-env"),
                "CLAUDE_PROJECT_DIR": str(project_dir),
            }
        )

        payload = {"cwd": str(project_dir), "prompt": "hello"}

        start = time.monotonic()
        result = subprocess.run(
            [str(RECALL_SCRIPT)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=20,  # pytest-level safety net; the real assertion is below
        )
        elapsed = time.monotonic() - start
    finally:
        healthy_server.shutdown()
        healthy_thread.join(timeout=2)
        hang_server.shutdown()
        hang_thread.join(timeout=2)

    print(f"\n[timing] health-OK/search-hang split hook run took {elapsed:.3f}s (budget 5.0s)")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert elapsed < 5.0, (
        f"SessionStart's hooks.json budget is 5s; a health-OK/search-hang "
        f"backend must not blow it — took {elapsed:.2f}s. stderr: {result.stderr}"
    )

    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=55" in ctx and "claude-code/project" in ctx, (
        f"results from the healthy backend must still be injected despite "
        f"the search-hang split, got: {ctx}"
    )


def test_memory_hooks_all_backends_hang_stays_within_budget(tmp_path: Path) -> None:
    """(b) Everything hangs: BOTH routed backends accept the TCP connection
    and never respond at all (not even /health) — no healthy backend in the
    mix. The hook must still return within budget with an honest "nothing
    found" result, never a crash or a process hard-killed past hooks.json's
    own timeout with no output at all."""
    listeners: list[socket.socket] = []
    dangling_conns: list[socket.socket] = []
    stop_event = threading.Event()
    accept_threads: list[threading.Thread] = []
    ports: list[int] = []

    def _accept_and_hang(sock: socket.socket) -> None:
        sock.settimeout(0.2)
        while not stop_event.is_set():
            try:
                conn, _addr = sock.accept()
                dangling_conns.append(conn)
            except socket.timeout:
                continue
            except OSError:
                break

    for _ in range(2):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(20)
        listeners.append(s)
        ports.append(s.getsockname()[1])
        t = threading.Thread(target=_accept_and_hang, args=(s,), daemon=True)
        t.start()
        accept_threads.append(t)

    try:
        project_dir = tmp_path / "project"
        (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
        (project_dir / ".memories" / "backends.yaml").write_text(
            "backends:\n"
            f"  hangs_a:\n"
            f"    url: http://127.0.0.1:{ports[0]}\n"
            f"    api_key: test-key\n"
            f"  hangs_b:\n"
            f"    url: http://127.0.0.1:{ports[1]}\n"
            f"    api_key: test-key\n"
        )

        home_dir = tmp_path / "home"
        home_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home_dir),
                "MEMORIES_URL": "",
                "MEMORIES_API_KEY": "test-key",
                "MEMORIES_ENV_FILE": str(tmp_path / "missing-env"),
                "CLAUDE_PROJECT_DIR": str(project_dir),
            }
        )

        payload = {"cwd": str(project_dir), "prompt": "hello"}

        start = time.monotonic()
        result = subprocess.run(
            [str(RECALL_SCRIPT)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )
        elapsed = time.monotonic() - start
    finally:
        stop_event.set()
        for t in accept_threads:
            t.join(timeout=2)
        for c in dangling_conns:
            try:
                c.close()
            except OSError:
                pass
        for s in listeners:
            s.close()

    print(f"\n[timing] all-backends-hang hook run took {elapsed:.3f}s (budget 5.0s)")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert elapsed < 5.0, (
        f"SessionStart's hooks.json budget is 5s; a fully-hung routed set "
        f"must still return within it — took {elapsed:.2f}s. stderr: {result.stderr}"
    )
    output = json.loads(result.stdout)  # must still be well-formed JSON
    assert "hookSpecificOutput" in output


def test_memory_hooks_healthy_only_baseline_unchanged(tmp_path: Path) -> None:
    """(c) Baseline: nothing fails, nothing hangs. The deadline machinery
    must not truncate or otherwise change behavior in the common case —
    every scoped prefix is still searched (no early break), and the hook
    completes fast (mocked harness, no real network)."""
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 1,
                        "source": "claude-code/memories",
                        "text": "Baseline result.",
                        "similarity": 0.9,
                    }
                ],
                "count": 1,
            },
        },
    ]
    payload = {"cwd": "/Users/example/memories"}

    start = time.monotonic()
    result, calls, _ = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses)
    elapsed = time.monotonic() - start

    assert result.returncode == 0
    assert elapsed < 5.0, f"the healthy baseline must be fast, took {elapsed:.2f}s"

    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "candidate memory id=1" in ctx
    assert "budget exhausted" not in ctx.lower()

    # All 4 default scoped prefixes, plus the dedicated WIP search, must
    # have actually been attempted — no truncation in the unhindered case.
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    prefixes = [c["body"].get("source_prefix", "") for c in search_calls if c["body"] is not None]
    assert "claude-code/memories" in prefixes
    assert "codex/memories" in prefixes
    assert "learning/memories" in prefixes
    assert prefixes.count("wip/memories") >= 1


def test_memory_hooks_tiny_budget_exhausted_from_start_exits_cleanly(tmp_path: Path) -> None:
    """(d) Budget-exhaustion path: MEMORIES_HOOK_BUDGET_MS forced absurdly
    small (the deadline is already in the past before the first call). The
    hook must still exit 0 with valid, well-formed JSON — never a crash,
    never empty/malformed output — even though every backend call this run
    gets skipped by the deadline."""
    payload = {"cwd": "/Users/example/memories", "prompt": "hello"}

    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_HOOK_BUDGET_MS": "1"},
    )

    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    output = json.loads(result.stdout)  # must parse — never malformed/empty
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "Memory Playbook" in ctx
    assert "budget exhausted" in ctx.lower() or "not reachable" in ctx.lower()
    # Every backend call this run should have been skipped by the deadline.
    assert calls == []


def test_memory_hooks_explicit_backends_file_wins_over_project_root(tmp_path: Path) -> None:
    """Precedence: MEMORIES_BACKENDS_FILE is an explicit override and must win
    over a project-root .memories/backends.yaml, even when both exist."""
    project_dir = tmp_path / "project"
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  root:\n"
        "    url: https://project-root.example\n"
        "    api_key: test-key\n"
    )

    explicit_file = tmp_path / "explicit-backends.yaml"
    explicit_file.write_text(
        "backends:\n"
        "  explicit:\n"
        "    url: https://explicit-override.example\n"
        "    api_key: test-key\n"
    )

    payload = {"cwd": str(project_dir), "prompt": "hello"}
    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={
            "MEMORIES_URL": "",
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "MEMORIES_BACKENDS_FILE": str(explicit_file),
        },
    )

    assert result.returncode == 0
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert len(search_calls) > 0
    urls = [c["url"] for c in search_calls]
    assert all(url.startswith("https://explicit-override.example") for url in urls), (
        f"MEMORIES_BACKENDS_FILE must win over the project-root file, got: {urls}"
    )
    assert not any("project-root.example" in url for url in urls)


def test_memory_hooks_cwd_only_backends_yaml_still_loads(tmp_path: Path) -> None:
    """Backward compat: a project that only ever configured via a
    cwd-relative .memories/backends.yaml (no CLAUDE_PROJECT_DIR, predating
    that support) must keep loading it — the fix for the gate/loader mismatch
    must not drop the existing cwd-based fallback.

    No CLAUDE_PROJECT_DIR is set, so the gate has nothing but $PWD to check
    (same documented residual limitation as the unconfigured case) — this
    test forces activation via MEMORIES_ENABLED=true to isolate what it
    actually verifies: _load_backends' cwd fallback still resolves once the
    hook is running.
    """
    project_dir = tmp_path / "project"
    (project_dir / ".memories").mkdir(parents=True, exist_ok=True)
    (project_dir / ".memories" / "backends.yaml").write_text(
        "backends:\n"
        "  cwd_only:\n"
        "    url: https://cwd-only.example\n"
        "    api_key: test-key\n"
    )

    payload = {"cwd": str(project_dir), "prompt": "hello"}
    result, calls, _ = _run_hook(
        RECALL_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_URL": "", "MEMORIES_ENABLED": "true"},
    )

    assert result.returncode == 0
    search_calls = [c for c in calls if str(c["url"]).endswith("/search")]
    assert len(search_calls) > 0
    urls = [c["url"] for c in search_calls]
    assert all(url.startswith("https://cwd-only.example") for url in urls), (
        f"cwd-only backends.yaml must still load, got: {urls}"
    )


def test_memory_recall_401_shows_credential_warning_not_reachability(tmp_path: Path) -> None:
    """/health is unauthenticated and can't see a bad API key. The hook must detect
    the 401 from the /search calls it already makes and name the real problem,
    instead of silently returning nothing or blaming service reachability."""
    responses = [
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "status": 401,
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "codex/memories",
            "status": 401,
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "learning/memories",
            "status": 401,
            "response": {"results": [], "count": 0},
        },
        {
            "url_suffix": "/search",
            "source_prefix": "wip/memories",
            "status": 401,
            "response": {"results": [], "count": 0},
        },
    ]

    payload = {"cwd": "/Users/example/memories"}
    result, calls, _ = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]
    assert "rejected the API key" in ctx
    assert "MEMORIES_API_KEY" in ctx
    assert "Check that the service is running" not in ctx
    assert len(calls) > 0


def test_memory_query_logs_candidate_ids_for_non_active_prompts(tmp_path: Path) -> None:
    """Every prompt that gets injected candidates logs which memory ids were surfaced.

    This is the surfaced half of the recall-feedback loop: without it,
    surfaced-but-never-used memories can never be detected.
    """
    metrics_log = tmp_path / "active-search.jsonl"
    responses = [
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
        {
            "url_suffix": "/search",
            "source_prefix": "claude-code/memories",
            "response": {
                "results": [
                    {
                        "id": 11,
                        "source": "claude-code/memories",
                        "text": "Webhook retries use exponential backoff.",
                        "similarity": 0.86,
                    }
                ],
                "count": 1,
            },
        },
    ]

    payload = {
        "session_id": "session-passive",
        "cwd": "/Users/example/memories",
        "prompt": "Help me design webhook notifications for this billing service.",
    }

    result, _, _ = _run_hook(
        QUERY_SCRIPT,
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "prompt_evaluated"
    assert event["active_search_required"] is False
    assert event["hook_results_injected"] is True
    assert event["candidate_ids"] == [11, 99]
    assert "exponential backoff" not in json.dumps(event)
    assert "notification patterns" not in json.dumps(event)

def test_memory_query_logs_no_event_for_non_active_prompt_without_candidates(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    responses = [
        {"url_suffix": "/search", "source_prefix": "", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "claude-code/memories", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "codex/memories", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "learning/memories", "response": {"results": [], "count": 0}},
        {"url_suffix": "/search", "source_prefix": "wip/memories", "response": {"results": [], "count": 0}},
    ]

    payload = {
        "session_id": "session-quiet",
        "cwd": "/Users/example/memories",
        "prompt": "Help me design webhook notifications for this billing service.",
    }

    result, _, _ = _run_hook(
        QUERY_SCRIPT,
        tmp_path,
        payload,
        responses,
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    assert not metrics_log.exists()

def test_memory_observe_logs_memory_ids_from_tool_input(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "tool_name": "mcp__memories__memory_get",
        "tool_input": {"id": 42},
        "tool_response": {
            "content": [{"type": "text", "text": "[42] claude-code/memories 2026-06-01\n\nFull memory text."}]
        },
    }

    result, _, _ = _run_hook(
        OBSERVE_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["tool_name"] == "mcp__memories__memory_get"
    assert events[0]["memory_ids"] == [42]
    assert "Full memory text" not in json.dumps(events[0])

def test_memory_observe_logs_memory_ids_from_feedback_tool_input(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "tool_name": "mcp__memories__memory_is_useful",
        "tool_input": {"memory_id": 9, "signal": "useful"},
    }

    result, _, _ = _run_hook(
        OBSERVE_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert events[0]["memory_ids"] == [9]

def test_memory_observe_parses_memory_ids_from_search_response_text(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    response_text = (
        'Found 2 memories for "release gate":\n\n'
        "[1] id=42 (91%) codex/memories\nRelease is gated by setup validation.\n\n---\n\n"
        "[2] id=7 (84%) learning/memories\nUse uv for python projects. valid=99 must not match."
    )
    payload = {
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "tool_name": "mcp__memories__memory_search",
        "tool_input": {"query": "private query text", "source_prefix": "codex/memories"},
        "tool_response": {"content": [{"type": "text", "text": response_text}]},
    }

    result, _, _ = _run_hook(
        OBSERVE_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert events[0]["memory_ids"] == [7, 42]
    assert "private query text" not in json.dumps(events[0])
    assert "setup validation" not in json.dumps(events[0])

def test_memory_observe_ignores_result_indices_without_id_markers(tmp_path: Path) -> None:
    """Plain [1]/[2] result indices must not be mistaken for memory ids."""
    metrics_log = tmp_path / "active-search.jsonl"
    response_text = (
        'Found 2 memories for "release gate":\n\n'
        "[1] (91%) codex/memories\nNo ids in this legacy format.\n\n---\n\n"
        "[2] (84%) learning/memories\nStill no ids."
    )
    payload = {
        "session_id": "session-1",
        "cwd": "/Users/example/memories",
        "tool_name": "mcp__memories__memory_search",
        "tool_input": {"query": "q", "source_prefix": ""},
        "tool_response": {"content": [{"type": "text", "text": response_text}]},
    }

    result, _, _ = _run_hook(
        OBSERVE_SCRIPT,
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert events[0]["memory_ids"] == []

def test_codex_memory_observe_logs_memory_ids(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "codex-session",
        "cwd": "/Users/example/memories",
        "tool_name": "mcp__memories__memory_get",
        "tool_input": {"id": 314},
    }

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-observe.sh",
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0
    events = [json.loads(line) for line in metrics_log.read_text().splitlines()]
    assert events[0]["memory_ids"] == [314]


def test_codex_memory_observe_logs_nested_exec_memory_tools(tmp_path: Path) -> None:
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "codex-nested-exec",
        "cwd": "/Users/example/memories",
        "tool_name": "exec",
        "tool_input": {
            "input": (
                'const a = await tools.mcp__memories__memory_search({query:"release", '
                'source_prefix:"codex/memories"});\n'
                'const b = await tools.mcp__memories__memory_get({id:42});\n'
                'text(JSON.stringify({a,b}));'
            )
        },
        "tool_response": {
            "content": [
                {
                    "type": "text",
                    "text": "Found 1 compact memories: [1] id=42 codex/memories",
                }
            ]
        },
    }

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-observe.sh",
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in metrics_log.read_text(encoding="utf-8").splitlines()]
    assert [event["tool_name"] for event in events] == [
        "mcp__memories__memory_get",
        "mcp__memories__memory_search",
    ]
    assert all(event["parent_tool"] == "exec" for event in events)
    assert all(event["observed_via"] == "nested_exec" for event in events)
    assert all(event["memory_ids"] == [42] for event in events)
    search_event = next(event for event in events if event["tool_name"].endswith("memory_search"))
    assert search_event["source_prefixes"] == ["codex/memories"]
    assert search_event["source_prefix_quality"] == "exact_project"
    assert "release" not in json.dumps(events)


def test_codex_hooks_config_observes_exec_envelopes() -> None:
    hooks = json.loads((CODEX_HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    matcher = hooks["hooks"]["PostToolUse"][0]["matcher"]
    assert "exec" in matcher
    # Server-name-agnostic: matches the local server AND any custom-named
    # connector or UUID-named connector, not just the literal "memories".
    assert re.search(matcher, "mcp__memories__memory_search")
    assert re.search(matcher, "mcp__Remote_Memories__memory_search")
    assert re.search(matcher, "mcp__843a7d55-4d6a-4efb-b73e-90428866e135__memory_search")
    assert re.search(matcher, "exec")


def test_hooks_json_posttooluse_matcher_is_server_name_agnostic() -> None:
    """PostToolUse matcher must key off the memory_* TOOL name, not a hardcoded
    server segment — a claude.ai connector can register under any name the
    user picks, or a UUID when surfaced into local Claude Code."""
    hooks = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    post_tool_use = hooks["hooks"]["PostToolUse"]
    memory_observe_entry = next(
        entry for entry in post_tool_use if "memory-observe.sh" in entry["hooks"][0]["command"]
    )
    matcher = memory_observe_entry["matcher"]

    for shape in (
        "mcp__memories__memory_search",
        "mcp__Remote_Memories__memory_search",
        "mcp__843a7d55-4d6a-4efb-b73e-90428866e135__memory_search",
    ):
        assert re.search(matcher, shape), f"matcher {matcher!r} should match {shape!r}"

    for unrelated in ("Read", "Write", "Bash", "mcp__slack__send_message"):
        assert not re.search(matcher, unrelated), f"matcher {matcher!r} should not match {unrelated!r}"


def test_memory_query_injected_context_uses_name_agnostic_toolsearch(tmp_path: Path) -> None:
    """The injected hint must resolve regardless of what the memory MCP server
    is named — 'select:mcp__memories__memory_search' is unresolvable the
    moment the server isn't literally named 'memories'."""
    payload = {
        "cwd": "/Users/example/memories",
        "prompt": "Weren't we going to migrate the embedder to the new model?",
    }

    result, _, _ = _run_hook(QUERY_SCRIPT, tmp_path, payload, responses=[])

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "select:mcp__memories__" not in ctx
    assert 'ToolSearch("+memory_search")' in ctx


def test_memory_recall_injected_context_uses_name_agnostic_toolsearch(tmp_path: Path) -> None:
    payload = {"cwd": "/Users/example/memories"}
    result, _, _ = _run_hook(RECALL_SCRIPT, tmp_path, payload, responses=[])

    assert result.returncode == 0
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "select:mcp__memories__" not in ctx
    assert 'ToolSearch("+memory_search")' in ctx


def _lib_eval(expr: str) -> subprocess.CompletedProcess:
    """Source the Claude hook lib and evaluate a shell expression against it."""
    lib = HOOKS_DIR / "_lib.sh"
    return subprocess.run(
        ["bash", "-c", f'MEMORIES_HOOK_NAME=test; . "{lib}" >/dev/null 2>&1; {expr}'],
        capture_output=True,
        text=True,
    )


def _codex_lib_eval(expr: str) -> subprocess.CompletedProcess:
    """Source the Codex hook lib and evaluate a shell expression against it."""
    lib = CODEX_HOOKS_DIR / "_lib.sh"
    return subprocess.run(
        ["bash", "-c", f'MEMORIES_HOOK_NAME=test; . "{lib}" >/dev/null 2>&1; {expr}'],
        capture_output=True,
        text=True,
    )


def test_breaker_trip_decision_distinguishes_starvation_from_a_hanging_backend() -> None:
    """A curl timeout (exit 28) is evidence about the backend only if the
    backend got substantially the time we meant to give it.

    Measured against a real backend, /search takes 1.2-2.1s while the
    minimum-call floor is 0.3s, so a SessionStart recall's tail calls are
    issued with budgets that cannot succeed; tripping on those marks a healthy
    backend down and the next session reports "not reachable" without probing.

    But "starved" must mean materially less, not merely less: health and
    version probes run before search, so overhead trims a 4s cap to ~3.9s. A
    backend that hangs through 3.9 of 4 seconds IS unhealthy, and failing to
    trip there makes every later session re-pay the full timeout.
    """
    cap = 4

    # Got most of the intended cap and still timed out -> real evidence.
    for budget in ("4", "3.9", "3.0"):  # 3.0 is the 0.75 boundary, inclusive
        r = _lib_eval(f'_should_trip_breaker 28 {budget} {cap} && echo TRIP || echo NOTRIP')
        assert r.stdout.strip() == "TRIP", f"budget={budget}: {r.stderr}"

    # Materially starved -> our deadline, not their downtime.
    for budget in ("2.9", "1.0", "0.54"):
        r = _lib_eval(f'_should_trip_breaker 28 {budget} {cap} && echo TRIP || echo NOTRIP')
        assert r.stdout.strip() == "NOTRIP", f"budget={budget}: {r.stderr}"

    # Non-timeout failures say something real regardless of budget.
    for rc in (7, 22, 52):
        r = _lib_eval(f'_should_trip_breaker {rc} 0.54 {cap} && echo TRIP || echo NOTRIP')
        assert r.stdout.strip() == "TRIP", f"rc={rc}: {r.stderr}"


def test_codex_breaker_timeout_budget_is_materially_short_not_trip() -> None:
    cap = 4
    for budget in ("4", "3.9", "3.0"):
        result = _codex_lib_eval(
            f'_should_trip_breaker 28 {budget} {cap} && echo TRIP || echo NOTRIP'
        )
        assert result.stdout.strip() == "TRIP", f"budget={budget}: {result.stderr}"
    for budget in ("2.9", "1.0", "0.54"):
        result = _codex_lib_eval(
            f'_should_trip_breaker 28 {budget} {cap} && echo TRIP || echo NOTRIP'
        )
        assert result.stdout.strip() == "NOTRIP", f"budget={budget}: {result.stderr}"


def test_codex_breaker_names_with_punctuation_are_collision_free() -> None:
    result = _codex_lib_eval(
        "tmp=$(mktemp -d); "
        "MEMORIES_LOG=\"$tmp/log\"; "
        "_MEMORIES_BREAKER_FILE=\"$tmp/backend-down\"; "
        "a=$(_breaker_file_for 'foo/bar'); "
        "b=$(_breaker_file_for 'foo?bar'); "
        "_breaker_trip 'foo/bar'; "
        "if [ \"$a\" != \"$b\" ] && _breaker_open 'foo/bar' && ! _breaker_open 'foo?bar' "
        "&& [ \"$(_breaker_file_for default)\" = \"$tmp/backend-down\" ]; then echo PASS; fi"
    )
    assert result.stdout.strip() == "PASS", result.stderr


def test_codex_fallback_parser_accepts_punctuation_backend_keys_and_routes_breakers() -> None:
    result = _codex_lib_eval(
        "tmp=$(mktemp -d); "
        "printf '%s\\n' 'backends:' '  foo/bar:' '    url: https://slash.example' '    api_key: test-key' "
        "'  foo?bar:' '    url: https://question.example' '    api_key: test-key' "
        "'routing:' '  search: [foo/bar, foo?bar]' > \"$tmp/backends.yaml\"; "
        "parsed=$(_parse_backends_yaml \"$tmp/backends.yaml\"); "
        "_BACKENDS_CACHE=\"$parsed\"; "
        "routed=$(_get_backends_for_op search | jq -r 'map(.name) | join(\",\")'); "
        "_MEMORIES_BREAKER_FILE=\"$tmp/backend-down\"; "
        "a=$(_breaker_file_for 'foo/bar'); b=$(_breaker_file_for 'foo?bar'); "
        "_breaker_trip 'foo/bar'; "
        "if [ \"$routed\" = 'foo/bar,foo?bar' ] && [ \"$a\" != \"$b\" ] "
        "&& _breaker_open 'foo/bar' && ! _breaker_open 'foo?bar'; then echo PASS; fi"
    )
    assert result.stdout.strip() == "PASS", result.stderr


def test_codex_memory_observe_nested_exec_matches_non_memories_server_names(tmp_path: Path) -> None:
    """The exec-envelope grep must not be hardcoded to the 'memories' server
    segment. A UUID-named connector contains hyphens, which JS parses as
    subtraction inside a dotted identifier, so real calls to those servers can
    only ever appear in bracket form — the parser must read that form."""
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "codex-nested-exec-uuid",
        "cwd": "/Users/example/memories",
        "tool_name": "exec",
        "tool_input": {
            "input": (
                'const a = await tools["mcp__843a7d55-4d6a-4efb-b73e-90428866e135__memory_search"]('
                '{query:"release", source_prefix:"codex/memories"});\n'
                'text(JSON.stringify({a}));'
            )
        },
    }

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-observe.sh",
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in metrics_log.read_text(encoding="utf-8").splitlines()]
    assert [event["tool_name"] for event in events] == [
        "mcp__843a7d55-4d6a-4efb-b73e-90428866e135__memory_search"
    ]
    assert events[0]["source_prefix"] == "codex/memories"


def test_codex_memory_observe_nested_exec_reads_every_bracket_spelling(tmp_path: Path) -> None:
    """Bracket property access is valid JS with either quote style and with
    padding inside the brackets. All three spellings must be recognized, and a
    bare tool name that is not a property access must not be counted."""
    metrics_log = tmp_path / "active-search.jsonl"
    payload = {
        "session_id": "codex-nested-exec-brackets",
        "cwd": "/Users/example/memories",
        "tool_name": "exec",
        "tool_input": {
            "input": (
                'const a = await tools["mcp__Remote_Memories__memory_search"]({query:"a"});\n'
                "const b = await tools['mcp__843a-bbb__memory_get']({id:1});\n"
                'const c = await tools[ "mcp__843a-ccc__memory_add" ]({text:"x"});\n'
                'const d = await tools.mcp__memories__memory_count({});\n'
                'console.log("mcp__not_a_call__memory_delete");\n'
            )
        },
    }

    result, _, _ = _run_hook(
        CODEX_HOOKS_DIR / "memory-observe.sh",
        tmp_path,
        payload,
        responses=[],
        extra_env={"MEMORIES_ACTIVE_SEARCH_LOG": str(metrics_log)},
    )

    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in metrics_log.read_text(encoding="utf-8").splitlines()]
    assert sorted(event["tool_name"] for event in events) == [
        "mcp__843a-bbb__memory_get",
        "mcp__843a-ccc__memory_add",
        "mcp__Remote_Memories__memory_search",
        "mcp__memories__memory_count",
    ]
