"""Tests for auto-detect installer target selection."""

import os
import subprocess
from pathlib import Path


INSTALL_SCRIPT = Path(__file__).resolve().parents[1] / "integrations" / "claude-code" / "install.sh"


def _run_installer(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [str(INSTALL_SCRIPT), *args],
        cwd=str(INSTALL_SCRIPT.parent),
        env=env,
        text=True,
        capture_output=True,
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
