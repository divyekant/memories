"""Worktree-aware project resolution in hook _lib.sh.

Git worktree sessions (e.g. Claude Code's .claude/worktrees/<name>) must
resolve PROJECT to the main repo's directory name, not the worktree dir —
otherwise every worktree session scopes recall/capture to a throwaway name
like `infallible-elion-cdc047` and is memory-blind.
"""

import subprocess
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
