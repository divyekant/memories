"""Tests for installer target selection and Codex integration behavior."""

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "integrations" / "claude-code" / "install.sh"


def _prepare_installer_fixture(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT / "integrations" / "claude-code",
        repo_root / "integrations" / "claude-code",
        dirs_exist_ok=True,
    )
    shutil.copytree(
        REPO_ROOT / "integrations" / "codex",
        repo_root / "integrations" / "codex",
        dirs_exist_ok=True,
    )
    mcp_dir = repo_root / "mcp-server"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "index.js").write_text("// installer test fixture\n")
    return repo_root / "integrations" / "claude-code" / "install.sh"


def _write_fake_curl(bin_dir: Path) -> None:
    script = bin_dir / "curl"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if printf '%s\n' "$@" | grep -q '/health'; then
  printf '{"total_memories":7}\n'
  exit 0
fi

printf '{"job_id":"job-1"}\n'
"""
    )
    script.chmod(0o755)


def _run_installer(
    home: Path,
    *args: str,
    install_script: Path = INSTALL_SCRIPT,
    input_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(install_script), *args],
        cwd=str(install_script.parent),
        env=env,
        text=True,
        capture_output=True,
        input=input_text,
        check=False,
    )


def test_auto_detect_defaults_to_claude_when_no_client_dirs(tmp_path: Path) -> None:
    result = _run_installer(tmp_path, "--auto", "--dry-run")
    assert result.returncode == 0
    assert "targets=claude" in result.stdout
    assert "mode=install" in result.stdout


def test_auto_detect_finds_all_supported_targets(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".openclaw" / "skills").mkdir(parents=True)

    result = _run_installer(tmp_path, "--auto", "--dry-run")
    assert result.returncode == 0
    assert "targets=claude,codex,openclaw" in result.stdout


def test_explicit_target_flags_override_auto_detection(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()

    result = _run_installer(tmp_path, "--codex", "--openclaw", "--dry-run", "--uninstall")
    assert result.returncode == 0
    assert "targets=codex,openclaw" in result.stdout
    assert "mode=uninstall" in result.stdout


def test_uninstall_mode_does_not_require_shell_profile_variable(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir(parents=True)

    result = _run_installer(tmp_path, "--claude", "--uninstall")
    assert result.returncode == 0
    assert "unbound variable" not in (result.stderr + result.stdout).lower()


def test_codex_install_writes_standalone_hooks_json(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    result = _run_installer(
        home,
        "--codex",
        install_script=install_script,
        input_text="4\n",
        extra_env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )

    assert result.returncode == 0, result.stderr

    # Hooks config goes to standalone hooks.json (not settings.json)
    hooks_json = json.loads((home / ".codex" / "hooks.json").read_text())
    hooks = hooks_json["hooks"]
    assert (
        hooks["SessionStart"][0]["hooks"][0]["command"]
        == f"{home}/.codex/hooks/memory/memory-recall.sh"
    )
    assert (
        hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        == f"{home}/.codex/hooks/memory/memory-query.sh"
    )
    assert (
        hooks["Stop"][0]["hooks"][0]["command"]
        == f"{home}/.codex/hooks/memory/memory-extract.sh"
    )
    assert (
        hooks["PreToolUse"][0]["hooks"][0]["command"]
        == f"{home}/.codex/hooks/memory/memory-guard.sh"
    )
    assert (
        hooks["PostToolUse"][0]["hooks"][0]["command"]
        == f"{home}/.codex/hooks/memory/memory-observe.sh"
    )

    # settings.json has permissions only (no hooks)
    settings = json.loads((home / ".codex" / "settings.json").read_text())
    assert "hooks" not in settings
    assert "mcp__memories__memory_search" in settings["permissions"]["allow"]

    config_toml = (home / ".codex" / "config.toml").read_text()
    assert "[mcp_servers.memories]" in config_toml
    assert "developer_instructions" in config_toml
    assert "notify =" not in config_toml

    hook_dir = home / ".codex" / "hooks" / "memory"
    assert (hook_dir / "memory-recall.sh").exists()
    assert (hook_dir / "memory-query.sh").exists()
    assert (hook_dir / "memory-extract.sh").exists()
    assert (hook_dir / "memory-guard.sh").exists()
    assert (hook_dir / "memory-observe.sh").exists()
    assert (hook_dir / "_lib.sh").exists()
    assert (hook_dir / "response-hints.json").exists()
