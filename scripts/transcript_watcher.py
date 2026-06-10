#!/usr/bin/env python3
"""Capture memories from hookless sessions by watching transcript files.

Claude Desktop (and any client that writes JSONL transcripts but does not fire
Stop hooks) leaves its work uncaptured — SessionStart recall works there, but
nothing extracts. This daemon tails the transcript directory, and when a
session goes idle it sends the new messages (since a per-session watermark) to
the extraction endpoint. The watermark makes it idempotent across idle bursts
and daemon restarts.

Runs standalone (`python scripts/transcript_watcher.py`) or under launchd
(`integrations/launchd/com.memories.transcript-watcher.plist`).

Config via env:
    MEMORIES_URL, MEMORIES_API_KEY        backend
    WATCHER_TRANSCRIPT_DIR                 default ~/.claude/projects
    WATCHER_STATE_FILE                     default ~/.config/memories/watcher-state.json
    WATCHER_IDLE_SECONDS                   default 300 (capture after this much quiet)
    WATCHER_POLL_SECONDS                   default 60
    WATCHER_MIN_CHARS                      default 200 (skip trivially short bursts)
    WATCHER_SOURCE_PREFIX                  default claude-code (source = <prefix>/<project>)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- pure-function core (unit-tested) ---------------------------------------

_ROLE_TYPES = {"user", "assistant"}


def parse_transcript_messages(lines):
    """Parse JSONL transcript lines into ordered {uuid, role, text, cwd} dicts.

    Tolerant of mixed entry types (system, attachment, mode, etc.) — only
    user/assistant entries with extractable text are returned.
    """
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        typ = obj.get("type")
        if typ not in _ROLE_TYPES:
            continue
        uuid = obj.get("uuid")
        if not uuid:
            continue
        text = _extract_text(obj.get("message", {}))
        if not text or len(text) < 2:
            continue
        out.append({"uuid": uuid, "role": typ, "text": text, "cwd": obj.get("cwd", "")})
    return out


def _extract_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return " ".join(parts).strip()
    return ""


def messages_after_cursor(messages, cursor_uuid):
    """Return messages that appear after cursor_uuid (or all if cursor is unseen)."""
    if not cursor_uuid:
        return list(messages)
    seen = [m["uuid"] for m in messages]
    if cursor_uuid not in seen:
        return list(messages)
    idx = seen.index(cursor_uuid)
    return messages[idx + 1:]


def assemble_text(messages) -> str:
    """Render messages as a transcript for the extraction endpoint."""
    lines = []
    for m in messages:
        speaker = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{speaker}: {m['text']}")
    return "\n".join(lines)


def resolve_project(cwd: str) -> str:
    """Project name from cwd — git common dir (worktree-aware), basename fallback.

    Mirrors the hooks' _memories_resolve_project so watcher-captured memories
    land under the same source as hook-captured ones.
    """
    fallback = os.path.basename(cwd.rstrip("/")) if cwd else "unknown"
    if not cwd or not os.path.isdir(cwd):
        return fallback or "unknown"
    try:
        common = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return fallback or "unknown"
    if common.returncode != 0 or not common.stdout.strip():
        return fallback or "unknown"
    gitdir = common.stdout.strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(cwd, gitdir)
    if os.path.basename(gitdir.rstrip("/")) == ".git":
        root = os.path.dirname(gitdir.rstrip("/"))
        name = os.path.basename(root.rstrip("/"))
        if name:
            return name
    return fallback or "unknown"


def is_idle(mtime: float, now: float, idle_seconds: float) -> bool:
    return (now - mtime) >= idle_seconds


def plan_capture(messages, cursor_uuid, min_chars):
    """Decide what to capture. Returns (text, new_cursor) or (None, cursor).

    new_cursor advances to the latest message even when the burst is too short
    to extract, so trivial chatter is not re-evaluated every poll.
    """
    fresh = messages_after_cursor(messages, cursor_uuid)
    if not fresh:
        return None, cursor_uuid
    latest = messages[-1]["uuid"]
    text = assemble_text(fresh)
    if len(text) < min_chars:
        return None, latest
    return text, latest


# --- daemon -----------------------------------------------------------------

def _env(name, default):
    return os.environ.get(name, default)


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)


def _backend_healthy(base: str, key: str) -> bool:
    try:
        req = urllib.request.Request(f"{base}/health")
        if key:
            req.add_header("X-API-Key", key)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def _submit_extraction(base: str, key: str, text: str, source: str) -> bool:
    body = json.dumps({"messages": text, "source": source, "context": "session_end"}).encode()
    req = urllib.request.Request(f"{base}/memory/extract", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        print(f"[watcher] extraction submit failed for {source}: {e}", file=sys.stderr)
        return False


def run_once(cfg, state, now=None) -> int:
    """One scan pass. Mutates state in place; returns number of captures submitted."""
    now = now if now is not None else time.time()
    tdir = Path(cfg["transcript_dir"]).expanduser()
    if not tdir.is_dir():
        return 0
    if not _backend_healthy(cfg["base"], cfg["key"]):
        return 0
    captured = 0
    for path in sorted(tdir.rglob("*.jsonl")):
        try:
            st = path.stat()
        except OSError:
            continue
        if not is_idle(st.st_mtime, now, cfg["idle_seconds"]):
            continue
        key = str(path)
        entry = state.get(key, {})
        if entry.get("mtime") == st.st_mtime and entry.get("cursor"):
            continue  # unchanged since last pass
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        messages = parse_transcript_messages(lines)
        if not messages:
            state[key] = {"mtime": st.st_mtime, "cursor": entry.get("cursor", "")}
            continue
        text, new_cursor = plan_capture(messages, entry.get("cursor", ""), cfg["min_chars"])
        if text:
            project = resolve_project(messages[-1].get("cwd", ""))
            source = f"{cfg['source_prefix']}/{project}"
            if _submit_extraction(cfg["base"], cfg["key"], text, source):
                captured += 1
                print(f"[watcher] captured {len(text)} chars -> {source}", file=sys.stderr)
        state[key] = {"mtime": st.st_mtime, "cursor": new_cursor}
    return captured


def main() -> int:
    cfg = {
        "base": _env("MEMORIES_URL", "http://localhost:8900").rstrip("/"),
        "key": _env("MEMORIES_API_KEY", ""),
        "transcript_dir": _env("WATCHER_TRANSCRIPT_DIR", "~/.claude/projects"),
        "idle_seconds": float(_env("WATCHER_IDLE_SECONDS", "300")),
        "min_chars": int(_env("WATCHER_MIN_CHARS", "200")),
        "source_prefix": _env("WATCHER_SOURCE_PREFIX", "claude-code"),
    }
    state_path = Path(_env("WATCHER_STATE_FILE", "~/.config/memories/watcher-state.json")).expanduser()
    poll = float(_env("WATCHER_POLL_SECONDS", "60"))
    print(f"[watcher] watching {cfg['transcript_dir']} (idle={cfg['idle_seconds']}s, poll={poll}s)", file=sys.stderr)
    state = _load_state(state_path)
    while True:
        try:
            n = run_once(cfg, state)
            if n:
                _save_state(state_path, state)
        except Exception as e:
            print(f"[watcher] scan error: {e}", file=sys.stderr)
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
