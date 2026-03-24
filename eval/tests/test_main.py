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

    monkeypatch.setattr(
        sys,
        "argv",
        ["python", "--config", str(config_path)],
    )
    monkeypatch.setenv("MEMORIES_URL", "http://localhost:8901")
    monkeypatch.setenv("MEMORIES_API_KEY", "env-key")
    monkeypatch.setenv("EVAL_MCP_SERVER_PATH", "/tmp/mcp-server.js")
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
