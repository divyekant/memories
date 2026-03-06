"""Tests for CLI output formatting and TTY detection."""

import io
import json

from cli.output import format_json_envelope, format_error_envelope, OutputFormatter


class TestJsonEnvelope:
    def test_success_envelope_shape(self):
        result = json.loads(format_json_envelope({"count": 5}))
        assert result == {"ok": True, "data": {"count": 5}}

    def test_success_envelope_ok_is_true(self):
        result = json.loads(format_json_envelope({}))
        assert result["ok"] is True

    def test_error_envelope_shape(self):
        result = json.loads(format_error_envelope("bad thing"))
        assert result == {"ok": False, "error": "bad thing", "code": "GENERAL_ERROR"}

    def test_error_envelope_custom_code(self):
        result = json.loads(format_error_envelope("nope", "AUTH_ERROR"))
        assert result["code"] == "AUTH_ERROR"

    def test_error_envelope_ok_is_false(self):
        result = json.loads(format_error_envelope("fail"))
        assert result["ok"] is False


class TestTTYDetection:
    def test_force_json_overrides_tty(self):
        tty = io.StringIO()
        tty.isatty = lambda: True  # type: ignore[assignment]
        fmt = OutputFormatter(force_json=True, stream=tty)
        assert fmt.is_json is True

    def test_force_pretty_overrides_pipe(self):
        pipe = io.StringIO()
        pipe.isatty = lambda: False  # type: ignore[assignment]
        fmt = OutputFormatter(force_pretty=True, stream=pipe)
        assert fmt.is_json is False

    def test_pipe_detected_as_json(self):
        pipe = io.StringIO()
        pipe.isatty = lambda: False  # type: ignore[assignment]
        fmt = OutputFormatter(stream=pipe)
        assert fmt.is_json is True

    def test_tty_detected_as_pretty(self):
        tty = io.StringIO()
        tty.isatty = lambda: True  # type: ignore[assignment]
        fmt = OutputFormatter(stream=tty)
        assert fmt.is_json is False


class TestOutputFormatter:
    def test_success_returns_envelope(self):
        fmt = OutputFormatter(force_json=True)
        result = json.loads(fmt.success({"id": "abc"}))
        assert result["ok"] is True
        assert result["data"]["id"] == "abc"

    def test_error_returns_envelope(self):
        fmt = OutputFormatter(force_json=True)
        result = json.loads(fmt.error("oops", "TEST_ERR"))
        assert result["ok"] is False
        assert result["error"] == "oops"
        assert result["code"] == "TEST_ERR"

    def test_echo_json_mode(self, capsys):
        fmt = OutputFormatter(force_json=True)
        fmt.echo({"x": 1})
        out = capsys.readouterr().out
        assert json.loads(out)["data"]["x"] == 1

    def test_echo_human_mode_calls_fn(self, capsys):
        fmt = OutputFormatter(force_pretty=True)
        fmt.echo({"x": 1}, human_fn=lambda d: print(f"Value: {d['x']}"))
        out = capsys.readouterr().out
        assert "Value: 1" in out

    def test_echo_error_json_mode(self, capsys):
        fmt = OutputFormatter(force_json=True)
        fmt.echo_error("fail", "E")
        err = capsys.readouterr().err
        result = json.loads(err)
        assert result["ok"] is False

    def test_echo_error_pretty_mode(self, capsys):
        fmt = OutputFormatter(force_pretty=True)
        fmt.echo_error("fail")
        err = capsys.readouterr().err
        assert "Error: fail" in err
