"""Transcript watcher: capture from hookless sessions, idempotent via watermark."""

import json
from pathlib import Path

import pytest

from scripts.transcript_watcher import (
    assemble_text,
    is_idle,
    messages_after_cursor,
    parse_transcript_messages,
    plan_capture,
    resolve_project,
    run_once,
)


def _line(typ, uuid, text, cwd="/x"):
    if typ in ("user", "assistant"):
        return json.dumps({"type": typ, "uuid": uuid, "cwd": cwd, "message": {"content": text}})
    return json.dumps({"type": typ, "uuid": uuid})


class TestParse:
    def test_parses_user_and_assistant_only(self):
        lines = [
            _line("system", "s1", None),
            _line("user", "u1", "hello there friend"),
            _line("attachment", "a1", None),
            _line("assistant", "a2", "hi back"),
        ]
        msgs = parse_transcript_messages(lines)
        assert [m["uuid"] for m in msgs] == ["u1", "a2"]
        assert msgs[0]["role"] == "user"

    def test_array_content_blocks(self):
        line = json.dumps({
            "type": "user", "uuid": "u1", "cwd": "/x",
            "message": {"content": [
                {"type": "text", "text": "first"},
                {"type": "image", "source": {}},
                {"type": "text", "text": "second"},
            ]},
        })
        msgs = parse_transcript_messages([line])
        assert msgs[0]["text"] == "first second"

    def test_skips_malformed_and_empty(self):
        msgs = parse_transcript_messages(["not json", "", _line("user", "", "no uuid"), _line("user", "u1", "")])
        assert msgs == []


class TestCursor:
    def test_all_when_no_cursor(self):
        msgs = [{"uuid": "a"}, {"uuid": "b"}]
        assert messages_after_cursor(msgs, "") == msgs

    def test_after_known_cursor(self):
        msgs = [{"uuid": "a"}, {"uuid": "b"}, {"uuid": "c"}]
        assert [m["uuid"] for m in messages_after_cursor(msgs, "a")] == ["b", "c"]

    def test_unknown_cursor_returns_all(self):
        """A cursor not in the file (truncation/rotation) re-captures rather than losing data."""
        msgs = [{"uuid": "a"}, {"uuid": "b"}]
        assert messages_after_cursor(msgs, "ZZZ") == msgs


class TestPlanCapture:
    def _msgs(self, n, size=60):
        return [{"uuid": f"u{i}", "role": "user", "text": "x" * size} for i in range(n)]

    def test_captures_fresh_above_threshold(self):
        msgs = self._msgs(4)
        text, cursor = plan_capture(msgs, "", min_chars=100)
        assert text is not None
        assert cursor == "u3"

    def test_advances_cursor_even_when_too_short(self):
        msgs = self._msgs(1, size=10)
        text, cursor = plan_capture(msgs, "", min_chars=200)
        assert text is None
        assert cursor == "u0", "short bursts still advance the cursor so they aren't re-evaluated"

    def test_no_new_messages_keeps_cursor(self):
        msgs = self._msgs(2)
        text, cursor = plan_capture(msgs, "u1", min_chars=10)
        assert text is None and cursor == "u1"

    def test_only_fresh_messages_in_text(self):
        msgs = [
            {"uuid": "u0", "role": "user", "text": "OLD SECRET"},
            {"uuid": "u1", "role": "user", "text": "new content here that is long enough"},
        ]
        text, _ = plan_capture(msgs, "u0", min_chars=10)
        assert "OLD SECRET" not in text
        assert "new content" in text


class TestIdle:
    def test_idle_true_after_window(self):
        assert is_idle(mtime=1000.0, now=1400.0, idle_seconds=300)

    def test_idle_false_within_window(self):
        assert not is_idle(mtime=1000.0, now=1100.0, idle_seconds=300)


class TestResolveProject:
    def test_non_git_basename(self, tmp_path):
        d = tmp_path / "myproj"
        d.mkdir()
        assert resolve_project(str(d)) == "myproj"

    def test_empty_cwd(self):
        assert resolve_project("") == "unknown"


class TestRunOnce:
    @pytest.fixture
    def cfg(self, tmp_path):
        return {
            "base": "http://localhost:9", "key": "",
            "transcript_dir": str(tmp_path / "projects"),
            "idle_seconds": 300, "min_chars": 50, "source_prefix": "claude-code",
        }

    def _write_transcript(self, cfg, name, msgs, mtime):
        import os
        d = Path(cfg["transcript_dir"]) / "proj"
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_text("\n".join(
            json.dumps({"type": m[0], "uuid": m[1], "cwd": "/tmp/x", "message": {"content": m[2]}})
            for m in msgs
        ))
        os.utime(f, (mtime, mtime))
        return f

    def test_captures_idle_transcript(self, cfg, monkeypatch):
        import scripts.transcript_watcher as w
        monkeypatch.setattr(w, "_backend_healthy", lambda *a: True)
        submitted = []
        monkeypatch.setattr(w, "_submit_extraction", lambda base, key, text, source: submitted.append((source, text)) or True)

        self._write_transcript(cfg, "s.jsonl", [
            ("user", "u1", "Decision: we will use Qdrant for the vector store because it scales."),
            ("assistant", "a1", "Got it, recording that decision."),
        ], mtime=1000.0)

        state = {}
        n = w.run_once(cfg, state, now=2000.0)
        assert n == 1
        assert submitted[0][0] == "claude-code/x"
        assert "Qdrant" in submitted[0][1]

    def test_skips_active_transcript(self, cfg, monkeypatch):
        import scripts.transcript_watcher as w
        monkeypatch.setattr(w, "_backend_healthy", lambda *a: True)
        monkeypatch.setattr(w, "_submit_extraction", lambda *a: pytest.fail("should not submit active session"))
        self._write_transcript(cfg, "s.jsonl", [("user", "u1", "x" * 100)], mtime=1950.0)
        assert w.run_once(cfg, {}, now=2000.0) == 0

    def test_failed_submit_does_not_advance_cursor(self, cfg, monkeypatch):
        """A 429/error on submit must leave the watermark in place so the next
        pass retries; advancing it silently drops the backlog (seen live when
        the cold-start sweep flooded the extract queue)."""
        import scripts.transcript_watcher as w
        monkeypatch.setattr(w, "_backend_healthy", lambda *a: True)
        monkeypatch.setattr(w, "_submit_extraction", lambda *a: False)
        self._write_transcript(cfg, "s.jsonl", [
            ("user", "u1", "Decision: we will use Qdrant for the vector store because it scales."),
            ("assistant", "a1", "Got it, recording that decision."),
        ], mtime=1000.0)

        state = {}
        assert w.run_once(cfg, state, now=2000.0) == 0

        submitted = []
        monkeypatch.setattr(w, "_submit_extraction", lambda base, key, text, source: submitted.append(text) or True)
        assert w.run_once(cfg, state, now=2100.0) == 1
        assert "Qdrant" in submitted[0]

    def test_watermark_prevents_recapture(self, cfg, monkeypatch):
        import scripts.transcript_watcher as w
        monkeypatch.setattr(w, "_backend_healthy", lambda *a: True)
        calls = []
        monkeypatch.setattr(w, "_submit_extraction", lambda *a: calls.append(1) or True)
        f = self._write_transcript(cfg, "s.jsonl", [
            ("user", "u1", "Decision: use Qdrant because it scales well for our needs here."),
        ], mtime=1000.0)
        state = {}
        assert w.run_once(cfg, state, now=2000.0) == 1
        # second pass, same file unchanged -> no re-capture
        assert w.run_once(cfg, state, now=2100.0) == 0
        assert len(calls) == 1

    def test_skips_when_backend_down(self, cfg, monkeypatch):
        import scripts.transcript_watcher as w
        monkeypatch.setattr(w, "_backend_healthy", lambda *a: False)
        monkeypatch.setattr(w, "_submit_extraction", lambda *a: pytest.fail("must not submit when backend down"))
        self._write_transcript(cfg, "s.jsonl", [("user", "u1", "x" * 200)], mtime=1000.0)
        assert w.run_once(cfg, {}, now=2000.0) == 0
