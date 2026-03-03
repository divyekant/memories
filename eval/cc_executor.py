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
        If with_memories=True, stores MCP config JSON string for use
        with --strict-mcp-config at runtime.
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
        """Remove temp project dir and its Claude Code auto-memory."""
        shutil.rmtree(project_dir, ignore_errors=True)
        self._cleanup_auto_memory(project_dir)

    def _cleanup_auto_memory(self, project_dir: str) -> None:
        """Remove Claude Code auto-memory for a project dir.

        Claude Code stores per-project context in ~/.claude/projects/<mangled-path>/.
        The mangled path is the absolute path with / replaced by - .
        """
        abs_path = os.path.realpath(project_dir)
        mangled = "-" + abs_path.strip("/").replace("/", "-")
        auto_memory_dir = os.path.join(
            os.environ.get("HOME", ""), ".claude", "projects", mangled
        )
        if os.path.isdir(auto_memory_dir):
            shutil.rmtree(auto_memory_dir, ignore_errors=True)
            logger.debug("Cleaned auto-memory: %s", auto_memory_dir)

    @staticmethod
    def cleanup_stale_auto_memory() -> None:
        """Remove all stale cc_eval auto-memory from ~/.claude/projects/.

        Call at eval startup to purge leftover auto-memory from prior runs.
        Claude Code mangles paths: underscores become hyphens, so we match both
        'cc_eval' and 'cc-eval' patterns.
        """
        projects_dir = os.path.join(
            os.environ.get("HOME", ""), ".claude", "projects"
        )
        if not os.path.isdir(projects_dir):
            return
        count = 0
        for entry in os.listdir(projects_dir):
            if "cc_eval" in entry or "cc-eval" in entry:
                shutil.rmtree(os.path.join(projects_dir, entry), ignore_errors=True)
                count += 1
        if count:
            logger.info("Cleaned %d stale cc_eval auto-memory dirs", count)

    def run_prompt(self, prompt: str, project_dir: str) -> str:
        """Run prompt via claude -p with strict MCP isolation.

        Uses --strict-mcp-config to override ALL MCP configurations:
        - If .mcp.json exists in project_dir (with-memory run):
          loads that config exclusively
        - If no .mcp.json (without-memory run):
          passes empty config — no MCP servers available at all
        """
        mcp_path = os.path.join(project_dir, ".mcp.json")
        if os.path.exists(mcp_path):
            mcp_arg = mcp_path
        else:
            mcp_arg = json.dumps({"mcpServers": {}})

        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "--strict-mcp-config",
            "--mcp-config", mcp_arg,
            "-p", prompt,
        ]
        # Strip env vars that cause Claude Code to detect nesting
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CLAUDE_") and k != "MCP_CONTEXT"
        }
        home = os.environ.get("HOME", "")
        env["PATH"] = os.environ.get("PATH", f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = home
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
