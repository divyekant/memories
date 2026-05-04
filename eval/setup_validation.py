"""Preflight validation for trusted eval runs."""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
LOCAL_PRODUCTION_PORTS = {8900}
DEFAULT_EVAL_MEMORIES_URL = "http://localhost:8901"


@dataclass
class EvalSetupReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def is_local_production_url(url: str) -> bool:
    """Return True when a URL points at the normal local production service."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return host in LOCAL_HOSTS and parsed.port in LOCAL_PRODUCTION_PORTS


def resolve_eval_memories_url(default: str = DEFAULT_EVAL_MEMORIES_URL) -> str:
    """Resolve the eval target without defaulting to the normal local service."""
    return os.getenv("EVAL_MEMORIES_URL") or os.getenv("MEMORIES_URL") or default


def validate_eval_setup(
    *,
    memories_url: str,
    mcp_server_path: str = "",
    require_mcp: bool = True,
    require_claude: bool = True,
    allow_unsafe_target: bool = False,
) -> EvalSetupReport:
    """Validate static eval setup before network or model work begins."""
    report = EvalSetupReport()

    if not memories_url:
        report.errors.append("MEMORIES_URL is empty; eval target is unknown.")
    elif is_local_production_url(memories_url) and not allow_unsafe_target:
        report.errors.append(
            f"Refusing to run eval against {memories_url}; localhost:8900 is the normal local production service."
        )
    else:
        report.info.append(f"Eval Memories target: {memories_url}")

    if not require_mcp:
        report.info.append("Eval MCP server: not required for this eval mode")
    elif not mcp_server_path:
        report.errors.append("MCP server path is required for trusted with-memory eval runs.")
    elif not Path(mcp_server_path).is_file():
        report.errors.append(f"MCP server path does not exist: {mcp_server_path}")
    else:
        report.info.append(f"Eval MCP server: {mcp_server_path}")

    if require_claude and shutil.which("claude") is None:
        report.errors.append("claude CLI not found in PATH; scenario eval cannot run.")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Memories eval setup")
    parser.add_argument("--memories-url", required=True)
    parser.add_argument("--mcp-server-path", required=True)
    parser.add_argument("--allow-unsafe-target", action="store_true")
    parser.add_argument("--no-claude", action="store_true", help="Skip claude CLI check")
    args = parser.parse_args()

    report = validate_eval_setup(
        memories_url=args.memories_url,
        mcp_server_path=args.mcp_server_path,
        require_claude=not args.no_claude,
        allow_unsafe_target=args.allow_unsafe_target,
    )
    for line in report.info:
        print(f"OK: {line}")
    for line in report.warnings:
        print(f"WARN: {line}")
    for line in report.errors:
        print(f"ERROR: {line}")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
