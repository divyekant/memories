"""Claude Code executor with isolated project environments."""

import json
import logging
import os
import shutil
import shlex
import subprocess
import tempfile

logger = logging.getLogger("eval.cc")


class CCExecutor:
    """Runs prompts through Claude Code in isolated temp projects."""

    def __init__(
        self,
        timeout: int = 120,
        memories_url: str = "http://localhost:8901",
        memories_api_key: str = "",
        mcp_server_path: str = "",
        model: str = "",
        capture_trace: bool = False,
    ):
        self.timeout = timeout
        self.memories_url = memories_url
        self.memories_api_key = memories_api_key
        self.model = model
        self.mcp_server_path = mcp_server_path
        self.capture_trace = capture_trace
        self.last_run_trace: dict = {}

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
                            # Force single-backend mode: setting MEMORIES_BACKENDS_FILE
                            # (even to a nonexistent path) tells the MCP server to skip
                            # project/global config resolution and fall back to
                            # MEMORIES_URL/MEMORIES_API_KEY env vars only.
                            "MEMORIES_BACKENDS_FILE": "__eval_single_backend__",
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

    def _write_hook_env_file(self, project_dir: str, with_memories: bool) -> str:
        """Write an eval-scoped env file for global hooks that may still fire."""
        env_path = os.path.join(project_dir, ".memories-eval-env")
        values = {
            "MEMORIES_URL": self.memories_url,
            "MEMORIES_API_KEY": self.memories_api_key,
            "MEMORIES_BACKENDS_FILE": "__eval_single_backend__",
            "MEMORIES_DISABLED": "0" if with_memories else "1",
            "MEMORIES_LOG": os.path.join(project_dir, ".memories-hook.log"),
        }
        with open(env_path, "w") as f:
            for key, value in values.items():
                f.write(f"export {key}={shlex.quote(str(value))}\n")
        os.chmod(env_path, 0o600)
        return env_path

    def reset_project(self, project_dir: str) -> None:
        """Scrub Claude Code auto-memory for a reusable project dir.

        Call between questions when reusing the same project dir across
        multiple prompts. Removes per-project state without destroying
        the project dir itself (preserves .mcp.json etc).
        """
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

    @staticmethod
    def _classify_agent_output(text: str, stderr: str = "", returncode: int | None = None) -> str:
        """Classify infrastructure failures separately from bad answers."""
        combined = f"{text}\n{stderr}".strip().lower()
        if not text.strip() and returncode not in (0, None):
            return "agent_error"
        if text.startswith("[TIMEOUT]"):
            return "timeout"
        if "invalid api key" in combined or "authentication_error" in combined:
            return "auth_error"
        if text.startswith("[STDERR]"):
            return "agent_stderr"
        if text.startswith("[ERROR]"):
            return "agent_error"
        if returncode not in (0, None):
            return "agent_error"
        return ""

    @staticmethod
    def _content_blocks(event: dict) -> list[dict]:
        """Return assistant/user content blocks from Claude stream-json events."""
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                return [b for b in content if isinstance(b, dict)]
        content = event.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
        return []

    @staticmethod
    def _summarize_tool_input(tool_input) -> dict:
        """Keep proof-useful tool input fields without copying full payloads."""
        if not isinstance(tool_input, dict):
            return {"input_keys": []}
        summary = {"input_keys": sorted(str(k) for k in tool_input.keys())}
        for key in ("query", "source_prefix", "reference_date", "memory_id"):
            if key in tool_input:
                summary[key] = str(tool_input[key])[:300]
        if "ids" in tool_input and isinstance(tool_input["ids"], list):
            summary["ids_count"] = len(tool_input["ids"])
        return summary

    @classmethod
    def _parse_stream_json_trace(
        cls,
        stdout: str,
        stderr: str = "",
        returncode: int | None = None,
    ) -> tuple[str, dict]:
        """Parse Claude Code stream-json into a final answer and audit trace."""
        trace = {
            "output_format": "stream-json",
            "event_count": 0,
            "parse_errors": 0,
            "tool_calls": [],
            "tool_results": 0,
            "result": {},
            "duration_ms": None,
            "stdout_chars": len(stdout or ""),
            "stderr_chars": len(stderr or ""),
            "stderr_excerpt": (stderr or "")[:1000],
            "returncode": returncode,
            "error_kind": "",
        }
        assistant_text: list[str] = []
        result_text = ""

        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                trace["parse_errors"] += 1
                continue
            if not isinstance(event, dict):
                continue
            trace["event_count"] += 1

            if event.get("type") == "result":
                trace["result"] = {
                    key: event.get(key)
                    for key in ("type", "subtype", "is_error", "num_turns", "total_cost_usd")
                    if key in event
                }
                if "duration_ms" in event:
                    trace["duration_ms"] = event.get("duration_ms")
                if isinstance(event.get("result"), str):
                    result_text = event["result"].strip()
                if event.get("is_error"):
                    trace["error_kind"] = "agent_error"

            for block in cls._content_blocks(event):
                block_type = block.get("type")
                if block_type == "tool_use":
                    tool = {
                        "id": str(block.get("id", "")),
                        "name": str(block.get("name", "")),
                    }
                    tool.update(cls._summarize_tool_input(block.get("input")))
                    trace["tool_calls"].append(tool)
                elif block_type == "tool_result":
                    trace["tool_results"] += 1
                elif block_type == "text" and isinstance(block.get("text"), str):
                    assistant_text.append(block["text"])

        text = result_text or "\n".join(t.strip() for t in assistant_text if t.strip()).strip()
        if not text and stdout and trace["parse_errors"]:
            text = stdout.strip()
        trace["error_kind"] = trace["error_kind"] or cls._classify_agent_output(text, stderr, returncode)
        return text, trace

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
            with_memories = True
        else:
            mcp_arg = json.dumps({"mcpServers": {}})
            with_memories = False

        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "--strict-mcp-config",
            "--mcp-config", mcp_arg,
        ]
        if self.capture_trace:
            cmd.extend(["--output-format", "stream-json", "--verbose"])
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.extend(["-p", prompt])
        # Strip env vars that cause Claude Code to detect nesting
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CLAUDE_") and k != "MCP_CONTEXT"
        }
        home = os.environ.get("HOME", "")
        env["PATH"] = os.environ.get("PATH", f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = home
        hook_env_file = self._write_hook_env_file(project_dir, with_memories=with_memories)
        env["MEMORIES_URL"] = self.memories_url
        env["MEMORIES_API_KEY"] = self.memories_api_key
        env["MEMORIES_BACKENDS_FILE"] = "__eval_single_backend__"
        env["MEMORIES_ENV_FILE"] = hook_env_file
        env["MEMORIES_LOG"] = os.path.join(project_dir, ".memories-hook.log")
        env["MEMORIES_DISABLED"] = "0" if with_memories else "1"
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=project_dir,
                env=env,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            output = stdout.strip()
            logger.debug("claude exit=%d stdout=%d chars stderr=%d chars",
                         result.returncode, len(output), len(stderr))
            if stderr:
                logger.debug("stderr: %s", stderr[:500])
            if self.capture_trace:
                output, self.last_run_trace = self._parse_stream_json_trace(
                    stdout,
                    stderr=stderr,
                    returncode=result.returncode,
                )
                output = output.strip()
            else:
                self.last_run_trace = {
                    "output_format": "text",
                    "stdout_chars": len(stdout),
                    "stderr_chars": len(stderr),
                    "stderr_excerpt": stderr[:1000],
                    "returncode": result.returncode,
                    "error_kind": self._classify_agent_output(output, stderr, result.returncode),
                    "tool_calls": [],
                }
            if not output and stderr:
                output = f"[STDERR] {stderr.strip()}"
                self.last_run_trace["error_kind"] = "agent_stderr"
            return output
        except subprocess.TimeoutExpired:
            self.last_run_trace = {
                "output_format": "stream-json" if self.capture_trace else "text",
                "event_count": 0,
                "tool_calls": [],
                "tool_results": 0,
                "result": {},
                "error_kind": "timeout",
                "returncode": None,
            }
            return f"[TIMEOUT] Claude Code timed out after {self.timeout}s"
        except FileNotFoundError:
            self.last_run_trace = {
                "output_format": "stream-json" if self.capture_trace else "text",
                "event_count": 0,
                "tool_calls": [],
                "tool_results": 0,
                "result": {},
                "error_kind": "agent_error",
                "returncode": None,
            }
            return "[ERROR] Claude Code CLI not found. Ensure 'claude' is on PATH."
