"""Claude Code executor with isolated project environments."""

import json
import os
import shutil
import subprocess
import tempfile


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

    def run_prompt(self, prompt: str, project_dir: str) -> str:
        """Run prompt via claude -p.

        Returns stdout on success.
        On timeout returns '[TIMEOUT]...' message.
        On FileNotFoundError returns '[ERROR] Claude Code CLI not found...'
        """
        cmd = ["claude", "-p", prompt, "--project", project_dir, "--no-input"]
        # Strip env vars that cause Claude Code to detect nesting
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CLAUDE_") and k != "MCP_CONTEXT"
        }
        env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")
        env["HOME"] = os.environ.get("HOME", "")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=project_dir,
                env=env,
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT] Claude Code timed out after {self.timeout}s"
        except FileNotFoundError:
            return "[ERROR] Claude Code CLI not found. Ensure 'claude' is on PATH."
