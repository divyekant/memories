"""Tests for the repo-local Codex plugin packaging."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "memories" / ".codex-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
MEMORIES_SKILL = REPO_ROOT / "plugins" / "memories" / "skills" / "memories" / "SKILL.md"
SETUP_SKILL = REPO_ROOT / "plugins" / "memories" / "skills" / "setup" / "SKILL.md"
CANONICAL_MEMORIES_SKILL = REPO_ROOT / "mcp-server" / "assets" / "claude-code" / "skills" / "memories" / "SKILL.md"


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
    assert "npm installer" in manifest["description"].lower()
    assert "npm installer" in manifest["interface"]["longDescription"].lower()
    assert "from this repository checkout" not in manifest["interface"]["longDescription"].lower()

    plugin_entries = [entry for entry in marketplace["plugins"] if entry["name"] == "memories"]
    assert len(plugin_entries) == 1
    assert plugin_entries[0]["source"] == {"source": "local", "path": "./plugins/memories"}
    assert plugin_entries[0]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }


def test_codex_plugin_skills_include_memory_discipline_and_codex_bootstrap() -> None:
    assert not MEMORIES_SKILL.parent.is_symlink()
    assert MEMORIES_SKILL.read_text() == CANONICAL_MEMORIES_SKILL.read_text()

    setup_text = SETUP_SKILL.read_text()
    assert "npx -y memories-mcp@latest init --codex" in setup_text
    assert "npx -y memories-mcp@latest init --codex --mcp-url https://... --yes" in setup_text
    assert "codex mcp login memories" in setup_text
    assert "integrations/claude-code/install.sh" not in setup_text
    assert "mcp-server/index.js" not in setup_text
    assert "npm --prefix" not in setup_text
    assert re.search(r"\bnpm install\b", setup_text) is None
    assert "MEMORIES_API_KEY =" not in setup_text
    assert "~/.codex/hooks.json" in setup_text


def test_codex_plugin_copy_is_self_contained(tmp_path: Path) -> None:
    copied = tmp_path / "plugins" / "memories"
    shutil.copytree(REPO_ROOT / "plugins" / "memories", copied)

    assert (copied / ".codex-plugin" / "plugin.json").is_file()
    assert (copied / "skills" / "setup" / "SKILL.md").is_file()
    copied_skill = copied / "skills" / "memories" / "SKILL.md"
    assert copied_skill.is_file()
    assert not (copied / "skills" / "memories").is_symlink()
    assert not copied_skill.is_symlink()
    assert copied_skill.read_bytes() == CANONICAL_MEMORIES_SKILL.read_bytes()


def test_codex_shipped_guides_describe_current_installer_and_lifecycle() -> None:
    skill = CANONICAL_MEMORIES_SKILL.read_text()
    quickstart = (REPO_ROOT / "integrations" / "QUICKSTART-LLM.md").read_text()
    codex_quickstart = quickstart.split("## Setup for Codex", 1)[1].split("## Setup for OpenCode", 1)[0]

    for text in (skill, codex_quickstart):
        assert "npx -y memories-mcp@latest init --codex" in text
        assert "--mcp-url https://" in text
        assert "codex mcp login memories" in text
        assert "0.146.0" in text
        assert "ten" in text.lower() or "10" in text
        assert "five" in text.lower() or "5" in text
        assert "SessionEnd" in text and "PostCompact" in text
        assert "config.toml" in text
        assert "six" in text.lower() or "6" in text
        assert "memory_is_useful" in text and "prompt-gated" in text.lower()
        assert "native" in text.lower() and "optional" in text.lower()

    assert "integrations/claude-code/install.sh --codex" in codex_quickstart
    assert "not the canonical" in codex_quickstart
    assert "does not write `~/.codex/settings.json`" in skill
    assert "does not write\n`~/.codex/settings.json`" in codex_quickstart
    assert "No repository checkout is required" in codex_quickstart


def test_codex_docs_describe_published_installer_and_current_lifecycle() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    getting_started = (REPO_ROOT / "GETTING_STARTED.md").read_text()
    architecture = (REPO_ROOT / "docs" / "architecture.md").read_text()
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text()

    for text in (readme, getting_started):
        assert "npx -y memories-mcp@latest init --codex" in text
        assert "npx -y memories-mcp@latest init --codex --mcp-url https://" in text
        assert "codex mcp login memories" in text
        assert "npm package has not been published" not in text.lower()
        assert "has not published its first npm release" not in text.lower()

    codex_docs = f"{readme}\n{getting_started}\n{architecture}"
    assert "0.146.0" in codex_docs
    assert "ten" in codex_docs.lower() or "10" in codex_docs
    assert "five" in codex_docs.lower() or "5" in codex_docs
    assert "PostCompact" in codex_docs and "suppressOutput" in codex_docs
    assert "SessionStart(source=compact)" in codex_docs
    assert "one" in codex_docs.lower() and "max-time 2" in codex_docs
    assert "timeout exactly 3" in codex_docs.lower() or "timeout of 3" in codex_docs.lower()
    assert "memory_is_useful" in codex_docs and "prompt-gated" in codex_docs.lower()
    assert "disable_on_external_context = true" in codex_docs
    assert "installer never sets" in codex_docs.lower()
    assert "external" in codex_docs.lower() and "cross-client" in codex_docs.lower()
    assert "v5.10-v5.12 reliability parity" in codex_docs
    assert "payload cwd" in codex_docs.lower()
    assert "per-backend breaker isolation" in codex_docs.lower()
    assert "partial" in codex_docs.lower() and "401" in codex_docs
    assert "## [Unreleased]" in changelog
