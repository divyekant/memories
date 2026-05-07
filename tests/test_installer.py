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
    opencode_plugin_dir = repo_root / "integrations" / "opencode" / "plugin"
    opencode_plugin_dir.mkdir(parents=True, exist_ok=True)
    (opencode_plugin_dir / "memories.js").write_text("// installer test fixture\n")
    shutil.copytree(
        REPO_ROOT / "plugin" / "skills" / "memories",
        repo_root / "plugin" / "skills" / "memories",
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
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    (tmp_path / ".openclaw" / "skills").mkdir(parents=True)

    result = _run_installer(tmp_path, "--auto", "--dry-run")
    assert result.returncode == 0
    assert "targets=claude,codex,opencode,openclaw" in result.stdout


def test_auto_detect_finds_opencode_target(tmp_path: Path) -> None:
    (tmp_path / ".config" / "opencode" / "opencode.json").parent.mkdir(parents=True)
    (tmp_path / ".config" / "opencode" / "opencode.json").write_text("{}")

    result = _run_installer(tmp_path, "--auto", "--dry-run")
    assert result.returncode == 0
    assert "targets=opencode" in result.stdout


def test_explicit_target_flags_override_auto_detection(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()

    result = _run_installer(tmp_path, "--codex", "--opencode", "--openclaw", "--dry-run", "--uninstall")
    assert result.returncode == 0
    assert "targets=codex,opencode,openclaw" in result.stdout
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
    assert "replace {project} with the current working directory basename" in config_toml
    assert "Do not use broad family prefixes" in config_toml
    assert "notify =" not in config_toml

    hook_dir = home / ".codex" / "hooks" / "memory"
    assert (hook_dir / "memory-recall.sh").exists()
    assert (hook_dir / "memory-query.sh").exists()
    assert (hook_dir / "memory-extract.sh").exists()
    assert (hook_dir / "memory-guard.sh").exists()
    assert (hook_dir / "memory-observe.sh").exists()
    assert (hook_dir / "_lib.sh").exists()
    assert (hook_dir / "response-hints.json").exists()


def test_opencode_install_writes_mcp_skill_and_plugin_config(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    opencode_dir = home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "mcp": {"other": {"type": "remote", "enabled": True}},
                "plugin": ["/tmp/other-plugin.js"],
                "theme": "system",
            }
        )
    )

    result = _run_installer(
        home,
        "--opencode",
        install_script=install_script,
        extra_env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )

    assert result.returncode == 0, result.stderr
    assert "Skipping extraction provider prompt (no hook targets selected)" in result.stdout

    config = json.loads(opencode_json.read_text())
    assert config["theme"] == "system"
    assert config["mcp"]["other"] == {"type": "remote", "enabled": True}
    assert config["mcp"]["memories"] == {
        "type": "local",
        "enabled": True,
        "timeout": 10000,
        "environment": {"MEMORIES_MANAGED_BY": "memories-opencode-installer"},
        "command": [
            "zsh",
            "-lc",
            f"set -a; [ -f \"$HOME/.config/memories/env\" ] && . \"$HOME/.config/memories/env\"; set +a; exec node \"{repo_root}/mcp-server/index.js\"",
        ],
    }

    plugin_path = f"{repo_root}/integrations/opencode/plugin/memories.js"
    assert config["plugin"] == ["/tmp/other-plugin.js", plugin_path]
    assert (home / ".config" / "opencode" / "skills" / "memories" / "SKILL.md").read_text() == (
        repo_root / "plugin" / "skills" / "memories" / "SKILL.md"
    ).read_text()
    assert (home / ".config" / "opencode" / "skills" / "memories" / ".memories-installer-managed").exists()
    assert not (home / ".claude" / "hooks" / "memory").exists()
    assert not (home / ".codex" / "hooks" / "memory").exists()


def test_opencode_install_preserves_existing_memories_mcp(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    custom_mcp = {
        "type": "remote",
        "enabled": False,
        "url": "https://memories.example.test/mcp",
        "headers": {"Authorization": "Bearer custom"},
    }
    opencode_dir = home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "mcp": {"memories": custom_mcp, "other": {"type": "remote"}},
                "plugin": ["/tmp/other-plugin.js"],
            }
        )
    )

    result = _run_installer(
        home,
        "--opencode",
        install_script=install_script,
        extra_env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )

    assert result.returncode == 0, result.stderr
    config = json.loads(opencode_json.read_text())
    assert config["mcp"]["memories"] == custom_mcp
    assert config["mcp"]["other"] == {"type": "remote"}
    assert config["plugin"] == [
        "/tmp/other-plugin.js",
        f"{repo_root}/integrations/opencode/plugin/memories.js",
    ]
    assert (home / ".config" / "opencode" / "skills" / "memories" / "SKILL.md").exists()


def test_opencode_install_preserves_unmarked_existing_skill(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    opencode_dir = home / ".config" / "opencode"
    skill_dir = opencode_dir / "skills" / "memories"
    skill_dir.mkdir(parents=True, exist_ok=True)
    custom_skill = "# Custom OpenCode Memories Skill\n"
    (skill_dir / "SKILL.md").write_text(custom_skill)
    (opencode_dir / "opencode.json").write_text(json.dumps({"plugin": []}))

    result = _run_installer(
        home,
        "--opencode",
        install_script=install_script,
        extra_env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )

    assert result.returncode == 0, result.stderr
    assert "OpenCode skill already exists without installer marker" in result.stdout
    assert (skill_dir / "SKILL.md").read_text() == custom_skill
    assert not (skill_dir / ".memories-installer-managed").exists()
    config = json.loads((opencode_dir / "opencode.json").read_text())
    assert config["plugin"] == [f"{repo_root}/integrations/opencode/plugin/memories.js"]


def test_opencode_install_replaces_stale_memories_plugin_path(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_curl(bin_dir)

    stale_plugin = "/old/worktree/integrations/opencode/plugin/memories.js"
    current_plugin = f"{repo_root}/integrations/opencode/plugin/memories.js"
    opencode_dir = home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "plugin": ["/tmp/other-plugin.js", stale_plugin, stale_plugin],
                "theme": "system",
            }
        )
    )

    result = _run_installer(
        home,
        "--opencode",
        install_script=install_script,
        extra_env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
    )

    assert result.returncode == 0, result.stderr
    config = json.loads(opencode_json.read_text())
    assert config["plugin"] == ["/tmp/other-plugin.js", current_plugin]


def test_opencode_uninstall_removes_only_memories_entries(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    opencode_dir = home / ".config" / "opencode"
    skill_dir = opencode_dir / "skills" / "memories"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("installed skill\n")
    (skill_dir / ".memories-installer-managed").write_text("managed by Memories installer\n")

    memories_plugin = f"{repo_root}/integrations/opencode/plugin/memories.js"
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "mcp": {
                    "memories": {
                        "type": "local",
                        "enabled": True,
                        "environment": {"MEMORIES_MANAGED_BY": "memories-opencode-installer"},
                        "command": [
                            "zsh",
                            "-lc",
                            f"exec node \"{repo_root}/mcp-server/index.js\"",
                        ],
                    },
                    "other": {"type": "remote", "enabled": True},
                },
                "plugin": ["/tmp/other-plugin.js", memories_plugin],
                "theme": "system",
            }
        )
    )

    result = _run_installer(home, "--opencode", "--uninstall", install_script=install_script)

    assert result.returncode == 0, result.stderr
    config = json.loads(opencode_json.read_text())
    assert config == {
        "mcp": {"other": {"type": "remote", "enabled": True}},
        "plugin": ["/tmp/other-plugin.js"],
        "theme": "system",
    }
    assert not skill_dir.exists()


def test_opencode_uninstall_preserves_unmarked_local_memories_mcp_config(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    opencode_dir = home / ".config" / "opencode"
    skill_dir = opencode_dir / "skills" / "memories"
    skill_dir.mkdir(parents=True, exist_ok=True)
    custom_skill = "custom local skill\n"
    (skill_dir / "SKILL.md").write_text(custom_skill)

    custom_mcp = {
        "type": "local",
        "enabled": True,
        "timeout": 30000,
        "command": ["node", f"{repo_root}/mcp-server/index.js"],
        "environment": {"MEMORIES_URL": "https://memories.example.test"},
    }
    memories_plugin = f"{repo_root}/integrations/opencode/plugin/memories.js"
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "mcp": {
                    "memories": custom_mcp,
                    "other": {"type": "remote", "enabled": True},
                },
                "plugin": ["/tmp/other-plugin.js", memories_plugin],
                "theme": "system",
            }
        )
    )

    result = _run_installer(home, "--opencode", "--uninstall", install_script=install_script)

    assert result.returncode == 0, result.stderr
    config = json.loads(opencode_json.read_text())
    assert config == {
        "mcp": {
            "memories": custom_mcp,
            "other": {"type": "remote", "enabled": True},
        },
        "plugin": ["/tmp/other-plugin.js"],
        "theme": "system",
    }
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").read_text() == custom_skill


def test_opencode_uninstall_preserves_custom_memories_mcp_config(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    opencode_dir = home / ".config" / "opencode"
    skill_dir = opencode_dir / "skills" / "memories"
    skill_dir.mkdir(parents=True, exist_ok=True)
    custom_skill = "custom remote skill\n"
    (skill_dir / "SKILL.md").write_text(custom_skill)

    custom_mcp = {
        "type": "remote",
        "enabled": False,
        "url": "https://memories.example.test/mcp",
        "headers": {"Authorization": "Bearer custom"},
    }
    memories_plugin = f"{repo_root}/integrations/opencode/plugin/memories.js"
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "mcp": {
                    "memories": custom_mcp,
                    "other": {"type": "remote", "enabled": True},
                },
                "plugin": ["/tmp/other-plugin.js", memories_plugin],
                "theme": "system",
            }
        )
    )

    result = _run_installer(home, "--opencode", "--uninstall", install_script=install_script)

    assert result.returncode == 0, result.stderr
    config = json.loads(opencode_json.read_text())
    assert config == {
        "mcp": {
            "memories": custom_mcp,
            "other": {"type": "remote", "enabled": True},
        },
        "plugin": ["/tmp/other-plugin.js"],
        "theme": "system",
    }
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").read_text() == custom_skill


def test_opencode_uninstall_removes_stale_memories_plugin_paths(tmp_path: Path) -> None:
    install_script = _prepare_installer_fixture(tmp_path)
    repo_root = install_script.parents[2]
    home = tmp_path / "home"
    opencode_dir = home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    current_plugin = f"{repo_root}/integrations/opencode/plugin/memories.js"
    stale_plugin = "/old/worktree/integrations/opencode/plugin/memories.js"
    opencode_json = opencode_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "plugin": [
                    "/tmp/other-plugin.js",
                    stale_plugin,
                    current_plugin,
                    "/tmp/memories-but-not-opencode.js",
                ],
                "theme": "system",
            }
        )
    )

    result = _run_installer(home, "--opencode", "--uninstall", install_script=install_script)

    assert result.returncode == 0, result.stderr
    config = json.loads(opencode_json.read_text())
    assert config == {
        "plugin": ["/tmp/other-plugin.js", "/tmp/memories-but-not-opencode.js"],
        "theme": "system",
    }
