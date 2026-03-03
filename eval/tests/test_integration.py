"""Integration test — full pipeline with mocked CC execution."""
import json
import os
import tempfile
import pytest
import yaml
from unittest.mock import MagicMock

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
        # Load scenarios using real loader
        scenarios = load_all_scenarios(scenarios_dir)
        assert len(scenarios) == 1

        config = EvalConfig(memories_url="http://localhost:8900", memories_api_key="test")

        # Mock external deps only
        memories = MagicMock()
        memories.health_check.return_value = True
        memories.clear_by_prefix.return_value = 0
        memories.seed_memories.return_value = [0]

        executor = MagicMock()
        executor.create_isolated_project.return_value = str(tmp_path)
        executor.run_prompt.side_effect = [
            "I need more context. What is the full error?",  # without memory
            "Add TenantGuard middleware to the route.",  # with memory
        ]

        # Run with real runner, scorer, aggregation
        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=None)
        report = runner.run_all(scenarios)

        # Verify efficacy signal
        assert report.overall_efficacy_delta > 0
        assert report.tests[0].score_with_memory > report.tests[0].score_without_memory

        # Verify report saving works
        path = save_report(report, str(tmp_path / "results"))
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data["overall_efficacy_delta"] > 0

        # Verify summary formatting
        summary = format_summary(report)
        assert "EFFICACY" in summary

    def test_multi_category_pipeline(self, tmp_path):
        """Test with scenarios across multiple categories."""
        scenarios_dir = str(tmp_path / "scenarios")
        coding_dir = os.path.join(scenarios_dir, "coding")
        recall_dir = os.path.join(scenarios_dir, "recall")
        os.makedirs(coding_dir)
        os.makedirs(recall_dir)

        coding_scenario = {
            "id": "coding-001", "category": "coding",
            "name": "Fix bug", "description": "Test coding",
            "memories": [{"text": "Use TenantGuard", "source": "eval/coding-001"}],
            "prompt": "Fix the error",
            "expected": [{"type": "contains", "value": "TenantGuard", "weight": 1.0}],
        }
        recall_scenario = {
            "id": "recall-001", "category": "recall",
            "name": "Recall decision", "description": "Test recall",
            "memories": [{"text": "We use uv not pip", "source": "eval/recall-001"}],
            "prompt": "How do I install packages?",
            "expected": [{"type": "contains", "value": "uv", "weight": 1.0}],
        }

        with open(os.path.join(coding_dir, "coding-001.yaml"), "w") as f:
            yaml.dump(coding_scenario, f)
        with open(os.path.join(recall_dir, "recall-001.yaml"), "w") as f:
            yaml.dump(recall_scenario, f)

        scenarios = load_all_scenarios(scenarios_dir)
        assert len(scenarios) == 2

        config = EvalConfig(memories_url="http://localhost:8900", memories_api_key="test")

        memories = MagicMock()
        memories.clear_by_prefix.return_value = 0
        memories.seed_memories.return_value = [0]

        executor = MagicMock()
        executor.create_isolated_project.return_value = str(tmp_path / "project")
        os.makedirs(str(tmp_path / "project"), exist_ok=True)
        executor.run_prompt.side_effect = [
            "I don't know",           # coding without
            "Use TenantGuard",        # coding with
            "Use pip install httpx",   # recall without
            "Use uv add httpx",       # recall with
        ]

        runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=None)
        report = runner.run_all(scenarios)

        assert len(report.categories) == 2
        assert "coding" in report.categories
        assert "recall" in report.categories
        assert report.overall_efficacy_delta > 0
