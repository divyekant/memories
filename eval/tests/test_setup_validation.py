"""Tests for eval setup validation."""

from __future__ import annotations

from pathlib import Path

from eval.setup_validation import is_local_production_url, resolve_eval_memories_url, validate_eval_setup


def test_localhost_8900_is_rejected_as_production_target() -> None:
    assert is_local_production_url("http://localhost:8900")
    assert is_local_production_url("http://127.0.0.1:8900")
    assert is_local_production_url("http://0.0.0.0:8900")
    assert not is_local_production_url("http://localhost:8901")


def test_validation_rejects_production_target(tmp_path: Path) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")

    report = validate_eval_setup(
        memories_url="http://localhost:8900",
        mcp_server_path=str(mcp_server),
        require_claude=False,
    )

    assert not report.ok
    assert any("localhost:8900" in error for error in report.errors)


def test_validation_rejects_remote_eval_target_by_default(tmp_path: Path) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")

    report = validate_eval_setup(
        memories_url="https://memory.divyekant.com",
        api_key="eval-key",
        require_api_key=True,
        mcp_server_path=str(mcp_server),
        require_claude=False,
    )

    assert not report.ok
    assert any("non-local" in error for error in report.errors)


def test_validation_requires_mcp_server_path_for_trusted_eval() -> None:
    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        mcp_server_path="",
        require_claude=False,
    )

    assert not report.ok
    assert any("MCP server path" in error for error in report.errors)


def test_validation_can_skip_mcp_for_tool_only_eval() -> None:
    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        require_mcp=False,
        require_claude=False,
    )

    assert report.ok


def test_validation_rejects_missing_anthropic_judge_dependency(tmp_path: Path, monkeypatch) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("eval.setup_validation.importlib_util.find_spec", lambda name: None)

    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        mcp_server_path=str(mcp_server),
        require_claude=False,
        require_judge=True,
        judge_provider="anthropic",
    )

    assert not report.ok
    assert any("anthropic package required" in error for error in report.errors)


def test_validation_rejects_missing_anthropic_judge_api_key(tmp_path: Path, monkeypatch) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        mcp_server_path=str(mcp_server),
        require_claude=False,
        require_judge=True,
        judge_provider="anthropic",
    )

    assert not report.ok
    assert any("ANTHROPIC_API_KEY" in error for error in report.errors)


def test_validation_requires_eval_api_key_when_requested(tmp_path: Path) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")

    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        mcp_server_path=str(mcp_server),
        require_claude=False,
        require_api_key=True,
        api_key="",
    )

    assert not report.ok
    assert any("MEMORIES_API_KEY" in error for error in report.errors)


def test_validation_records_api_key_presence_without_leaking_value(tmp_path: Path) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")

    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        mcp_server_path=str(mcp_server),
        require_claude=False,
        require_api_key=True,
        api_key="secret-eval-key",
    )

    assert report.ok
    assert any("Eval API key: set" in info for info in report.info)
    assert "secret-eval-key" not in "\n".join(report.info + report.warnings + report.errors)


def test_eval_url_resolution_prefers_eval_env(monkeypatch) -> None:
    monkeypatch.setenv("MEMORIES_URL", "http://localhost:8900")
    monkeypatch.setenv("EVAL_MEMORIES_URL", "http://localhost:8901")

    assert resolve_eval_memories_url() == "http://localhost:8901"


def test_eval_url_resolution_ignores_default_memories_url(monkeypatch) -> None:
    monkeypatch.setenv("MEMORIES_URL", "http://memory.divyekant.com")
    monkeypatch.delenv("EVAL_MEMORIES_URL", raising=False)

    assert resolve_eval_memories_url() == "http://localhost:8901"


def test_validation_accepts_isolated_eval_target(tmp_path: Path) -> None:
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")

    report = validate_eval_setup(
        memories_url="http://localhost:8901",
        mcp_server_path=str(mcp_server),
        require_claude=False,
    )

    assert report.ok
    assert report.errors == []
