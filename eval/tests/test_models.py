import pytest
import yaml
from eval.models import Scenario, Rubric, MemorySeed, EvalConfig, ScenarioResult, EvalReport


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


class TestScenarioResult:
    def test_efficacy_delta(self):
        r = ScenarioResult(
            scenario_id="coding-001", scenario_name="Fix bug",
            category="coding",
            score_with_memory=0.85, score_without_memory=0.40,
            output_with_memory="fixed it", output_without_memory="what error?",
            rubric_details=[]
        )
        assert r.efficacy_delta == pytest.approx(0.45)
