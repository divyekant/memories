"""Tests for eval CLI config resolution."""

from __future__ import annotations

import sys
import builtins
from types import SimpleNamespace
from unittest.mock import MagicMock

import eval.__main__ as eval_main


def test_memories_url_env_overrides_config(tmp_path, monkeypatch):
    """MEMORIES_URL should override the config file so eval can target an isolated stack."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "memories_url: http://localhost:8900",
                "memories_api_key: config-key",
                "judge_provider: anthropic",
                "cc_timeout: 120",
                "category_weights:",
                "  coding: 1.0",
            ]
        )
    )

    fake_scenario = MagicMock()
    fake_memories = MagicMock()
    fake_memories.health_check.return_value = True
    fake_runner = MagicMock()
    fake_runner.run_all.return_value = MagicMock()
    fake_executor = MagicMock()
    memories_ctor = MagicMock(return_value=fake_memories)
    executor_ctor = MagicMock(return_value=fake_executor)
    runner_ctor = MagicMock(return_value=fake_runner)
    mcp_server = tmp_path / "mcp-server.js"
    mcp_server.write_text("console.log('mcp');")

    monkeypatch.setattr(
        sys,
        "argv",
        ["python", "--config", str(config_path)],
    )
    monkeypatch.setenv("MEMORIES_URL", "http://localhost:8901")
    monkeypatch.setenv("MEMORIES_API_KEY", "env-key")
    monkeypatch.setenv("EVAL_MCP_SERVER_PATH", str(mcp_server))
    monkeypatch.delenv("EXTRACT_PROVIDER", raising=False)

    monkeypatch.setattr(eval_main, "load_all_scenarios", lambda *args, **kwargs: [fake_scenario])
    monkeypatch.setattr(eval_main, "MemoriesClient", memories_ctor)
    monkeypatch.setattr(eval_main, "CCExecutor", executor_ctor)
    monkeypatch.setattr(eval_main, "EvalRunner", runner_ctor)
    monkeypatch.setattr(eval_main, "save_report", lambda *args, **kwargs: "report.json")
    monkeypatch.setattr(eval_main, "format_summary", lambda *args, **kwargs: "summary")
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "llm_provider", SimpleNamespace(get_provider=lambda: None))

    eval_main.main()

    assert fake_memories.health_check.called
    assert memories_ctor.call_args.kwargs["url"] == "http://localhost:8901"
    assert memories_ctor.call_args.kwargs["api_key"] == "env-key"
    assert executor_ctor.call_args.kwargs["memories_url"] == "http://localhost:8901"
    assert executor_ctor.call_args.kwargs["memories_api_key"] == "env-key"
    assert executor_ctor.call_args.kwargs["mcp_server_path"] == str(mcp_server)


def test_rejects_production_memories_url(tmp_path, monkeypatch):
    """Eval must not run against the normal local Memories service."""
    config_path = tmp_path / "config.yaml"
    mcp_server = tmp_path / "index.js"
    mcp_server.write_text("console.log('mcp');")
    config_path.write_text(
        "\n".join(
            [
                "memories_url: http://localhost:8900",
                "memories_api_key: config-key",
                "judge_provider: anthropic",
                "cc_timeout: 120",
                f"mcp_server_path: {mcp_server}",
                "category_weights:",
                "  coding: 1.0",
            ]
        )
    )

    fake_scenario = MagicMock()
    monkeypatch.setattr(sys, "argv", ["python", "--config", str(config_path)])
    monkeypatch.delenv("MEMORIES_URL", raising=False)
    monkeypatch.delenv("EVAL_MEMORIES_URL", raising=False)
    monkeypatch.delenv("EVAL_ALLOW_UNSAFE_TARGET", raising=False)
    monkeypatch.setattr(eval_main, "load_all_scenarios", lambda *args, **kwargs: [fake_scenario])

    try:
        eval_main.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected eval setup validation to reject localhost:8900")
