"""Generic MCP stdio smoke tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER_DIR = ROOT / "mcp-server"


def test_generic_mcp_stdio_client_lists_and_calls_tools() -> None:
    result = subprocess.run(
        ["npm", "run", "smoke"],
        cwd=MCP_SERVER_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "generic_mcp_stdio_smoke=ok" in result.stdout


def test_mcp_refuses_redirecting_backend_url() -> None:
    result = subprocess.run(
        ["npm", "run", "smoke:redirect"],
        cwd=MCP_SERVER_DIR,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "redirect_guard_smoke=ok" in result.stdout
