"""Preflight validation for trusted eval runs."""

from __future__ import annotations

import argparse
import importlib.util as importlib_util
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
DEFAULT_LOCAL_PRODUCTION_PORTS = {8900}
DEFAULT_EVAL_MEMORIES_URL = "http://localhost:8901"
JUDGE_PROVIDER_MODULES = {
    "anthropic": "anthropic",
    "openai": "openai",
}
JUDGE_PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "chatgpt-subscription": "CHATGPT_REFRESH_TOKEN",
}


@dataclass
class EvalSetupReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _local_production_ports() -> set[int]:
    raw = os.getenv("EVAL_LOCAL_PRODUCTION_PORTS", "")
    ports = set(DEFAULT_LOCAL_PRODUCTION_PORTS)
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ports.add(int(item))
        except ValueError:
            continue
    return ports


def is_local_production_url(url: str) -> bool:
    """Return True when a URL points at the normal local production service."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return host in LOCAL_HOSTS and parsed.port in _local_production_ports()


def resolve_eval_memories_url(default: str = DEFAULT_EVAL_MEMORIES_URL) -> str:
    """Resolve the eval target without defaulting to the normal local service."""
    return os.getenv("EVAL_MEMORIES_URL") or default


def validate_eval_setup(
    *,
    memories_url: str,
    api_key: str | None = None,
    require_api_key: bool = False,
    mcp_server_path: str = "",
    require_mcp: bool = True,
    require_claude: bool = True,
    require_judge: bool = False,
    judge_provider: str = "",
    allow_unsafe_target: bool = False,
) -> EvalSetupReport:
    """Validate static eval setup before network or model work begins."""
    report = EvalSetupReport()

    if not memories_url:
        report.errors.append("MEMORIES_URL is empty; eval target is unknown.")
    elif not allow_unsafe_target and (urlparse(memories_url).hostname or "") not in LOCAL_HOSTS:
        report.errors.append(
            f"Refusing to run eval against non-local target {memories_url}; set EVAL_MEMORIES_URL to the isolated eval service."
        )
    elif is_local_production_url(memories_url) and not allow_unsafe_target:
        report.errors.append(
            f"Refusing to run eval against {memories_url}; localhost:8900 is the normal local production service."
        )
    else:
        report.info.append(f"Eval Memories target: {memories_url}")

    if api_key is None:
        api_key = os.getenv("MEMORIES_API_KEY", "")
    if require_api_key and not api_key.strip():
        report.errors.append("MEMORIES_API_KEY is required for trusted eval runs.")
    elif api_key.strip():
        report.info.append("Eval API key: set")
    else:
        report.warnings.append("Eval API key: not set")

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

    if require_judge:
        provider = judge_provider.strip().lower()
        if not provider:
            report.errors.append("Judge provider is required for this eval run.")
        else:
            known_providers = set(JUDGE_PROVIDER_ENV_KEYS) | set(JUDGE_PROVIDER_MODULES)
            if provider not in known_providers:
                report.errors.append(
                    f"Unknown judge provider {provider}; supported providers: {', '.join(sorted(known_providers))}."
                )
            env_key = JUDGE_PROVIDER_ENV_KEYS.get(provider)
            if env_key and not os.getenv(env_key, "").strip():
                report.errors.append(f"{env_key} is required when judge_provider={provider}.")
            module = JUDGE_PROVIDER_MODULES.get(provider)
            if module and importlib_util.find_spec(module) is None:
                report.errors.append(
                    f"{module} package required for judge_provider={provider}. "
                    "Install eval dependencies with: uv sync --extra extract"
                )
            if report.ok:
                report.info.append(f"Eval judge: {provider}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Memories eval setup")
    parser.add_argument("--memories-url", required=True)
    parser.add_argument("--mcp-server-path", required=True)
    parser.add_argument("--require-api-key", action="store_true")
    parser.add_argument("--allow-unsafe-target", action="store_true")
    parser.add_argument("--no-claude", action="store_true", help="Skip claude CLI check")
    parser.add_argument("--require-judge", action="store_true")
    parser.add_argument("--judge-provider", default="anthropic")
    args = parser.parse_args()

    report = validate_eval_setup(
        memories_url=args.memories_url,
        api_key=os.getenv("MEMORIES_API_KEY", ""),
        require_api_key=args.require_api_key,
        mcp_server_path=args.mcp_server_path,
        require_claude=not args.no_claude,
        require_judge=args.require_judge,
        judge_provider=args.judge_provider,
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
