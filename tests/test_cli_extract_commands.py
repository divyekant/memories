"""Tests for extract CLI commands using httpx.MockTransport."""

import json

import httpx
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _invoke(args, handler, input=None):
    """Invoke the CLI app with a mock transport backing the client."""
    original_init = MemoriesClient.__init__

    def patched_init(self, url=None, api_key=None, transport=None):
        original_init(self, url=url, api_key=api_key,
                      transport=httpx.MockTransport(handler))

    MemoriesClient.__init__ = patched_init
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["--json"] + args, input=input)
    finally:
        MemoriesClient.__init__ = original_init
    return result


class TestExtractSubmit:
    def test_submit_from_stdin(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "job_id": "abc-123", "status": "submitted",
            })

        result = _invoke(
            ["extract", "submit", "-s", "test/", "-"],
            handler,
            input="Hello, this is a test conversation.\n",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["job_id"] == "abc-123"
        assert isinstance(captured["body"]["messages"], str)
        assert captured["body"]["source"] == "test/"
        assert captured["body"]["context"] == "stop"

    def test_submit_plain_text(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "job_id": "def-456", "status": "submitted",
            })

        result = _invoke(
            ["extract", "submit", "-s", "test/", "--context", "session_end", "-"],
            handler,
            input="User: hello\nAssistant: hi there\n",
        )
        assert result.exit_code == 0
        assert isinstance(captured["body"]["messages"], str)
        assert "hello" in captured["body"]["messages"]
        assert captured["body"]["context"] == "session_end"

    def test_submit_requires_source(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={})

        result = _invoke(
            ["extract", "submit", "-"],
            handler,
            input="text\n",
        )
        assert result.exit_code != 0


class TestExtractStatus:
    def test_status_with_job_id(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "job_id": "abc-123", "status": "completed",
                "result": [{"text": "fact 1"}, {"text": "fact 2"}],
            })

        result = _invoke(["extract", "status", "abc-123"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "completed"
        assert len(data["data"]["result"]) == 2

    def test_status_system(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "provider": "anthropic", "active_jobs": 0,
            })

        result = _invoke(["extract", "status"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["provider"] == "anthropic"


class TestExtractPoll:
    def test_poll_without_wait(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "job_id": "abc-123", "status": "processing",
            })

        result = _invoke(["extract", "poll", "abc-123"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "processing"

    def test_poll_with_wait_completed(self):
        call_count = 0

        def handler(request: httpx.Request):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return httpx.Response(200, json={
                    "job_id": "abc-123", "status": "completed",
                    "result": [{"text": "memory 1"}],
                })
            return httpx.Response(200, json={
                "job_id": "abc-123", "status": "processing",
            })

        result = _invoke(
            ["extract", "poll", "abc-123", "--wait", "--timeout", "10"],
            handler,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["status"] == "completed"
