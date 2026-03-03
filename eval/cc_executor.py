"""Claude Code executor with isolated project environments."""

import json
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger("eval.cc")


class CCExecutor:
    """Runs prompts through Claude Code in isolated temp projects."""

    def __init__(
        self,
        timeout: int = 120,
        memories_url: str = "http://localhost:8900",
        memories_api_key: str = "",
        mcp_server_path: str = "",
    ):
        self.timeout = timeout
        self.memories_url = memories_url
        self.memories_api_key = memories_api_key
        self.mcp_server_path = mcp_server_path

    def create_isolated_project(self, with_memories: bool = False) -> str:
        """Create temp dir as isolated CC project.

        No CLAUDE.md, no .claude/ dir.
        If with_memories=True and mcp_server_path set, writes .mcp.json
        to enable Memories MCP.
        """
        project_dir = tempfile.mkdtemp(prefix="cc_eval_")

        if with_memories and self.mcp_server_path:
            mcp_config = {
                "mcpServers": {
                    "memories": {
                        "command": "node",
                        "args": [self.mcp_server_path],
                        "env": {
                            "MEMORIES_URL": self.memories_url,
                            "MEMORIES_API_KEY": self.memories_api_key,
                        },
                    }
                }
            }
            mcp_path = os.path.join(project_dir, ".mcp.json")
            with open(mcp_path, "w") as f:
                json.dump(mcp_config, f, indent=2)

        return project_dir

    def cleanup_project(self, project_dir: str) -> None:
        """Remove temp project dir."""
        shutil.rmtree(project_dir, ignore_errors=True)

    def _create_clean_home(self) -> str:
        """Create temp HOME with Claude config but no MCP servers.

        Copies ~/.claude.json and ~/.claude/ with MCP configs neutralized:
        - ~/.claude.json: mcpServers key removed
        - ~/.claude/.mcp.json: overwritten with empty mcpServers
        - ~/.claude/settings.json: mcpServers key removed
        - All other config files and directories copied as-is
        - Large data dirs (debug, file-history, paste-cache) skipped

        Does NOT modify the user's real HOME — only the subprocess sees this.
        """
        real_home = os.environ.get("HOME", "")
        clean_home = tempfile.mkdtemp(prefix="cc_eval_home_")

        # Copy ~/.claude.json without mcpServers
        claude_json = os.path.join(real_home, ".claude.json")
        if os.path.exists(claude_json):
            with open(claude_json) as f:
                config = json.load(f)
            config.pop("mcpServers", None)
            with open(os.path.join(clean_home, ".claude.json"), "w") as f:
                json.dump(config, f, indent=2)

        # Copy ~/.claude/ directory, neutralizing MCP configs
        claude_dir = os.path.join(real_home, ".claude")
        clean_claude_dir = os.path.join(clean_home, ".claude")
        # Skip large data dirs not needed for claude -p
        skip_dirs = {"debug", "file-history", "paste-cache", "backups",
                     "downloads", "plans", "projects"}
        if os.path.isdir(claude_dir):
            os.makedirs(clean_claude_dir, exist_ok=True)
            for name in os.listdir(claude_dir):
                src = os.path.join(claude_dir, name)
                dst = os.path.join(clean_claude_dir, name)
                if os.path.isdir(src):
                    if name not in skip_dirs:
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                elif os.path.isfile(src):
                    if name == "history.jsonl":
                        continue  # large, not needed
                    shutil.copy2(src, dst)

            # Overwrite .mcp.json with empty MCP config
            with open(os.path.join(clean_claude_dir, ".mcp.json"), "w") as f:
                json.dump({"mcpServers": {}}, f)

            # Strip mcpServers from settings.json if present
            settings = os.path.join(clean_claude_dir, "settings.json")
            if os.path.exists(settings):
                with open(settings) as f:
                    sconfig = json.load(f)
                if "mcpServers" in sconfig:
                    sconfig.pop("mcpServers")
                    with open(settings, "w") as f:
                        json.dump(sconfig, f, indent=2)

        return clean_home

    def run_prompt(self, prompt: str, project_dir: str) -> str:
        """Run prompt via claude -p with isolated HOME (no global MCP).

        Uses a temp HOME stripped of MCP server config so only the
        project-level .mcp.json (if any) provides MCP access.
        The user's real HOME is never modified.
        """
        cmd = ["claude", "--dangerously-skip-permissions", "-p", prompt]
        # Strip env vars that cause Claude Code to detect nesting
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CLAUDE_") and k != "MCP_CONTEXT"
        }
        real_home = os.environ.get("HOME", "")
        clean_home = self._create_clean_home()
        env["PATH"] = os.environ.get("PATH", f"{real_home}/.local/bin:/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = clean_home
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=project_dir,
                env=env,
            )
            output = result.stdout.strip()
            logger.debug("claude exit=%d stdout=%d chars stderr=%d chars",
                         result.returncode, len(output), len(result.stderr or ""))
            if result.stderr:
                logger.debug("stderr: %s", result.stderr[:500])
            if not output and result.stderr:
                return f"[STDERR] {result.stderr.strip()}"
            return output
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT] Claude Code timed out after {self.timeout}s"
        except FileNotFoundError:
            return "[ERROR] Claude Code CLI not found. Ensure 'claude' is on PATH."
        finally:
            shutil.rmtree(clean_home, ignore_errors=True)
