"""Tests for Codex notify hook integration script."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "integrations"
    / "codex"
    / "memory-codex-notify.sh"
)


def _write_fake_curl(bin_dir: Path) -> Path:
    script = bin_dir / "curl"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_CURL_ARGS:?}"
: "${FAKE_CURL_BODY:?}"

: > "$FAKE_CURL_ARGS"
for arg in "$@"; do
  printf '%s\n' "$arg" >> "$FAKE_CURL_ARGS"
done

body=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -d|--data|--data-raw|--data-binary)
      shift
      body="${1:-}"
      ;;
  esac
  shift || true
done

printf '%s' "$body" > "$FAKE_CURL_BODY"
printf '{"job_id":"job-1"}\n'
"""
    )
    script.chmod(0o755)
    return script


def _run_hook(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    memories_url: str = "http://127.0.0.1:9999",
    api_key: str = "",
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    args_file = tmp_path / "curl-args.txt"
    body_file = tmp_path / "curl-body.txt"

    env = os.environ.copy()
    env.update(
        {
            "MEMORIES_URL": memories_url,
            "MEMORIES_API_KEY": api_key,
            "MEMORIES_ENV_FILE": str(tmp_path / "no-hook-env"),
            "FAKE_CURL_ARGS": str(args_file),
            "FAKE_CURL_BODY": str(body_file),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [str(SCRIPT), json.dumps(payload)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return result, args_file, body_file, bin_dir


def test_codex_notify_posts_extraction_payload(tmp_path: Path) -> None:
    assert SCRIPT.exists(), f"missing script: {SCRIPT}"

    payload = {
        "type": "agent-turn-complete",
        "thread-id": "thread-1",
        "turn-id": "turn-1",
        "cwd": "/Users/example/memories",
        "input-messages": ["use qdrant in prod", "remember we chose docker compose"],
        "last-assistant-message": "done, updated docker compose and docs",
    }

    result, args_file, body_file, _ = _run_hook(tmp_path, payload, api_key="abc123")

    assert result.returncode == 0
    assert args_file.exists()
    assert body_file.exists()

    args_text = args_file.read_text()
    assert "http://127.0.0.1:9999/memory/extract" in args_text
    assert "X-API-Key: abc123" in args_text

    body = json.loads(body_file.read_text())
    assert body["context"] == "after_agent"
    assert body["source"] == "codex/memories"
    assert "User: use qdrant in prod" in body["messages"]
    assert "User: remember we chose docker compose" in body["messages"]
    assert "Assistant: done, updated docker compose and docs" in body["messages"]


def test_codex_notify_skips_when_no_messages(tmp_path: Path) -> None:
    assert SCRIPT.exists(), f"missing script: {SCRIPT}"

    payload = {
        "type": "agent-turn-complete",
        "thread-id": "thread-1",
        "turn-id": "turn-1",
        "cwd": "/Users/example/memories",
        "input-messages": [],
        "last-assistant-message": "",
    }

    result, args_file, body_file, _ = _run_hook(tmp_path, payload)

    assert result.returncode == 0
    assert not args_file.exists()
    assert not body_file.exists()


def test_codex_notify_supports_camel_case_and_object_messages(tmp_path: Path) -> None:
    payload = {
        "type": "agent-turn-complete",
        "workspaceRoots": ["/Users/example/agent-project"],
        "inputMessages": [
            {"content": [{"type": "text", "text": "remember db migration order"}]}
        ],
        "lastAssistantMessage": {
            "content": [
                {"type": "text", "text": "captured and updated docs"},
            ]
        },
    }

    result, _, body_file, _ = _run_hook(tmp_path, payload)

    assert result.returncode == 0
    body = json.loads(body_file.read_text())
    assert body["source"] == "codex/agent-project"
    assert "User: remember db migration order" in body["messages"]
    assert "Assistant: captured and updated docs" in body["messages"]


def test_codex_notify_uses_transcript_when_available(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": "ship with qdrant and sqlite fallback"
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "implemented with feature flag"}
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n"
    )

    payload = {
        "type": "agent-turn-complete",
        "cwd": "/Users/example/memories",
        "transcript_path": str(transcript),
        "input-messages": [],
        "last-assistant-message": "",
    }

    result, _, body_file, _ = _run_hook(tmp_path, payload)

    assert result.returncode == 0
    body = json.loads(body_file.read_text())
    assert "User: ship with qdrant and sqlite fallback" in body["messages"]
    assert "Assistant: implemented with feature flag" in body["messages"]


def test_codex_notify_honors_source_env_overrides(tmp_path: Path) -> None:
    payload = {
        "type": "agent-turn-complete",
        "cwd": "/Users/example/memories",
        "input-messages": ["remember this source behavior"],
        "last-assistant-message": "",
    }

    prefix_result, _, prefix_body_file, _ = _run_hook(
        tmp_path / "prefix",
        payload,
        extra_env={"MEMORIES_SOURCE_PREFIX": "claude-code/"},
    )
    assert prefix_result.returncode == 0
    prefix_body = json.loads(prefix_body_file.read_text())
    assert prefix_body["source"] == "claude-code/memories"

    full_result, _, full_body_file, _ = _run_hook(
        tmp_path / "full",
        payload,
        extra_env={"MEMORIES_SOURCE": "tenant-a/project-x"},
    )
    assert full_result.returncode == 0
    full_body = json.loads(full_body_file.read_text())
    assert full_body["source"] == "tenant-a/project-x"


def test_codex_notify_skips_non_turn_events(tmp_path: Path) -> None:
    payload = {
        "type": "session-start",
        "cwd": "/Users/example/memories",
        "input-messages": ["this should be ignored"],
        "last-assistant-message": "ignored",
    }

    result, args_file, body_file, _ = _run_hook(tmp_path, payload)

    assert result.returncode == 0
    assert not args_file.exists()
    assert not body_file.exists()
