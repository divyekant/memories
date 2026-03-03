"""Tests for eval.loader — YAML scenario loader."""

import os
import tempfile

import pytest
import yaml

from eval.loader import load_scenario, load_all_scenarios
from eval.models import Scenario


# ── helpers ──────────────────────────────────────────────────────────────────

SAMPLE_SCENARIO = {
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
        {"type": "no_retry", "description": "No clarifying questions", "weight": 0.5},
    ],
}

RECALL_SCENARIO = {
    "id": "recall-001",
    "category": "recall",
    "name": "Recall a decision",
    "description": "Agent recalls why SQLite was chosen",
    "memories": [
        {"text": "We chose SQLite over Postgres for local storage", "source": "eval/recall-001"}
    ],
    "prompt": "Why do we use SQLite instead of Postgres?",
    "expected": [
        {"type": "recall_accuracy", "description": "Recalls the decision", "weight": 1.0}
    ],
}


def _write_yaml(directory: str, filename: str, data: dict) -> str:
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


# ── tests ────────────────────────────────────────────────────────────────────


class TestLoadScenario:
    def test_load_single(self):
        """Loads one YAML file and returns a valid Scenario."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_yaml(tmp, "coding-001.yaml", SAMPLE_SCENARIO)
            scenario = load_scenario(path)
            assert isinstance(scenario, Scenario)
            assert scenario.id == "coding-001"
            assert scenario.category == "coding"
            assert scenario.name == "Fix bug using known pattern"
            assert len(scenario.memories) == 1
            assert len(scenario.expected) == 2

    def test_load_invalid_yaml_raises(self):
        """Raises on malformed YAML content."""
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.yaml")
            with open(bad_path, "w") as f:
                f.write("{{not: valid: yaml: [}")
            with pytest.raises(Exception):
                load_scenario(bad_path)

    def test_load_invalid_schema_raises(self):
        """Raises when YAML is valid but doesn't match Scenario schema."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_yaml(tmp, "incomplete.yaml", {"id": "x"})
            with pytest.raises(Exception):
                load_scenario(path)


class TestLoadAllScenarios:
    def test_loads_all_from_dir(self):
        """Loads scenarios from multiple category subdirectories."""
        with tempfile.TemporaryDirectory() as tmp:
            coding_dir = os.path.join(tmp, "coding")
            recall_dir = os.path.join(tmp, "recall")
            os.makedirs(coding_dir)
            os.makedirs(recall_dir)
            _write_yaml(coding_dir, "coding-001.yaml", SAMPLE_SCENARIO)
            _write_yaml(recall_dir, "recall-001.yaml", RECALL_SCENARIO)

            scenarios = load_all_scenarios(tmp)
            assert len(scenarios) == 2
            ids = {s.id for s in scenarios}
            assert ids == {"coding-001", "recall-001"}

    def test_filters_by_category(self):
        """Only returns scenarios matching the given category."""
        with tempfile.TemporaryDirectory() as tmp:
            coding_dir = os.path.join(tmp, "coding")
            recall_dir = os.path.join(tmp, "recall")
            os.makedirs(coding_dir)
            os.makedirs(recall_dir)
            _write_yaml(coding_dir, "coding-001.yaml", SAMPLE_SCENARIO)
            _write_yaml(recall_dir, "recall-001.yaml", RECALL_SCENARIO)

            scenarios = load_all_scenarios(tmp, category="recall")
            assert len(scenarios) == 1
            assert scenarios[0].id == "recall-001"

    def test_empty_dir_returns_empty(self):
        """Empty directory returns an empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            scenarios = load_all_scenarios(tmp)
            assert scenarios == []

    def test_results_are_sorted_by_id(self):
        """Returned scenarios are sorted by id."""
        with tempfile.TemporaryDirectory() as tmp:
            coding_dir = os.path.join(tmp, "coding")
            recall_dir = os.path.join(tmp, "recall")
            os.makedirs(coding_dir)
            os.makedirs(recall_dir)
            _write_yaml(recall_dir, "recall-001.yaml", RECALL_SCENARIO)
            _write_yaml(coding_dir, "coding-001.yaml", SAMPLE_SCENARIO)

            scenarios = load_all_scenarios(tmp)
            assert [s.id for s in scenarios] == ["coding-001", "recall-001"]

    def test_ignores_non_yaml_files(self):
        """Non-.yaml files in subdirectories are ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            coding_dir = os.path.join(tmp, "coding")
            os.makedirs(coding_dir)
            _write_yaml(coding_dir, "coding-001.yaml", SAMPLE_SCENARIO)
            # write a non-yaml file
            with open(os.path.join(coding_dir, "README.md"), "w") as f:
                f.write("# not a scenario")

            scenarios = load_all_scenarios(tmp)
            assert len(scenarios) == 1
