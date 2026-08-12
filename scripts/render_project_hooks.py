#!/usr/bin/env python3
"""Render the repo's `.claude/settings.json` hooks block from the plugin's hooks.json.

Why this exists: a Claude Code cloud session starts from a fresh clone and does
NOT perform the marketplace fetch or plugin install, even when the committed
`.claude/settings.json` declares `extraKnownMarketplaces` and `enabledPlugins`.
Verified in a real cloud container: `installed_plugins.json` was `{"plugins":{}}`,
`~/.claude/plugins/marketplaces/` did not exist, and zero hooks ran. The hook
scripts themselves work there — only the install step is skipped.

So the repo wires the same hooks directly, pointing at the in-repo copies via
$CLAUDE_PROJECT_DIR. That needs no fetch and no bootstrap cooperation.

The wiring is GENERATED rather than hand-maintained: hand-copying 11 events
would fork them from hooks.json and drift silently the next time a hook is
added or a timeout changes. `--check` fails when the committed block no longer
matches, so drift breaks CI instead of quietly disabling a hook in cloud.

Usage:
    python3 scripts/render_project_hooks.py            # rewrite .claude/settings.json
    python3 scripts/render_project_hooks.py --check    # verify, exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS_JSON = REPO / "mcp-server" / "assets" / "claude-code" / "hooks" / "hooks.json"
SETTINGS = REPO / ".claude" / "settings.json"

# The plugin resolves its own root at runtime; a repo-local wiring cannot, so
# rewrite to the in-repo path. Deliberately the canonical directory rather than
# the `plugin/` symlink — one less indirection, and a committed symlink is what
# broke cloud plugin distribution before.
PLUGIN_ROOT = "${CLAUDE_PLUGIN_ROOT}"
HOOKS_DIR_TPL = "${CLAUDE_PROJECT_DIR}/mcp-server/assets/claude-code/hooks"

# Every command is routed through repo-hook.sh, which stands down when the
# plugin is installed. Claude Code runs ALL matching hooks, so an ungated repo
# wiring would double-fire alongside the plugin locally: recall injected twice,
# telemetry double-counted, and two concurrent Stop/SubagentStop extractions
# racing to write the same memories (the hooks have no invocation locking).
LAUNCHER = "repo-hook.sh"

# ConfigChange is deliberately NOT wired. memory-config-guard.sh runs without
# CLAUDE_PLUGIN_ROOT here, takes its legacy path, and checks only
# ~/.claude/settings.json for the hook names — which under repo wiring live in
# the PROJECT settings, so it would emit a false "hooks may be missing"
# warning telling the user to rerun the installer.
EXCLUDED_EVENTS = ("ConfigChange",)


def render_hooks() -> dict:
    """The hooks block `.claude/settings.json` should carry, from hooks.json."""
    source = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    rendered: dict = {}
    for event, entries in source["hooks"].items():
        if event in EXCLUDED_EVENTS:
            continue
        new_entries = []
        for entry in entries:
            new_hooks = []
            for hook in entry.get("hooks", []):
                script = hook["command"].rsplit("/", 1)[-1]
                # Quote the path: a checkout under e.g. "/Users/a/My Projects"
                # would otherwise word-split and every hook would fail with
                # command-not-found.
                new_hooks.append({
                    **hook,
                    "command": f'"{HOOKS_DIR_TPL}/{LAUNCHER}" {script}',
                })
            new_entries.append({**entry, "hooks": new_hooks})
        rendered[event] = new_entries
    for entries in rendered.values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                assert PLUGIN_ROOT not in hook["command"], hook
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()

    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    expected = render_hooks()

    if args.check:
        actual = settings.get("hooks")
        if actual == expected:
            print(f"ok: .claude/settings.json hooks match hooks.json ({len(expected)} events)")
            return 0
        print("drift: .claude/settings.json hooks no longer match hooks.json", file=sys.stderr)
        missing = sorted(set(expected) - set(actual or {}))
        extra = sorted(set(actual or {}) - set(expected))
        if missing:
            print(f"  events missing from settings.json: {missing}", file=sys.stderr)
        if extra:
            print(f"  events not in hooks.json: {extra}", file=sys.stderr)
        print("  regenerate with: python3 scripts/render_project_hooks.py", file=sys.stderr)
        return 1

    settings["hooks"] = expected
    # ensure_ascii=False: em-dashes in neighbouring values must survive a rewrite.
    SETTINGS.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(expected)} hook events into {SETTINGS.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
