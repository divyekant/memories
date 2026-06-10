"""Tests for the per-prompt playbook injection gate (_playbook_injection_mode).

The gate decides whether the UserPromptSubmit hook injects the full directive
memory playbook ("full") or at most a 1-2 line reminder ("minimal").

Gate signal: full iff (retrieval returned >=1 candidate memory) OR (the prompt
is prior-work-shaped). Prompt fixtures are derived from the cases in
eval/active_search_cases.json (used by eval/run_active_search_eval.py) — the
live eval itself is NOT run here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_LIB = REPO_ROOT / "plugin" / "hooks" / "_lib.sh"
CODEX_LIB = REPO_ROOT / "integrations" / "codex" / "hooks" / "_lib.sh"

LIBS = [
    pytest.param(PLUGIN_LIB, id="claude-code-lib"),
    pytest.param(CODEX_LIB, id="codex-lib"),
]


def _gate(lib: Path, prompt: str, candidate_count: int) -> str:
    """Shell out to _playbook_injection_mode exactly as the hooks would."""

    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; _playbook_injection_mode "$2" "$3"',
            "_",
            str(lib),
            prompt,
            str(candidate_count),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"gate invocation failed: {result.stderr}"
    return result.stdout.strip()


# Prior-work-shaped prompts (should_search=True shapes from
# eval/active_search_cases.json) must gate "full" even with zero candidates.
EVAL_PRIOR_WORK_PROMPTS = [
    "Did we already decide how active-search evals should be gated before release?",
    "Where did we leave the generic MCP client work?",
    "What was the last fix for temporal memory answers?",
    "Do you remember how we handled temporal eval gating?",
    "What's the plan for checking whether active memory search keeps working?",
    "How did we handle cross-client MCP compatibility?",
]

# Additional prior-work shapes the gate must catch.
EXTRA_PRIOR_WORK_PROMPTS = [
    "Weren't we going to gate the playbook injection?",
    "Didn't we fix the transcript parsing already?",
    "Did we settle on pnpm for this repo?",
    "How does the AUDN extraction pipeline work?",
    "What version of the backend is currently deployed?",
    "Is the extraction endpoint still slow on the M4?",
    "Last time we talked about the shadow extraction runners.",
    "We were in the middle of wiring the fan-out orchestrator.",
    "Resume the worktree cleanup task from yesterday.",
    "Continue with the hook refactor please.",
    "What about the Codex hook variant?",
    "Does that still apply to the new installer?",
]

# Self-contained prompts (derived from the should_search=False control case
# "What is 2 plus 2?" plus arithmetic/translation/formatting/generic-fact
# shapes) must take the minimal path when retrieval returned nothing.
SELF_CONTAINED_PROMPTS = [
    "What is 2 plus 2?",
    "Translate the phrase good morning into French.",
    "Format this JSON with two-space indentation.",
    "What is the capital of Australia?",
    "Write a regex that matches ISO 8601 dates.",
    "Sort these words alphabetically: banana, apple, cherry.",
]


@pytest.mark.parametrize("lib", LIBS)
@pytest.mark.parametrize("prompt", EVAL_PRIOR_WORK_PROMPTS + EXTRA_PRIOR_WORK_PROMPTS)
def test_prior_work_prompts_gate_full_even_without_candidates(lib: Path, prompt: str) -> None:
    assert _gate(lib, prompt, 0) == "full"


@pytest.mark.parametrize("lib", LIBS)
@pytest.mark.parametrize("prompt", SELF_CONTAINED_PROMPTS)
def test_self_contained_prompts_gate_minimal_without_candidates(lib: Path, prompt: str) -> None:
    assert _gate(lib, prompt, 0) == "minimal"


@pytest.mark.parametrize("lib", LIBS)
@pytest.mark.parametrize("prompt", SELF_CONTAINED_PROMPTS[:2])
def test_candidates_without_prior_work_shape_gate_memories(lib: Path, prompt: str) -> None:
    """>=1 retrieved candidate on a non-prior-work prompt injects the memories
    block with a short preamble — not the full directive mandate. On real
    telemetry ~99% of prompts have >=1 keyword candidate, so keying the
    mandate on candidates would defeat the gate entirely."""

    assert _gate(lib, prompt, 1) == "memories"
    assert _gate(lib, prompt, 6) == "memories"


@pytest.mark.parametrize("lib", LIBS)
def test_prior_work_shape_with_candidates_gates_full(lib: Path) -> None:
    assert _gate(lib, "how does the deploy pipeline work?", 3) == "full"
    assert _gate(lib, "didn't we decide to use Qdrant?", 1) == "full"


@pytest.mark.parametrize("lib", LIBS)
def test_gate_treats_garbage_candidate_count_as_zero(lib: Path) -> None:
    assert _gate(lib, "What is 2 plus 2?", "not-a-number") == "minimal"  # type: ignore[arg-type]


@pytest.mark.parametrize("lib", LIBS)
def test_gate_is_case_insensitive(lib: Path) -> None:
    assert _gate(lib, "DIDN'T WE DECIDE THIS ALREADY?", 0) == "full"


@pytest.mark.parametrize("lib", LIBS)
def test_active_search_pattern_exposed_for_hooks(lib: Path) -> None:
    """memory-query.sh reuses the lib regex for its active-search classifier."""

    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; _active_search_pattern',
            "_",
            str(lib),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    pattern = result.stdout.strip()
    assert "did we already" in pattern
    assert "do you remember" in pattern
    assert "left off" in pattern
