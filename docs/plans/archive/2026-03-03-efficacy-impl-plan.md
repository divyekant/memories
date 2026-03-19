# Efficacy Eval Harness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a benchmark harness that runs test scenarios with and without Memories via Claude Code, scoring outputs to prove Memories improves agent effectiveness.

**Architecture:** YAML-defined scenarios → Python runner orchestrates CC execution in isolated temp projects → scorer evaluates output against rubrics (deterministic + LLM-as-judge) → reporter generates JSON efficacy report.

**Tech Stack:** Python, PyYAML, httpx, Pydantic, Claude Code (`claude -p`), existing `llm_provider.py` for LLM judge.

---

### Task 1: Add PyYAML Dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add pyyaml to dev dependencies**

In `pyproject.toml`, add `pyyaml>=6.0` to the dev dependencies section alongside pytest and httpx.

**Step 2: Install**

Run: `pip install pyyaml`

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pyyaml dependency for eval harness"
```

---

### Task 2: Scenario & Config Models

**Files:**
- Create: `eval/__init__.py`
- Create: `eval/models.py`
- Create: `eval/tests/__init__.py`
- Create: `eval/tests/test_models.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_models.py
import pytest
import yaml
from eval.models import Scenario, Rubric, MemorySeed, EvalConfig, TestResult, EvalReport


class TestRubric:
    def test_contains_rubric(self):
        r = Rubric(type="contains", value="auth middleware", weight=0.5)
        assert r.type == "contains"
        assert r.value == "auth middleware"
        assert r.weight == 0.5

    def test_llm_judge_rubric(self):
        r = Rubric(type="correct_fix", description="Fix addresses root cause", weight=0.3)
        assert r.type == "correct_fix"
        assert r.description == "Fix addresses root cause"

    def test_invalid_rubric_type_rejected(self):
        with pytest.raises(ValueError):
            Rubric(type="invalid_type", weight=0.5)

    def test_weights_must_be_0_to_1(self):
        with pytest.raises(ValueError):
            Rubric(type="contains", value="x", weight=1.5)


class TestScenario:
    def test_load_from_dict(self):
        data = {
            "id": "coding-001",
            "category": "coding",
            "name": "Fix bug using known pattern",
            "description": "Agent fixes a null-check bug",
            "memories": [
                {"text": "NoneType errors caused by missing auth middleware", "source": "eval/coding-001"}
            ],
            "prompt": "Fix the TypeError in user_handler.py",
            "expected": [
                {"type": "contains", "value": "auth middleware", "weight": 0.5},
                {"type": "no_retry", "description": "No clarifying questions", "weight": 0.5}
            ],
        }
        s = Scenario(**data)
        assert s.id == "coding-001"
        assert s.category == "coding"
        assert len(s.memories) == 1
        assert len(s.expected) == 2
        assert s.expected[0].type == "contains"

    def test_load_from_yaml_string(self):
        yaml_str = """
id: recall-001
category: recall
name: Recall a decision
description: Agent recalls why SQLite was chosen
memories:
  - text: "We chose SQLite over Postgres for local storage (ADR-003, 2026-01-15)"
    source: eval/recall-001
prompt: "Why do we use SQLite instead of Postgres?"
expected:
  - type: recall_accuracy
    description: Recalls the decision and rationale
    weight: 1.0
"""
        data = yaml.safe_load(yaml_str)
        s = Scenario(**data)
        assert s.id == "recall-001"
        assert s.memories[0].source == "eval/recall-001"

    def test_scenario_without_memories_is_valid(self):
        """For compounding tests that manage their own seeding."""
        s = Scenario(
            id="comp-001", category="compounding",
            name="Scale test", description="Test at scale",
            memories=[], prompt="Search for X",
            expected=[Rubric(type="contains", value="X", weight=1.0)]
        )
        assert s.memories == []


class TestEvalConfig:
    def test_default_weights(self):
        c = EvalConfig()
        assert c.category_weights == {"coding": 0.40, "recall": 0.35, "compounding": 0.25}

    def test_custom_weights(self):
        c = EvalConfig(category_weights={"coding": 0.5, "recall": 0.3, "compounding": 0.2})
        assert c.category_weights["coding"] == 0.5

    def test_load_from_yaml(self):
        yaml_str = """
memories_url: http://localhost:8900
memories_api_key: test-key
judge_provider: anthropic
cc_timeout: 120
category_weights:
  coding: 0.40
  recall: 0.35
  compounding: 0.25
"""
        data = yaml.safe_load(yaml_str)
        c = EvalConfig(**data)
        assert c.memories_url == "http://localhost:8900"
        assert c.cc_timeout == 120


class TestTestResult:
    def test_efficacy_delta(self):
        r = TestResult(
            scenario_id="coding-001", scenario_name="Fix bug",
            category="coding",
            score_with_memory=0.85, score_without_memory=0.40,
            output_with_memory="fixed it", output_without_memory="what error?",
            rubric_details=[]
        )
        assert r.efficacy_delta == pytest.approx(0.45)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_models.py -v`
Expected: FAIL — `eval.models` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/__init__.py
# Eval harness package

# eval/tests/__init__.py
# Eval tests package
```

```python
# eval/models.py
"""Data models for the efficacy eval harness."""
from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class RubricType(str, Enum):
    contains = "contains"
    not_contains = "not_contains"
    correct_fix = "correct_fix"
    no_retry = "no_retry"
    match_convention = "match_convention"
    recall_accuracy = "recall_accuracy"


class Rubric(BaseModel):
    type: RubricType
    value: Optional[str] = None
    description: Optional[str] = None
    weight: float = Field(ge=0.0, le=1.0)


class MemorySeed(BaseModel):
    text: str
    source: str


class Scenario(BaseModel):
    id: str
    category: str
    name: str
    description: str
    memories: list[MemorySeed]
    prompt: str
    expected: list[Rubric]


class EvalConfig(BaseModel):
    memories_url: str = "http://localhost:8900"
    memories_api_key: str = ""
    judge_provider: str = "anthropic"
    cc_timeout: int = 120
    category_weights: dict[str, float] = Field(
        default_factory=lambda: {"coding": 0.40, "recall": 0.35, "compounding": 0.25}
    )


class RubricResult(BaseModel):
    rubric_type: str
    score: float
    weight: float
    reasoning: Optional[str] = None


class TestResult(BaseModel):
    scenario_id: str
    scenario_name: str
    category: str
    score_with_memory: float
    score_without_memory: float
    output_with_memory: str
    output_without_memory: str
    rubric_details: list[RubricResult]

    @property
    def efficacy_delta(self) -> float:
        return self.score_with_memory - self.score_without_memory


class CategoryResult(BaseModel):
    category: str
    with_memory: float
    without_memory: float
    delta: float


class EvalReport(BaseModel):
    version: str = "1.0.0"
    timestamp: str
    overall_with_memory: float
    overall_without_memory: float
    overall_efficacy_delta: float
    categories: dict[str, CategoryResult]
    tests: list[TestResult]
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_models.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/__init__.py eval/models.py eval/tests/__init__.py eval/tests/test_models.py
git commit -m "feat(eval): add scenario and config data models"
```

---

### Task 3: Scenario Loader

**Files:**
- Create: `eval/loader.py`
- Create: `eval/tests/test_loader.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_loader.py
import os
import tempfile
import pytest
import yaml
from eval.loader import load_scenario, load_all_scenarios
from eval.models import Scenario


@pytest.fixture
def scenarios_dir():
    """Create a temp dir with sample scenario YAMLs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        coding_dir = os.path.join(tmpdir, "coding")
        recall_dir = os.path.join(tmpdir, "recall")
        os.makedirs(coding_dir)
        os.makedirs(recall_dir)

        scenario1 = {
            "id": "coding-001", "category": "coding",
            "name": "Fix bug", "description": "Fix a bug",
            "memories": [{"text": "Use auth middleware", "source": "eval/coding-001"}],
            "prompt": "Fix the error", "expected": [{"type": "contains", "value": "auth", "weight": 1.0}],
        }
        scenario2 = {
            "id": "recall-001", "category": "recall",
            "name": "Recall decision", "description": "Recall why SQLite",
            "memories": [{"text": "We chose SQLite (ADR-003)", "source": "eval/recall-001"}],
            "prompt": "Why SQLite?", "expected": [{"type": "recall_accuracy", "description": "Correct", "weight": 1.0}],
        }
        with open(os.path.join(coding_dir, "coding-001.yaml"), "w") as f:
            yaml.dump(scenario1, f)
        with open(os.path.join(recall_dir, "recall-001.yaml"), "w") as f:
            yaml.dump(scenario2, f)

        yield tmpdir


class TestLoadScenario:
    def test_load_single(self, scenarios_dir):
        path = os.path.join(scenarios_dir, "coding", "coding-001.yaml")
        s = load_scenario(path)
        assert isinstance(s, Scenario)
        assert s.id == "coding-001"

    def test_load_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("id: 123\n  broken: [")
        with pytest.raises(Exception):
            load_scenario(str(bad))


class TestLoadAllScenarios:
    def test_loads_all_from_dir(self, scenarios_dir):
        scenarios = load_all_scenarios(scenarios_dir)
        assert len(scenarios) == 2
        ids = {s.id for s in scenarios}
        assert ids == {"coding-001", "recall-001"}

    def test_filters_by_category(self, scenarios_dir):
        scenarios = load_all_scenarios(scenarios_dir, category="coding")
        assert len(scenarios) == 1
        assert scenarios[0].category == "coding"

    def test_empty_dir_returns_empty(self, tmp_path):
        scenarios = load_all_scenarios(str(tmp_path))
        assert scenarios == []
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_loader.py -v`
Expected: FAIL — `eval.loader` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/loader.py
"""Load scenario YAML files into Scenario models."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from eval.models import Scenario


def load_scenario(path: str) -> Scenario:
    """Load a single scenario from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return Scenario(**data)


def load_all_scenarios(
    scenarios_dir: str, category: Optional[str] = None
) -> list[Scenario]:
    """Load all scenarios from subdirectories, optionally filtered by category."""
    scenarios = []
    root = Path(scenarios_dir)
    if not root.exists():
        return []

    for yaml_file in sorted(root.rglob("*.yaml")):
        try:
            scenario = load_scenario(str(yaml_file))
            if category is None or scenario.category == category:
                scenarios.append(scenario)
        except Exception:
            raise

    return scenarios
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_loader.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/loader.py eval/tests/test_loader.py
git commit -m "feat(eval): add YAML scenario loader"
```

---

### Task 4: Deterministic Scorer

**Files:**
- Create: `eval/scorer.py`
- Create: `eval/tests/test_scorer.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_scorer.py
import pytest
from eval.scorer import score_rubric, score_all_rubrics
from eval.models import Rubric, RubricResult


class TestContainsRubric:
    def test_match(self):
        r = Rubric(type="contains", value="auth middleware", weight=0.5)
        result = score_rubric(r, "You should check the auth middleware in the handler")
        assert result.score == 1.0

    def test_no_match(self):
        r = Rubric(type="contains", value="auth middleware", weight=0.5)
        result = score_rubric(r, "I don't know what the error is about")
        assert result.score == 0.0

    def test_case_insensitive(self):
        r = Rubric(type="contains", value="Auth Middleware", weight=0.5)
        result = score_rubric(r, "check the auth middleware")
        assert result.score == 1.0


class TestNotContainsRubric:
    def test_absent_scores_1(self):
        r = Rubric(type="not_contains", value="redis-py", weight=0.3)
        result = score_rubric(r, "Use the custom connection pooler instead")
        assert result.score == 1.0

    def test_present_scores_0(self):
        r = Rubric(type="not_contains", value="redis-py", weight=0.3)
        result = score_rubric(r, "Install redis-py version 4.1")
        assert result.score == 0.0


class TestNoRetryRubric:
    def test_no_questions_scores_1(self):
        r = Rubric(type="no_retry", weight=0.3)
        result = score_rubric(r, "Here's the fix: add the auth check before line 42")
        assert result.score == 1.0

    def test_question_mark_scores_0(self):
        r = Rubric(type="no_retry", weight=0.3)
        result = score_rubric(r, "Can you share the full traceback? What version are you using?")
        assert result.score == 0.0


class TestScoreAllRubrics:
    def test_weighted_average(self):
        rubrics = [
            Rubric(type="contains", value="auth", weight=0.6),
            Rubric(type="no_retry", weight=0.4),
        ]
        output = "The auth middleware is missing. Here's the fix."
        score, details = score_all_rubrics(rubrics, output)
        # contains=1.0 * 0.6 + no_retry=1.0 * 0.4 = 1.0
        assert score == pytest.approx(1.0)
        assert len(details) == 2

    def test_partial_score(self):
        rubrics = [
            Rubric(type="contains", value="auth", weight=0.5),
            Rubric(type="contains", value="nonexistent", weight=0.5),
        ]
        output = "Check the auth middleware"
        score, details = score_all_rubrics(rubrics, output)
        # 1.0 * 0.5 + 0.0 * 0.5 = 0.5
        assert score == pytest.approx(0.5)

    def test_llm_rubrics_returned_unscored(self):
        """LLM judge rubrics are marked as needing judge evaluation."""
        rubrics = [
            Rubric(type="contains", value="auth", weight=0.5),
            Rubric(type="correct_fix", description="Root cause", weight=0.5),
        ]
        output = "The auth middleware fix"
        score, details = score_all_rubrics(rubrics, output)
        # Only deterministic rubrics scored; LLM ones get score=-1 (pending)
        llm_detail = [d for d in details if d.rubric_type == "correct_fix"][0]
        assert llm_detail.score == -1.0  # sentinel for "needs judge"
        assert llm_detail.reasoning == "pending_llm_judge"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_scorer.py -v`
Expected: FAIL — `eval.scorer` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/scorer.py
"""Score scenario outputs against rubrics."""
from __future__ import annotations

from eval.models import Rubric, RubricResult

# Rubric types that require LLM-as-judge
LLM_JUDGE_TYPES = {"correct_fix", "recall_accuracy", "match_convention"}


def score_rubric(rubric: Rubric, output: str) -> RubricResult:
    """Score a single rubric against output. Returns RubricResult."""
    rtype = rubric.type.value

    if rtype in LLM_JUDGE_TYPES:
        return RubricResult(
            rubric_type=rtype, score=-1.0, weight=rubric.weight,
            reasoning="pending_llm_judge",
        )

    if rtype == "contains":
        hit = rubric.value.lower() in output.lower() if rubric.value else False
        return RubricResult(
            rubric_type=rtype, score=1.0 if hit else 0.0, weight=rubric.weight,
            reasoning=f"'{ rubric.value}' {'found' if hit else 'not found'} in output",
        )

    if rtype == "not_contains":
        hit = rubric.value.lower() in output.lower() if rubric.value else True
        return RubricResult(
            rubric_type=rtype, score=0.0 if hit else 1.0, weight=rubric.weight,
            reasoning=f"'{rubric.value}' {'found (bad)' if hit else 'absent (good)'} in output",
        )

    if rtype == "no_retry":
        has_question = "?" in output
        return RubricResult(
            rubric_type=rtype, score=0.0 if has_question else 1.0, weight=rubric.weight,
            reasoning=f"Output {'contains' if has_question else 'does not contain'} question marks",
        )

    return RubricResult(
        rubric_type=rtype, score=0.0, weight=rubric.weight,
        reasoning=f"Unknown rubric type: {rtype}",
    )


def score_all_rubrics(
    rubrics: list[Rubric], output: str
) -> tuple[float, list[RubricResult]]:
    """Score all rubrics, return weighted average and details.

    LLM judge rubrics are excluded from the average (score=-1 sentinel).
    """
    details = [score_rubric(r, output) for r in rubrics]

    scored = [d for d in details if d.score >= 0]
    if not scored:
        return 0.0, details

    total_weight = sum(d.weight for d in scored)
    if total_weight == 0:
        return 0.0, details

    weighted_sum = sum(d.score * d.weight for d in scored)
    return weighted_sum / total_weight, details
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_scorer.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/scorer.py eval/tests/test_scorer.py
git commit -m "feat(eval): add deterministic rubric scorer"
```

---

### Task 5: LLM Judge

**Files:**
- Create: `eval/judge.py`
- Create: `eval/tests/test_judge.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_judge.py
import pytest
from unittest.mock import MagicMock, patch
from eval.judge import LLMJudge
from eval.models import Rubric, RubricResult


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.provider_name = "anthropic"
    provider.model = "claude-haiku-4-5-20251001"
    return provider


class TestLLMJudge:
    def test_judge_returns_score(self, mock_provider):
        mock_provider.complete.return_value = MagicMock(
            text='{"score": 0.8, "reasoning": "Fix correctly addresses the root cause"}',
            input_tokens=100, output_tokens=50,
        )
        judge = LLMJudge(mock_provider)
        rubric = Rubric(type="correct_fix", description="Fix addresses root cause", weight=0.5)
        result = judge.evaluate(rubric, prompt="Fix the error", output="Added auth check")
        assert result.score == pytest.approx(0.8)
        assert "root cause" in result.reasoning

    def test_judge_handles_unstructured_response(self, mock_provider):
        """If LLM returns plain text with a number, extract it."""
        mock_provider.complete.return_value = MagicMock(
            text="Score: 0.6. The fix partially addresses the issue.",
            input_tokens=100, output_tokens=50,
        )
        judge = LLMJudge(mock_provider)
        rubric = Rubric(type="recall_accuracy", description="Recalls decision", weight=1.0)
        result = judge.evaluate(rubric, prompt="Why SQLite?", output="We use SQLite")
        assert 0.0 <= result.score <= 1.0

    def test_judge_returns_0_on_parse_failure(self, mock_provider):
        mock_provider.complete.return_value = MagicMock(
            text="I cannot evaluate this.", input_tokens=50, output_tokens=20,
        )
        judge = LLMJudge(mock_provider)
        rubric = Rubric(type="correct_fix", description="Test", weight=0.5)
        result = judge.evaluate(rubric, prompt="test", output="test")
        assert result.score == 0.0
        assert "parse" in result.reasoning.lower() or "could not" in result.reasoning.lower()

    def test_judge_called_with_correct_prompt(self, mock_provider):
        mock_provider.complete.return_value = MagicMock(
            text='{"score": 1.0, "reasoning": "Perfect"}',
            input_tokens=100, output_tokens=50,
        )
        judge = LLMJudge(mock_provider)
        rubric = Rubric(type="correct_fix", description="Root cause fix", weight=0.5)
        judge.evaluate(rubric, prompt="Fix error", output="Fixed it")

        call_args = mock_provider.complete.call_args
        system_msg = call_args[1].get("system") or call_args[0][0]
        user_msg = call_args[1].get("user") or call_args[0][1]
        assert "0" in system_msg and "1" in system_msg  # mentions scoring range
        assert "Fix error" in user_msg
        assert "Fixed it" in user_msg
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_judge.py -v`
Expected: FAIL — `eval.judge` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/judge.py
"""LLM-as-judge for non-deterministic rubric evaluation."""
from __future__ import annotations

import json
import re
from typing import Protocol

from eval.models import Rubric, RubricResult


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> object: ...


JUDGE_SYSTEM_PROMPT = """You are an expert evaluator for AI agent output quality.

Score the agent's output from 0.0 to 1.0 based on the rubric provided.

Respond with JSON only:
{"score": <float 0-1>, "reasoning": "<brief explanation>"}

Scoring guide:
- 1.0 = Fully meets the rubric criteria
- 0.7-0.9 = Mostly meets criteria with minor gaps
- 0.4-0.6 = Partially meets criteria
- 0.1-0.3 = Barely addresses criteria
- 0.0 = Does not meet criteria at all"""


class LLMJudge:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def evaluate(self, rubric: Rubric, prompt: str, output: str) -> RubricResult:
        """Use LLM to evaluate output against a rubric."""
        user_msg = (
            f"## Rubric\nType: {rubric.type.value}\n"
            f"Criteria: {rubric.description or 'N/A'}\n\n"
            f"## Original Prompt\n{prompt}\n\n"
            f"## Agent Output\n{output}"
        )

        try:
            result = self.provider.complete(JUDGE_SYSTEM_PROMPT, user_msg)
            score, reasoning = self._parse_response(result.text)
        except Exception as e:
            score, reasoning = 0.0, f"Judge error: {e}"

        return RubricResult(
            rubric_type=rubric.type.value,
            score=score,
            weight=rubric.weight,
            reasoning=reasoning,
        )

    def _parse_response(self, text: str) -> tuple[float, str]:
        """Extract score and reasoning from LLM response."""
        # Try JSON parse first
        try:
            data = json.loads(text)
            score = float(data["score"])
            score = max(0.0, min(1.0, score))
            return score, data.get("reasoning", "")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        # Fallback: find a decimal number after "score" or at start
        match = re.search(r"(?:score[:\s]*)?(\d+\.?\d*)", text, re.IGNORECASE)
        if match:
            score = float(match.group(1))
            if score > 1.0:
                score = score / 10.0 if score <= 10.0 else score / 100.0
            score = max(0.0, min(1.0, score))
            return score, text.strip()

        return 0.0, f"Could not parse score from response: {text[:200]}"
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_judge.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/judge.py eval/tests/test_judge.py
git commit -m "feat(eval): add LLM-as-judge for non-deterministic rubrics"
```

---

### Task 6: Memories API Client

**Files:**
- Create: `eval/memories_client.py`
- Create: `eval/tests/test_memories_client.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_memories_client.py
import pytest
from unittest.mock import patch, MagicMock
import httpx
from eval.memories_client import MemoriesClient


@pytest.fixture
def client():
    return MemoriesClient(url="http://localhost:8900", api_key="test-key")


class TestMemoriesClient:
    def test_seed_memories(self, client):
        with patch.object(client._client, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": 0})
            memories = [{"text": "fact one", "source": "eval/test"}]
            client.seed_memories(memories)
            mock_post.assert_called_once()
            call_json = mock_post.call_args[1]["json"]
            assert call_json["text"] == "fact one"
            assert call_json["source"] == "eval/test"
            assert call_json["deduplicate"] is False

    def test_clear_by_prefix(self, client):
        with patch.object(client._client, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"deleted": 5})
            deleted = client.clear_by_prefix("eval/")
            assert deleted == 5
            call_json = mock_post.call_args[1]["json"]
            assert call_json["source_prefix"] == "eval/"

    def test_health_check(self, client):
        with patch.object(client._client, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: {"status": "ok"})
            assert client.health_check() is True

    def test_health_check_fails(self, client):
        with patch.object(client._client, "get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")
            assert client.health_check() is False

    def test_get_stats(self, client):
        with patch.object(client._client, "get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"total_memories": 100, "model": "all-MiniLM-L6-v2"},
            )
            stats = client.get_stats()
            assert stats["total_memories"] == 100
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_memories_client.py -v`
Expected: FAIL — `eval.memories_client` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/memories_client.py
"""HTTP client for Memories API used by the eval runner."""
from __future__ import annotations

import httpx


class MemoriesClient:
    def __init__(self, url: str = "http://localhost:8900", api_key: str = ""):
        self._url = url.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["X-API-Key"] = api_key
        self._client = httpx.Client(timeout=30.0)

    def seed_memories(self, memories: list[dict]) -> list[int]:
        """Add memories to the store. Returns list of IDs."""
        ids = []
        for mem in memories:
            resp = self._client.post(
                f"{self._url}/memory/add",
                json={"text": mem["text"], "source": mem["source"], "deduplicate": False},
                headers=self._headers,
            )
            resp.raise_for_status()
            ids.append(resp.json().get("id"))
        return ids

    def clear_by_prefix(self, prefix: str) -> int:
        """Delete all memories matching source prefix. Returns count deleted."""
        resp = self._client.post(
            f"{self._url}/memory/delete-by-prefix",
            json={"source_prefix": prefix},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json().get("deleted", 0)

    def health_check(self) -> bool:
        """Check if Memories service is reachable."""
        try:
            resp = self._client.get(f"{self._url}/health/ready", headers=self._headers)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def get_stats(self) -> dict:
        """Get index stats."""
        resp = self._client.get(f"{self._url}/stats", headers=self._headers)
        resp.raise_for_status()
        return resp.json()
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_memories_client.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/memories_client.py eval/tests/test_memories_client.py
git commit -m "feat(eval): add Memories API client for eval runner"
```

---

### Task 7: CC Executor (Claude Code Runner)

**Files:**
- Create: `eval/cc_executor.py`
- Create: `eval/tests/test_cc_executor.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_cc_executor.py
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from eval.cc_executor import CCExecutor


class TestCCExecutor:
    def test_creates_temp_project(self):
        executor = CCExecutor(timeout=30)
        project_dir = executor.create_isolated_project()
        assert os.path.isdir(project_dir)
        # Should NOT have CLAUDE.md
        assert not os.path.exists(os.path.join(project_dir, "CLAUDE.md"))
        # Should NOT have .claude/memory
        assert not os.path.exists(os.path.join(project_dir, ".claude"))
        executor.cleanup_project(project_dir)
        assert not os.path.exists(project_dir)

    def test_creates_mcp_config(self):
        executor = CCExecutor(
            timeout=30,
            memories_url="http://localhost:8900",
            memories_api_key="test-key",
            mcp_server_path="/path/to/mcp-server/index.js",
        )
        project_dir = executor.create_isolated_project(with_memories=True)
        mcp_config_path = os.path.join(project_dir, ".mcp.json")
        assert os.path.exists(mcp_config_path)
        with open(mcp_config_path) as f:
            config = json.load(f)
        assert "memories" in config.get("mcpServers", {})
        executor.cleanup_project(project_dir)

    def test_no_mcp_config_without_memories(self):
        executor = CCExecutor(timeout=30)
        project_dir = executor.create_isolated_project(with_memories=False)
        mcp_config_path = os.path.join(project_dir, ".mcp.json")
        assert not os.path.exists(mcp_config_path)
        executor.cleanup_project(project_dir)

    @patch("eval.cc_executor.subprocess.run")
    def test_run_prompt(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Here is the fix: add auth middleware check",
            stderr="",
            returncode=0,
        )
        executor = CCExecutor(timeout=30)
        output = executor.run_prompt("Fix the error", "/tmp/test-project")
        assert "auth middleware" in output
        # Verify claude -p was called with --project flag
        cmd = mock_run.call_args[0][0]
        assert "claude" in cmd[0] or "claude" in " ".join(cmd)
        assert "-p" in cmd

    @patch("eval.cc_executor.subprocess.run")
    def test_run_prompt_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        executor = CCExecutor(timeout=30)
        output = executor.run_prompt("Fix the error", "/tmp/test-project")
        assert "timeout" in output.lower()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_cc_executor.py -v`
Expected: FAIL — `eval.cc_executor` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/cc_executor.py
"""Execute prompts via Claude Code in isolated project environments."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile


class CCExecutor:
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
        """Create a temp directory as an isolated CC project.

        No CLAUDE.md, no auto-memory, no .claude/ dir.
        Optionally writes .mcp.json to enable Memories MCP.
        """
        project_dir = tempfile.mkdtemp(prefix="eval-memories-")

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
            with open(os.path.join(project_dir, ".mcp.json"), "w") as f:
                json.dump(mcp_config, f, indent=2)

        return project_dir

    def cleanup_project(self, project_dir: str) -> None:
        """Remove the temp project directory."""
        if project_dir and os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)

    def run_prompt(self, prompt: str, project_dir: str) -> str:
        """Run a prompt through Claude Code in programmatic mode.

        Returns CC's stdout output.
        """
        cmd = [
            "claude",
            "-p", prompt,
            "--project", project_dir,
            "--no-input",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=project_dir,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT] Claude Code did not respond within {self.timeout}s"
        except FileNotFoundError:
            return "[ERROR] Claude Code CLI not found. Ensure 'claude' is on PATH."
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_cc_executor.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/cc_executor.py eval/tests/test_cc_executor.py
git commit -m "feat(eval): add Claude Code executor with project isolation"
```

---

### Task 8: Runner (Orchestration)

**Files:**
- Create: `eval/runner.py`
- Create: `eval/tests/test_runner.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_runner.py
import pytest
from unittest.mock import MagicMock, patch
from eval.runner import EvalRunner
from eval.models import Scenario, Rubric, MemorySeed, EvalConfig, TestResult


@pytest.fixture
def scenario():
    return Scenario(
        id="coding-001", category="coding",
        name="Fix bug", description="Fix a bug using known pattern",
        memories=[MemorySeed(text="Use auth middleware", source="eval/coding-001")],
        prompt="Fix the TypeError in handler.py",
        expected=[
            Rubric(type="contains", value="auth middleware", weight=0.6),
            Rubric(type="no_retry", weight=0.4),
        ],
    )


@pytest.fixture
def config():
    return EvalConfig(
        memories_url="http://localhost:8900",
        memories_api_key="test-key",
    )


@pytest.fixture
def mock_deps():
    """Mock all external dependencies."""
    memories = MagicMock()
    memories.health_check.return_value = True
    memories.clear_by_prefix.return_value = 0
    memories.seed_memories.return_value = [0]

    executor = MagicMock()
    executor.create_isolated_project.return_value = "/tmp/eval-test"
    executor.run_prompt.side_effect = [
        "I'm not sure, can you share the traceback?",  # without memory
        "The auth middleware check is missing. Here's the fix.",  # with memory
    ]

    judge = MagicMock()
    return memories, executor, judge


class TestEvalRunner:
    def test_run_single_scenario(self, scenario, config, mock_deps):
        memories, executor, judge = mock_deps
        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=judge)
        result = runner.run_scenario(scenario)

        assert isinstance(result, TestResult)
        assert result.scenario_id == "coding-001"
        assert result.score_with_memory > result.score_without_memory
        # With memory: contains "auth middleware" (0.6) + no question (0.4) = 1.0
        assert result.score_with_memory == pytest.approx(1.0)
        # Without memory: no "auth middleware" (0.0) + has question (0.0) = 0.0
        assert result.score_without_memory == pytest.approx(0.0)

    def test_clears_memories_before_each_run(self, scenario, config, mock_deps):
        memories, executor, judge = mock_deps
        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=judge)
        runner.run_scenario(scenario)
        # Should clear eval/ prefix at least once
        memories.clear_by_prefix.assert_called()

    def test_seeds_memories_for_with_run(self, scenario, config, mock_deps):
        memories, executor, judge = mock_deps
        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=judge)
        runner.run_scenario(scenario)
        memories.seed_memories.assert_called_once()

    def test_creates_and_cleans_project(self, scenario, config, mock_deps):
        memories, executor, judge = mock_deps
        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=judge)
        runner.run_scenario(scenario)
        assert executor.create_isolated_project.call_count == 2  # without + with
        assert executor.cleanup_project.call_count == 2
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_runner.py -v`
Expected: FAIL — `eval.runner` does not exist.

**Step 3: Write minimal implementation**

```python
# eval/runner.py
"""Eval harness runner — orchestrates scenario execution."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from eval.models import (
    EvalConfig, Scenario, TestResult, RubricResult,
    CategoryResult, EvalReport,
)
from eval.scorer import score_all_rubrics, LLM_JUDGE_TYPES
from eval.memories_client import MemoriesClient
from eval.cc_executor import CCExecutor
from eval.judge import LLMJudge

logger = logging.getLogger("eval.runner")


class EvalRunner:
    def __init__(
        self,
        config: EvalConfig,
        memories_client: MemoriesClient | None = None,
        cc_executor: CCExecutor | None = None,
        judge: LLMJudge | None = None,
    ):
        self.config = config
        self.memories = memories_client or MemoriesClient(
            url=config.memories_url, api_key=config.memories_api_key,
        )
        self.executor = cc_executor or CCExecutor(
            timeout=config.cc_timeout,
            memories_url=config.memories_url,
            memories_api_key=config.memories_api_key,
        )
        self.judge = judge

    def run_scenario(self, scenario: Scenario) -> TestResult:
        """Run a single scenario with and without memories."""
        logger.info("Running scenario: %s", scenario.id)

        # --- Without memories ---
        self.memories.clear_by_prefix(f"eval/")
        project_dir = self.executor.create_isolated_project(with_memories=False)
        try:
            output_without = self.executor.run_prompt(scenario.prompt, project_dir)
        finally:
            self.executor.cleanup_project(project_dir)

        score_without, details_without = score_all_rubrics(scenario.expected, output_without)

        # --- With memories ---
        self.memories.clear_by_prefix(f"eval/")
        seeds = [{"text": m.text, "source": m.source} for m in scenario.memories]
        self.memories.seed_memories(seeds)
        project_dir = self.executor.create_isolated_project(with_memories=True)
        try:
            output_with = self.executor.run_prompt(scenario.prompt, project_dir)
        finally:
            self.executor.cleanup_project(project_dir)

        score_with, details_with = score_all_rubrics(scenario.expected, output_with)

        # --- LLM judge for pending rubrics ---
        if self.judge:
            for detail in details_with:
                if detail.score == -1.0:
                    rubric = next(
                        r for r in scenario.expected if r.type.value == detail.rubric_type
                    )
                    judged = self.judge.evaluate(rubric, scenario.prompt, output_with)
                    detail.score = judged.score
                    detail.reasoning = judged.reasoning

            for detail in details_without:
                if detail.score == -1.0:
                    rubric = next(
                        r for r in scenario.expected if r.type.value == detail.rubric_type
                    )
                    judged = self.judge.evaluate(rubric, scenario.prompt, output_without)
                    detail.score = judged.score
                    detail.reasoning = judged.reasoning

            # Recompute scores after judge fills in
            score_with = self._weighted_avg(details_with)
            score_without = self._weighted_avg(details_without)

        return TestResult(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            category=scenario.category,
            score_with_memory=score_with,
            score_without_memory=score_without,
            output_with_memory=output_with,
            output_without_memory=output_without,
            rubric_details=details_with,
        )

    def run_all(self, scenarios: list[Scenario]) -> EvalReport:
        """Run all scenarios and produce an aggregated report."""
        results = [self.run_scenario(s) for s in scenarios]
        return self._aggregate(results)

    def _weighted_avg(self, details: list[RubricResult]) -> float:
        scored = [d for d in details if d.score >= 0]
        total_w = sum(d.weight for d in scored)
        if total_w == 0:
            return 0.0
        return sum(d.score * d.weight for d in scored) / total_w

    def _aggregate(self, results: list[TestResult]) -> EvalReport:
        """Aggregate test results into category and overall scores."""
        categories: dict[str, list[TestResult]] = {}
        for r in results:
            categories.setdefault(r.category, []).append(r)

        cat_results = {}
        for cat, tests in categories.items():
            avg_with = sum(t.score_with_memory for t in tests) / len(tests)
            avg_without = sum(t.score_without_memory for t in tests) / len(tests)
            cat_results[cat] = CategoryResult(
                category=cat,
                with_memory=round(avg_with, 4),
                without_memory=round(avg_without, 4),
                delta=round(avg_with - avg_without, 4),
            )

        weights = self.config.category_weights
        total_weight = sum(weights.get(c, 0) for c in cat_results)
        if total_weight == 0:
            overall_with = overall_without = 0.0
        else:
            overall_with = sum(
                cat_results[c].with_memory * weights.get(c, 0) for c in cat_results
            ) / total_weight
            overall_without = sum(
                cat_results[c].without_memory * weights.get(c, 0) for c in cat_results
            ) / total_weight

        return EvalReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_with_memory=round(overall_with, 4),
            overall_without_memory=round(overall_without, 4),
            overall_efficacy_delta=round(overall_with - overall_without, 4),
            categories=cat_results,
            tests=results,
        )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_runner.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/runner.py eval/tests/test_runner.py
git commit -m "feat(eval): add runner orchestrating scenario execution"
```

---

### Task 9: Reporter

**Files:**
- Create: `eval/reporter.py`
- Create: `eval/tests/test_reporter.py`

**Step 1: Write the failing tests**

```python
# eval/tests/test_reporter.py
import json
import os
import pytest
from eval.reporter import save_report, format_summary
from eval.models import EvalReport, CategoryResult, TestResult


@pytest.fixture
def sample_report():
    return EvalReport(
        timestamp="2026-03-03T12:00:00+00:00",
        overall_with_memory=0.82,
        overall_without_memory=0.41,
        overall_efficacy_delta=0.41,
        categories={
            "coding": CategoryResult(category="coding", with_memory=0.85, without_memory=0.38, delta=0.47),
        },
        tests=[
            TestResult(
                scenario_id="coding-001", scenario_name="Fix bug", category="coding",
                score_with_memory=0.85, score_without_memory=0.38,
                output_with_memory="fixed", output_without_memory="confused",
                rubric_details=[],
            ),
        ],
    )


class TestSaveReport:
    def test_saves_json(self, sample_report, tmp_path):
        path = save_report(sample_report, str(tmp_path))
        assert os.path.exists(path)
        assert path.endswith(".json")
        with open(path) as f:
            data = json.load(f)
        assert data["overall_efficacy_delta"] == 0.41

    def test_filename_includes_timestamp(self, sample_report, tmp_path):
        path = save_report(sample_report, str(tmp_path))
        assert "2026-03-03" in os.path.basename(path)


class TestFormatSummary:
    def test_summary_contains_scores(self, sample_report):
        summary = format_summary(sample_report)
        assert "0.82" in summary
        assert "0.41" in summary
        assert "coding" in summary.lower()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest eval/tests/test_reporter.py -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

```python
# eval/reporter.py
"""Generate and save eval reports."""
from __future__ import annotations

import json
import os
from datetime import datetime

from eval.models import EvalReport


def save_report(report: EvalReport, results_dir: str) -> str:
    """Save report as JSON. Returns the file path."""
    os.makedirs(results_dir, exist_ok=True)
    ts = report.timestamp.replace(":", "-").replace("+", "p")[:19]
    filename = f"efficacy-{ts}.json"
    path = os.path.join(results_dir, filename)

    with open(path, "w") as f:
        json.dump(report.model_dump(), f, indent=2, default=str)

    return path


def format_summary(report: EvalReport) -> str:
    """Format a human-readable summary of the eval report."""
    lines = [
        "=" * 50,
        "MEMORIES EFFICACY REPORT",
        "=" * 50,
        f"Timestamp: {report.timestamp}",
        "",
        f"Overall with memory:    {report.overall_with_memory:.2f}",
        f"Overall without memory: {report.overall_without_memory:.2f}",
        f"Efficacy delta:         {report.overall_efficacy_delta:+.2f}",
        "",
        "--- Categories ---",
    ]

    for name, cat in report.categories.items():
        lines.append(f"  {name}: with={cat.with_memory:.2f} without={cat.without_memory:.2f} delta={cat.delta:+.2f}")

    lines.append("")
    lines.append(f"Tests run: {len(report.tests)}")
    lines.append("=" * 50)

    return "\n".join(lines)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest eval/tests/test_reporter.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add eval/reporter.py eval/tests/test_reporter.py
git commit -m "feat(eval): add JSON reporter and summary formatter"
```

---

### Task 10: Config File & Scenario YAMLs

**Files:**
- Create: `eval/config.yaml`
- Create: `eval/scenarios/coding/coding-001.yaml`
- Create: `eval/scenarios/coding/coding-002.yaml`
- Create: `eval/scenarios/recall/recall-001.yaml`
- Create: `eval/scenarios/recall/recall-002.yaml`
- Create: `eval/scenarios/compounding/compounding-001.yaml`
- Create: `eval/results/.gitkeep`

**Step 1: Create config.yaml**

```yaml
# eval/config.yaml
# Efficacy eval harness configuration

# Memories API
memories_url: http://localhost:8900
memories_api_key: ""  # Set via MEMORIES_API_KEY env var, or put here

# LLM judge provider (uses same env vars as extraction: EXTRACT_PROVIDER, ANTHROPIC_API_KEY)
judge_provider: anthropic

# Claude Code execution
cc_timeout: 120  # seconds per prompt
mcp_server_path: ""  # Set to absolute path of mcp-server/index.js

# Category weights for overall score
category_weights:
  coding: 0.40
  recall: 0.35
  compounding: 0.25
```

**Step 2: Create scenario YAMLs**

All scenarios use a synthetic project called "Voltis" — a fictional task management API. This avoids coupling to real projects and ensures Claude can't answer from training data.

```yaml
# eval/scenarios/coding/coding-001.yaml
id: coding-001
category: coding
name: "Fix bug using team's known pattern"
description: >
  The Voltis API has a recurring bug pattern where handlers crash with
  AttributeError when the request context is missing the tenant_id.
  The team's established fix is to add the TenantGuard middleware.
  With memories, the agent should apply TenantGuard directly.
  Without, it will likely try generic error handling.

memories:
  - text: >
      Voltis API: AttributeError crashes in handlers are always caused by
      missing tenant_id in request context. The fix is to add TenantGuard
      middleware to the route, not to add null checks in the handler.
      Decided in team retro 2026-01-20.
    source: eval/coding-001

prompt: >
  In the Voltis task management API, the endpoint POST /api/tasks/create
  is crashing with AttributeError: 'NoneType' object has no attribute 'tenant_id'
  on line 47 of task_handler.py. How should I fix this?

expected:
  - type: contains
    value: "TenantGuard"
    weight: 0.5
  - type: not_contains
    value: "if tenant_id is None"
    weight: 0.2
  - type: no_retry
    weight: 0.3
```

```yaml
# eval/scenarios/coding/coding-002.yaml
id: coding-002
category: coding
name: "Avoid known broken dependency"
description: >
  The Voltis project has a known issue with voltis-cache >= 3.0
  that breaks connection pooling. The team pins to 2.x.
  With memories, the agent should avoid recommending the upgrade.

memories:
  - text: >
      CRITICAL: Do NOT upgrade voltis-cache beyond 2.9.x. Version 3.0+
      rewrote the connection pooler and breaks our custom pool adapters.
      See incident report INC-2026-0042. Pin to voltis-cache>=2.8,<3.0.
    source: eval/coding-002

prompt: >
  I'm seeing slow cache performance in the Voltis API. Someone suggested
  upgrading voltis-cache to the latest version (3.2). Should I do that?

expected:
  - type: not_contains
    value: "upgrade to 3"
    weight: 0.4
  - type: contains
    value: "2."
    weight: 0.3
  - type: correct_fix
    description: "Agent warns against upgrading and references the known issue"
    weight: 0.3
```

```yaml
# eval/scenarios/recall/recall-001.yaml
id: recall-001
category: recall
name: "Recall architectural decision with rationale"
description: >
  The Voltis project chose SQLite for local job queues instead of Redis.
  With memories, the agent should recall the specific decision and reasoning.

memories:
  - text: >
      Voltis architecture decision ADR-007 (2026-01-15): Use SQLite for the
      local job queue instead of Redis. Rationale: (1) eliminates external
      dependency for single-node deploys, (2) ACID guarantees simplify
      retry logic, (3) team has deep SQLite expertise. Trade-off: won't
      scale to multi-node without migration to Redis later.
    source: eval/recall-001

prompt: >
  Why does the Voltis project use SQLite for the job queue instead of Redis?

expected:
  - type: recall_accuracy
    description: >
      Agent accurately recalls: (1) it was a deliberate decision (ADR-007),
      (2) at least 2 of the 3 rationale points, (3) acknowledges the trade-off
    weight: 1.0
```

```yaml
# eval/scenarios/recall/recall-002.yaml
id: recall-002
category: recall
name: "Recall user preference"
description: >
  The developer prefers uv over pip for package management in the Voltis project.
  With memories, the agent should use uv commands. Without, it will default to pip.

memories:
  - text: >
      Voltis project preference: Always use 'uv' for package management,
      never pip or pip-tools. The project uses uv.lock for deterministic installs.
      Set by lead developer, 2026-02-01.
    source: eval/recall-002

prompt: >
  I need to add the 'httpx' library to the Voltis project. How do I install it?

expected:
  - type: contains
    value: "uv"
    weight: 0.7
  - type: not_contains
    value: "pip install"
    weight: 0.3
```

```yaml
# eval/scenarios/compounding/compounding-001.yaml
id: compounding-001
category: compounding
name: "Search quality at scale"
description: >
  Tests whether search retrieval quality holds up as the memory store grows.
  Runner seeds 10, then 100, then 500 memories and searches for a specific fact.
  This scenario is special — the runner handles multi-round seeding externally.

memories: []

prompt: >
  What is the Voltis API rate limit for free-tier users?

expected:
  - type: contains
    value: "100 requests per minute"
    weight: 1.0
```

**Step 3: Create results directory**

```bash
mkdir -p eval/results && touch eval/results/.gitkeep
```

**Step 4: Commit**

```bash
git add eval/config.yaml eval/scenarios/ eval/results/.gitkeep
git commit -m "feat(eval): add config and initial scenario YAMLs"
```

---

### Task 11: CLI Entrypoint

**Files:**
- Create: `eval/__main__.py`

**Step 1: Write the entrypoint**

```python
# eval/__main__.py
"""CLI entrypoint: python -m eval [options]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import yaml

from eval.models import EvalConfig
from eval.loader import load_all_scenarios
from eval.runner import EvalRunner
from eval.reporter import save_report, format_summary
from eval.memories_client import MemoriesClient
from eval.cc_executor import CCExecutor
from eval.judge import LLMJudge

logger = logging.getLogger("eval")


def main():
    parser = argparse.ArgumentParser(description="Memories Efficacy Eval Harness")
    parser.add_argument(
        "--config", default="eval/config.yaml",
        help="Path to eval config YAML (default: eval/config.yaml)",
    )
    parser.add_argument(
        "--scenarios", default="eval/scenarios",
        help="Path to scenarios directory (default: eval/scenarios)",
    )
    parser.add_argument(
        "--results", default="eval/results",
        help="Path to results directory (default: eval/results)",
    )
    parser.add_argument(
        "--category", default=None,
        help="Run only scenarios in this category (coding, recall, compounding)",
    )
    parser.add_argument(
        "--scenario", default=None,
        help="Run a single scenario by ID",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load config
    with open(args.config) as f:
        config_data = yaml.safe_load(f)

    # Override with env vars
    config_data["memories_api_key"] = (
        os.getenv("MEMORIES_API_KEY") or config_data.get("memories_api_key", "")
    )
    config_data["mcp_server_path"] = (
        os.getenv("EVAL_MCP_SERVER_PATH") or config_data.get("mcp_server_path", "")
    )
    config = EvalConfig(**config_data)

    # Load scenarios
    scenarios = load_all_scenarios(args.scenarios, category=args.category)
    if args.scenario:
        scenarios = [s for s in scenarios if s.id == args.scenario]

    if not scenarios:
        logger.error("No scenarios found.")
        sys.exit(1)

    logger.info("Loaded %d scenarios", len(scenarios))

    # Build dependencies
    memories = MemoriesClient(url=config.memories_url, api_key=config.memories_api_key)
    if not memories.health_check():
        logger.error("Memories service not reachable at %s", config.memories_url)
        sys.exit(1)

    executor = CCExecutor(
        timeout=config.cc_timeout,
        memories_url=config.memories_url,
        memories_api_key=config.memories_api_key,
        mcp_server_path=config.get("mcp_server_path", ""),
    )

    # LLM judge (optional — uses existing provider infrastructure)
    judge = None
    try:
        from llm_provider import get_provider
        provider = get_provider()
        if provider:
            judge = LLMJudge(provider)
            logger.info("LLM judge enabled: %s/%s", provider.provider_name, provider.model)
    except ImportError:
        logger.warning("llm_provider not importable — LLM judge disabled")

    # Run
    runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=judge)
    report = runner.run_all(scenarios)

    # Save & print
    path = save_report(report, args.results)
    summary = format_summary(report)
    print(summary)
    logger.info("Report saved to %s", path)


if __name__ == "__main__":
    main()
```

**Step 2: Verify it loads**

Run: `python -m eval --help`
Expected: Shows argument help text.

**Step 3: Commit**

```bash
git add eval/__main__.py
git commit -m "feat(eval): add CLI entrypoint for running eval harness"
```

---

### Task 12: Integration Test (Dry Run)

**Files:**
- Create: `eval/tests/test_integration.py`

**Step 1: Write integration test**

This test mocks CC execution but uses real models, loader, scorer, and reporter.

```python
# eval/tests/test_integration.py
"""Integration test — full pipeline with mocked CC execution."""
import json
import os
import tempfile
import pytest
import yaml
from unittest.mock import MagicMock, patch

from eval.loader import load_all_scenarios
from eval.runner import EvalRunner
from eval.reporter import save_report, format_summary
from eval.models import EvalConfig


@pytest.fixture
def scenarios_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        coding_dir = os.path.join(tmpdir, "coding")
        os.makedirs(coding_dir)
        scenario = {
            "id": "coding-001", "category": "coding",
            "name": "Fix bug using known pattern",
            "description": "Agent should use TenantGuard",
            "memories": [{"text": "Use TenantGuard middleware", "source": "eval/coding-001"}],
            "prompt": "Fix the AttributeError in task_handler.py",
            "expected": [
                {"type": "contains", "value": "TenantGuard", "weight": 0.6},
                {"type": "no_retry", "weight": 0.4},
            ],
        }
        with open(os.path.join(coding_dir, "coding-001.yaml"), "w") as f:
            yaml.dump(scenario, f)
        yield tmpdir


class TestIntegration:
    def test_full_pipeline(self, scenarios_dir, tmp_path):
        scenarios = load_all_scenarios(scenarios_dir)
        assert len(scenarios) == 1

        config = EvalConfig(memories_url="http://localhost:8900", memories_api_key="test")

        memories = MagicMock()
        memories.health_check.return_value = True
        memories.clear_by_prefix.return_value = 0
        memories.seed_memories.return_value = [0]

        executor = MagicMock()
        executor.create_isolated_project.return_value = str(tmp_path)
        executor.run_prompt.side_effect = [
            "I need more context. What is the full error?",  # without
            "Add TenantGuard middleware to the route.",  # with
        ]

        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=None)
        report = runner.run_all(scenarios)

        assert report.overall_efficacy_delta > 0
        assert report.tests[0].score_with_memory > report.tests[0].score_without_memory

        path = save_report(report, str(tmp_path / "results"))
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data["overall_efficacy_delta"] > 0

        summary = format_summary(report)
        assert "EFFICACY" in summary
```

**Step 2: Run integration test**

Run: `python -m pytest eval/tests/test_integration.py -v`
Expected: PASS.

**Step 3: Commit**

```bash
git add eval/tests/test_integration.py
git commit -m "test(eval): add integration test for full eval pipeline"
```

---

Plan complete and saved to `docs/plans/2026-03-03-efficacy-impl-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach?