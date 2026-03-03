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

        Always writes .mcp.json:
        - with_memories=True: configures the Memories MCP server
        - with_memories=False: disables the memories server by name so
          any global config is overridden and the run is truly memory-free
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
        else:
            # Disable memories MCP by name — overrides any global config
            mcp_config = {
                "mcpServers": {
                    "memories": {
                        "disabled": True,
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

    def run_prompt(self, prompt: str, project_dir: str) -> str:
        """Run prompt via claude -p.

        Returns stdout on success.
        On timeout returns '[TIMEOUT]...' message.
        On FileNotFoundError returns '[ERROR] Claude Code CLI not found...'
        """
        cmd = ["claude", "--dangerously-skip-permissions", "-p", prompt]
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
