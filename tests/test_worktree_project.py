"""Worktree-aware project resolution in hook _lib.sh.

Git worktree sessions (e.g. Claude Code's .claude/worktrees/<name>) must
resolve PROJECT to the main repo's directory name, not the worktree dir —
otherwise every worktree session scopes recall/capture to a throwaway name
like `infallible-elion-cdc047` and is memory-blind.
"""

import subprocess
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS = [
    REPO_ROOT / "plugin" / "hooks" / "_lib.sh",
    REPO_ROOT / "integrations" / "codex" / "hooks" / "_lib.sh",
]


def _resolve(lib: Path, cwd: str) -> str:
    out = subprocess.run(
        ["bash", "-c", f'source "{lib}" 2>/dev/null; _memories_resolve_project "$1"', "_", cwd],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _project_context(lib: Path, cwd: str, *, env: dict[str, str] | None = None) -> dict[str, object]:
    full_env = {**os.environ, **(env or {})}
    out = subprocess.run(
        ["bash", "-c", f'source "{lib}" 2>/dev/null; _memories_project_context "$1"', "_", cwd],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _fake_curl(bin_dir: Path) -> None:
    script = bin_dir / "curl"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n200' \"$FAKE_ME_RESPONSE\"\n"
    )
    script.chmod(0o755)


@pytest.fixture()
def project_declarations() -> dict[str, str]:
    return {
        "valid": "project_id: shared-demo\nshared_memory: true\n",
        "missing": "project_id: shared-demo\n",
        "malformed": "project_id: [shared-demo\nshared_memory: true\n",
        "unknown": "project_id: shared-demo\nshared_memory: true\npromotion: true\n",
        "false": "project_id: shared-demo\nshared_memory: false\n",
        "invalid_slug": "project_id: Shared Demo\nshared_memory: true\n",
    }


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


@pytest.fixture()
def repo_with_worktree(tmp_path: Path) -> dict:
    repo = tmp_path / "myproj"
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "f.txt").write_text("x")
    _git(["add", "f.txt"], repo)
    _git(["commit", "-qm", "init"], repo)
    wt = repo / ".claude" / "worktrees" / "whimsical-name-abc123"
    wt.parent.mkdir(parents=True)
    _git(["worktree", "add", "-q", "-b", "wt-branch", str(wt)], repo)
    sub = repo / "subdir"
    sub.mkdir()
    plain = tmp_path / "plaindir"
    plain.mkdir()
    return {"repo": repo, "worktree": wt, "subdir": sub, "plain": plain}


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
def test_worktree_resolves_to_main_repo_name(lib: Path, repo_with_worktree: dict) -> None:
    assert _resolve(lib, str(repo_with_worktree["worktree"])) == "myproj"


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
def test_repo_root_resolves_to_own_name(lib: Path, repo_with_worktree: dict) -> None:
    assert _resolve(lib, str(repo_with_worktree["repo"])) == "myproj"


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
def test_repo_subdir_resolves_to_repo_name(lib: Path, repo_with_worktree: dict) -> None:
    assert _resolve(lib, str(repo_with_worktree["subdir"])) == "myproj"


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
def test_non_git_dir_resolves_to_basename(lib: Path, repo_with_worktree: dict) -> None:
    assert _resolve(lib, str(repo_with_worktree["plain"])) == "plaindir"


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
def test_empty_cwd_resolves_to_unknown(lib: Path) -> None:
    assert _resolve(lib, "") == "unknown"


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
@pytest.mark.parametrize("fixture_name", ["valid", "missing", "malformed", "unknown", "false", "invalid_slug"])
def test_project_declaration_fixtures_are_strict(
    lib: Path,
    fixture_name: str,
    project_declarations: dict[str, str],
    repo_with_worktree: dict,
) -> None:
    memories_dir = repo_with_worktree["repo"] / ".memories"
    memories_dir.mkdir(exist_ok=True)
    (memories_dir / "project.yaml").write_text(project_declarations[fixture_name])
    context = _project_context(
        lib,
        str(repo_with_worktree["repo"]),
        env={"MEMORIES_URL": "", "MEMORIES_API_KEY": ""},
    )
    assert context["active"] is False
    if fixture_name == "valid":
        # A valid declaration still fails closed without an authenticated
        # managed principal; the declaration itself must not grant access.
        assert context["reason"] in {"missing_principal", "principal_unreachable"}
    else:
        assert context["reason"] in {"missing_field", "malformed", "unknown_field", "shared_memory_not_true", "invalid_project_id"}


@pytest.mark.parametrize("lib", LIBS, ids=["claude-code-lib", "codex-lib"])
def test_project_declaration_in_worktree_uses_main_repository_boundary(
    lib: Path,
    repo_with_worktree: dict,
    tmp_path: Path,
) -> None:
    memories_dir = repo_with_worktree["repo"] / ".memories"
    memories_dir.mkdir(exist_ok=True)
    (memories_dir / "project.yaml").write_text("project_id: shared-demo\nshared_memory: true\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_curl(bin_dir)
    context = _project_context(
        lib,
        str(repo_with_worktree["worktree"]),
        env={
            "MEMORIES_URL": "http://backend.test",
            "MEMORIES_API_KEY": "secret",
            "FAKE_ME_RESPONSE": json.dumps({"type": "managed", "principal_id": "alice"}),
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        },
    )
    assert context["active"] is True
    assert context["reason"] == "active"
    assert context["project_id"] == "shared-demo"
    assert context["principal_id"] == "alice"
