"""Tests for the repo-local Codex plugin packaging."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "memories" / ".codex-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
MEMORIES_SKILL = REPO_ROOT / "plugins" / "memories" / "skills" / "memories" / "SKILL.md"
SETUP_SKILL = REPO_ROOT / "plugins" / "memories" / "skills" / "setup" / "SKILL.md"
CANONICAL_MEMORIES_SKILL = REPO_ROOT / "plugin" / "skills" / "memories" / "SKILL.md"


def test_codex_plugin_package_is_wired_for_current_release() -> None:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    manifest = json.loads(PLUGIN_MANIFEST.read_text())
    marketplace = json.loads(MARKETPLACE.read_text())

    assert manifest["name"] == "memories"
    assert manifest["version"] == project["version"]
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert "hooks" not in manifest
    assert manifest["interface"]["displayName"] == "Memories"
    assert manifest["interface"]["category"] == "Productivity"

    plugin_entries = [entry for entry in marketplace["plugins"] if entry["name"] == "memories"]
    assert len(plugin_entries) == 1
    assert plugin_entries[0]["source"] == {"source": "local", "path": "./plugins/memories"}
    assert plugin_entries[0]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }


def test_codex_plugin_skills_include_memory_discipline_and_codex_bootstrap() -> None:
    assert MEMORIES_SKILL.read_text() == CANONICAL_MEMORIES_SKILL.read_text()

    setup_text = SETUP_SKILL.read_text()
    assert "./integrations/claude-code/install.sh --codex" in setup_text
    assert "mcp-server/index.js" in setup_text
    assert "~/.codex/hooks.json" in setup_text
