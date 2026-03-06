"""Tests for export/import CLI client methods and commands."""

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


class TestExportClient:
    def test_export_stream(self):
        ndjson = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "hello", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ])

        def handler(request: httpx.Request):
            assert request.url.path == "/export"
            return httpx.Response(200, text=ndjson,
                                  headers={"content-type": "application/x-ndjson"})

        client = MemoriesClient(url="http://test", transport=httpx.MockTransport(handler))
        lines = list(client.export_stream())
        assert len(lines) == 2
        assert json.loads(lines[0])["_header"] is True

    def test_export_stream_with_params(self):
        """export_stream passes source, since, until as query params."""
        captured = {}

        def handler(request: httpx.Request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, text='{"_header": true, "count": 0}\n',
                                  headers={"content-type": "application/x-ndjson"})

        client = MemoriesClient(url="http://test", transport=httpx.MockTransport(handler))
        list(client.export_stream(source="proj/", since="2026-01-01", until="2026-02-01"))
        assert captured["params"]["source"] == "proj/"
        assert captured["params"]["since"] == "2026-01-01"
        assert captured["params"]["until"] == "2026-02-01"

    def test_import_upload(self):
        def handler(request: httpx.Request):
            assert request.url.path == "/import"
            assert "add" in str(request.url)
            return httpx.Response(200, json={
                "imported": 2, "skipped": 0, "updated": 0,
                "errors": [], "backup": "pre-import_123",
            })

        client = MemoriesClient(url="http://test", transport=httpx.MockTransport(handler))
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0"}),
            json.dumps({"text": "hi", "source": "s/"}),
        ]
        result = client.import_upload(lines, strategy="add")
        assert result["imported"] == 2

    def test_import_upload_with_options(self):
        """import_upload passes source_remap and no_backup as query params."""
        captured = {}

        def handler(request: httpx.Request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={
                "imported": 1, "skipped": 0, "updated": 0,
                "errors": [], "backup": None,
            })

        client = MemoriesClient(url="http://test", transport=httpx.MockTransport(handler))
        lines = [json.dumps({"_header": True, "count": 0, "version": "2.0.0"})]
        client.import_upload(lines, strategy="smart", source_remap="old/=new/", no_backup=True)
        assert captured["params"]["strategy"] == "smart"
        assert captured["params"]["source_remap"] == "old/=new/"
        assert captured["params"]["no_backup"] == "true"


class TestExportCommand:
    def test_export_to_stdout(self):
        ndjson_lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "hello", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ]

        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                text="\n".join(ndjson_lines),
                headers={"content-type": "application/x-ndjson"},
            )

        result = _invoke(["export"], handler)
        assert result.exit_code == 0
        # JSON mode wraps in envelope
        data = json.loads(result.output)
        assert data["ok"] is True


class TestImportCommand:
    def test_import_from_stdin(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "imported": 1, "skipped": 0, "updated": 0,
                "errors": [], "backup": "pre-import_123",
            })

        ndjson_input = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0"}),
            json.dumps({"text": "hi", "source": "s/"}),
        ]) + "\n"

        result = _invoke(
            ["import", "-"],
            handler,
            input=ndjson_input,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["imported"] == 1
