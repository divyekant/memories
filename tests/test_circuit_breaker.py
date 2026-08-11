"""Hook circuit breaker: a sick backend costs one failure, not 8s per prompt.

After any backend call fails, a breaker file makes every subsequent hook
invocation skip backend calls instantly until the cooldown elapses.
"""

import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS = [
    REPO_ROOT / "plugin" / "hooks" / "_lib.sh",
    REPO_ROOT / "integrations" / "codex" / "hooks" / "_lib.sh",
]


def _sh(lib: Path, script: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{lib}" 2>/dev/null; {script}'],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": env.pop("HOME"), **env},
    )


@pytest.fixture()
def breaker_env(tmp_path):
    return {
        "HOME": str(tmp_path),
        "MEMORIES_BREAKER_FILE": str(tmp_path / "backend-down"),
        "MEMORIES_BREAKER_COOLDOWN": "3600",
    }


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
class TestBreakerStates:
    def test_closed_when_no_file(self, lib, breaker_env, tmp_path):
        out = _sh(lib, "_breaker_open && echo OPEN || echo CLOSED", dict(breaker_env))
        assert out.stdout.strip() == "CLOSED"

    def test_trip_creates_file_and_opens(self, lib, breaker_env, tmp_path):
        out = _sh(lib, "_breaker_trip; _breaker_open && echo OPEN || echo CLOSED", dict(breaker_env))
        assert out.stdout.strip() == "OPEN"
        assert (tmp_path / "backend-down").exists()

    def test_half_open_after_cooldown(self, lib, breaker_env, tmp_path):
        stale = int(time.time()) - 9999
        (tmp_path / "backend-down").write_text(str(stale))
        out = _sh(lib, "_breaker_open && echo OPEN || echo CLOSED", dict(breaker_env))
        assert out.stdout.strip() == "CLOSED", "elapsed cooldown must allow a retry"

    def test_garbage_breaker_file_is_cleared(self, lib, breaker_env, tmp_path):
        (tmp_path / "backend-down").write_text("not-a-timestamp")
        out = _sh(lib, "_breaker_open && echo OPEN || echo CLOSED", dict(breaker_env))
        assert out.stdout.strip() == "CLOSED"
        assert not (tmp_path / "backend-down").exists()

    def test_reset_closes(self, lib, breaker_env, tmp_path):
        out = _sh(
            lib,
            "_breaker_trip; _breaker_reset; _breaker_open && echo OPEN || echo CLOSED",
            dict(breaker_env),
        )
        assert out.stdout.strip() == "CLOSED"


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
class TestBreakerWiring:
    def _fake_curl(self, tmp_path, exit_code=0, body='{"results":[],"count":0}'):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        marker = tmp_path / "curl-invoked"
        curl = bin_dir / "curl"
        # Mimic real curl's -w/--write-out: append "\n200" after the body when
        # the caller asked for %{http_code}, same as _search_memories_multi's
        # single-backend path relies on to tell a 401 (credential problem) apart
        # from a connection failure without a second round-trip.
        success_script = (
            f"printf '%s' '{body}'\n"
            "for arg in \"$@\"; do\n"
            '  case "$arg" in *"%{http_code}"*) printf "\\n200";; esac\n'
            "done\n"
            "exit 0\n"
        )
        curl.write_text(
            f"#!/bin/bash\necho invoked >> {marker}\n"
            + (success_script if exit_code == 0 else f"exit {exit_code}\n")
        )
        curl.chmod(0o755)
        return bin_dir, marker

    def test_open_breaker_skips_curl_entirely(self, lib, breaker_env, tmp_path):
        bin_dir, marker = self._fake_curl(tmp_path)
        env = dict(breaker_env)
        (tmp_path / "backend-down").write_text(str(int(time.time())))
        out = subprocess.run(
            ["bash", "-c", f'source "{lib}" 2>/dev/null; _search_memories_multi "query text"'],
            capture_output=True, text=True, timeout=30,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": env["HOME"],
                 "MEMORIES_BREAKER_FILE": env["MEMORIES_BREAKER_FILE"],
                 "MEMORIES_BREAKER_COOLDOWN": "3600"},
        )
        assert '"results":[]' in out.stdout.replace(" ", "")
        assert not marker.exists(), "curl must not be invoked while the breaker is open"

    def test_curl_failure_trips_breaker(self, lib, breaker_env, tmp_path):
        bin_dir, marker = self._fake_curl(tmp_path, exit_code=22)
        env = dict(breaker_env)
        out = subprocess.run(
            ["bash", "-c", f'source "{lib}" 2>/dev/null; _search_memories_multi "query text"; test -f "$MEMORIES_BREAKER_FILE" && echo TRIPPED'],
            capture_output=True, text=True, timeout=30,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": env["HOME"],
                 "MEMORIES_BREAKER_FILE": env["MEMORIES_BREAKER_FILE"],
                 "MEMORIES_BREAKER_COOLDOWN": "3600"},
        )
        assert "TRIPPED" in out.stdout
        assert marker.exists(), "the first failure does invoke curl"

    def test_success_resets_breaker(self, lib, breaker_env, tmp_path):
        bin_dir, _ = self._fake_curl(tmp_path, exit_code=0)
        env = dict(breaker_env)
        stale = int(time.time()) - 9999  # half-open: retry allowed
        (tmp_path / "backend-down").write_text(str(stale))
        out = subprocess.run(
            ["bash", "-c", f'source "{lib}" 2>/dev/null; _search_memories_multi "query text" >/dev/null; test -f "$MEMORIES_BREAKER_FILE" && echo STILL || echo CLEARED'],
            capture_output=True, text=True, timeout=30,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": env["HOME"],
                 "MEMORIES_BREAKER_FILE": env["MEMORIES_BREAKER_FILE"],
                 "MEMORIES_BREAKER_COOLDOWN": "3600"},
        )
        assert out.stdout.strip().endswith("CLEARED")
