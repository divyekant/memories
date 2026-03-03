"""Tests for EvalRunner orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from eval.models import (
    CategoryResult,
    EvalConfig,
    EvalReport,
    MemorySeed,
    Rubric,
    RubricResult,
    Scenario,
    ScenarioResult,
)
from eval.runner import EvalRunner


@pytest.fixture
def scenario():
    return Scenario(
        id="coding-001",
        category="coding",
        name="Fix bug",
        description="Fix a bug using known pattern",
        memories=[MemorySeed(text="Use auth middleware", source="eval/coding-001")],
        prompt="Fix the TypeError in handler.py",
        expected=[
            Rubric(type="contains", value="auth middleware", weight=0.6),
            Rubric(type="no_retry", weight=0.4),
        ],
    )


@pytest.fixture
def mock_deps():
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


@pytest.fixture
def config():
    return EvalConfig(
        memories_url="http://localhost:8900",
        memories_api_key="test-key",
        cc_timeout=60,
        category_weights={"coding": 0.40, "recall": 0.35, "compounding": 0.25},
    )


class TestRunSingleScenario:
    def test_score_with_greater_than_without(self, scenario, mock_deps, config):
        """With-memory score should exceed without-memory score."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        result = runner.run_scenario(scenario)

        assert isinstance(result, ScenarioResult)
        assert result.score_with_memory > result.score_without_memory
        assert result.efficacy_delta > 0

    def test_correct_delta(self, scenario, mock_deps, config):
        """Delta should equal score_with - score_without."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        result = runner.run_scenario(scenario)

        expected_delta = result.score_with_memory - result.score_without_memory
        assert result.efficacy_delta == pytest.approx(expected_delta)

    def test_outputs_captured(self, scenario, mock_deps, config):
        """Both model outputs should be captured in the result."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        result = runner.run_scenario(scenario)

        assert "traceback" in result.output_without_memory.lower()
        assert "auth middleware" in result.output_with_memory.lower()

    def test_rubric_details_populated(self, scenario, mock_deps, config):
        """Rubric details should contain entries for each expected rubric."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        result = runner.run_scenario(scenario)

        assert len(result.rubric_details) == len(scenario.expected)


class TestMemoriesInteraction:
    def test_clears_memories_before_each_run(self, scenario, mock_deps, config):
        """memories.clear_by_prefix should be called before each phase."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        runner.run_scenario(scenario)

        # Called at least twice: before without-memory run and before with-memory run
        assert memories.clear_by_prefix.call_count >= 2
        memories.clear_by_prefix.assert_any_call("eval/")

    def test_seeds_memories_for_with_run(self, scenario, mock_deps, config):
        """memories.seed_memories should be called exactly once (for the with-memory run)."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        runner.run_scenario(scenario)

        memories.seed_memories.assert_called_once()
        seeded = memories.seed_memories.call_args[0][0]
        assert len(seeded) == 1
        assert seeded[0]["text"] == "Use auth middleware"
        assert seeded[0]["source"] == "eval/coding-001"


class TestProjectLifecycle:
    def test_creates_and_cleans_project(self, scenario, mock_deps, config):
        """create_isolated_project called 2x, cleanup called 2x."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        runner.run_scenario(scenario)

        assert executor.create_isolated_project.call_count == 2
        assert executor.cleanup_project.call_count == 2

    def test_first_project_without_memories(self, scenario, mock_deps, config):
        """First project creation should be without memories."""
        memories, executor, judge = mock_deps
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        runner.run_scenario(scenario)

        calls = executor.create_isolated_project.call_args_list
        assert calls[0] == call(with_memories=False)
        assert calls[1] == call(with_memories=True)


class TestLLMJudgeIntegration:
    def test_judge_called_for_pending_rubrics(self, config):
        """When rubrics have score==-1 sentinel, judge.evaluate should be called."""
        memories = MagicMock()
        memories.clear_by_prefix.return_value = 0
        memories.seed_memories.return_value = [0]

        executor = MagicMock()
        executor.create_isolated_project.return_value = "/tmp/eval-test"
        executor.run_prompt.side_effect = [
            "No idea.",  # without memory
            "Applied the correct fix using auth middleware pattern.",  # with memory
        ]

        judge = MagicMock()
        judge.evaluate.return_value = RubricResult(
            rubric_type="correct_fix",
            score=0.9,
            weight=0.5,
            reasoning="Correctly applied fix.",
        )

        scenario = Scenario(
            id="coding-002",
            category="coding",
            name="Judge test",
            description="Test judge integration",
            memories=[MemorySeed(text="Use auth middleware", source="eval/coding-002")],
            prompt="Fix the bug",
            expected=[
                Rubric(type="correct_fix", description="Fix is correct", weight=0.5),
                Rubric(type="contains", value="auth middleware", weight=0.5),
            ],
        )

        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        result = runner.run_scenario(scenario)

        # Judge should have been called for the correct_fix rubric
        judge.evaluate.assert_called()
        # All rubric details should have score >= 0 (no pending sentinels)
        for detail in result.rubric_details:
            assert detail.score >= 0

    def test_no_judge_leaves_sentinels(self, config):
        """Without a judge, LLM rubrics keep score==-1 sentinel."""
        memories = MagicMock()
        memories.clear_by_prefix.return_value = 0
        memories.seed_memories.return_value = [0]

        executor = MagicMock()
        executor.create_isolated_project.return_value = "/tmp/eval-test"
        executor.run_prompt.side_effect = [
            "No idea.",
            "Applied the correct fix.",
        ]

        scenario = Scenario(
            id="coding-003",
            category="coding",
            name="No judge test",
            description="Test without judge",
            memories=[MemorySeed(text="Use pattern", source="eval/coding-003")],
            prompt="Fix the bug",
            expected=[
                Rubric(type="correct_fix", description="Fix is correct", weight=1.0),
            ],
        )

        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=None,
        )

        result = runner.run_scenario(scenario)

        # Without judge, correct_fix rubrics stay as -1 sentinels
        sentinels = [d for d in result.rubric_details if d.score < 0]
        assert len(sentinels) > 0


class TestWeightedAvg:
    def test_weighted_average(self, config):
        """Weighted average of scored rubrics (score >= 0)."""
        runner = EvalRunner(config=config)
        details = [
            RubricResult(rubric_type="contains", score=1.0, weight=0.6, reasoning="found"),
            RubricResult(rubric_type="no_retry", score=0.0, weight=0.4, reasoning="question"),
        ]

        avg = runner._weighted_avg(details)

        # (1.0 * 0.6 + 0.0 * 0.4) / (0.6 + 0.4) = 0.6
        assert avg == pytest.approx(0.6)

    def test_weighted_average_excludes_sentinels(self, config):
        """Sentinel scores (score==-1) should be excluded from average."""
        runner = EvalRunner(config=config)
        details = [
            RubricResult(rubric_type="contains", score=1.0, weight=0.5, reasoning="found"),
            RubricResult(rubric_type="correct_fix", score=-1.0, weight=0.5, reasoning="pending"),
        ]

        avg = runner._weighted_avg(details)

        # Only the contains rubric counts: 1.0 * 0.5 / 0.5 = 1.0
        assert avg == pytest.approx(1.0)

    def test_weighted_average_empty(self, config):
        """Empty details should return 0.0."""
        runner = EvalRunner(config=config)

        avg = runner._weighted_avg([])

        assert avg == 0.0


class TestAggregate:
    def test_aggregate_single_category(self, config):
        """Aggregate results for a single category."""
        runner = EvalRunner(config=config)
        results = [
            ScenarioResult(
                scenario_id="coding-001",
                scenario_name="Fix bug",
                category="coding",
                score_with_memory=0.8,
                score_without_memory=0.2,
                output_with_memory="fixed",
                output_without_memory="dunno",
                rubric_details=[],
            ),
            ScenarioResult(
                scenario_id="coding-002",
                scenario_name="Add feature",
                category="coding",
                score_with_memory=0.6,
                score_without_memory=0.3,
                output_with_memory="done",
                output_without_memory="what",
                rubric_details=[],
            ),
        ]

        report = runner._aggregate(results)

        assert isinstance(report, EvalReport)
        assert "coding" in report.categories
        cat = report.categories["coding"]
        assert cat.with_memory == pytest.approx(0.7)  # (0.8 + 0.6) / 2
        assert cat.without_memory == pytest.approx(0.25)  # (0.2 + 0.3) / 2
        assert cat.delta == pytest.approx(0.45)

    def test_aggregate_multiple_categories(self, config):
        """Aggregate results across multiple categories with config weights."""
        runner = EvalRunner(config=config)
        results = [
            ScenarioResult(
                scenario_id="coding-001",
                scenario_name="Fix bug",
                category="coding",
                score_with_memory=1.0,
                score_without_memory=0.0,
                output_with_memory="fixed",
                output_without_memory="dunno",
                rubric_details=[],
            ),
            ScenarioResult(
                scenario_id="recall-001",
                scenario_name="Recall decision",
                category="recall",
                score_with_memory=0.8,
                score_without_memory=0.2,
                output_with_memory="SQLite",
                output_without_memory="not sure",
                rubric_details=[],
            ),
        ]

        report = runner._aggregate(results)

        assert "coding" in report.categories
        assert "recall" in report.categories
        # overall_with = 1.0 * 0.4 + 0.8 * 0.35 = 0.68 (only present categories, renormalized)
        # Weights: coding=0.40, recall=0.35 -> renorm: 0.40/0.75, 0.35/0.75
        coding_w = 0.40 / (0.40 + 0.35)
        recall_w = 0.35 / (0.40 + 0.35)
        expected_with = 1.0 * coding_w + 0.8 * recall_w
        expected_without = 0.0 * coding_w + 0.2 * recall_w
        assert report.overall_with_memory == pytest.approx(expected_with, abs=0.01)
        assert report.overall_without_memory == pytest.approx(expected_without, abs=0.01)
        assert report.overall_efficacy_delta == pytest.approx(
            expected_with - expected_without, abs=0.01
        )

    def test_aggregate_timestamp_is_iso(self, config):
        """Timestamp should be a valid ISO format string."""
        runner = EvalRunner(config=config)
        results = [
            ScenarioResult(
                scenario_id="coding-001",
                scenario_name="Fix bug",
                category="coding",
                score_with_memory=0.5,
                score_without_memory=0.5,
                output_with_memory="ok",
                output_without_memory="ok",
                rubric_details=[],
            ),
        ]

        report = runner._aggregate(results)

        assert report.timestamp != ""
        # ISO format contains "T" separator
        assert "T" in report.timestamp

    def test_aggregate_empty(self, config):
        """Empty results should produce an empty report."""
        runner = EvalRunner(config=config)

        report = runner._aggregate([])

        assert report.overall_with_memory == 0.0
        assert report.overall_without_memory == 0.0
        assert report.overall_efficacy_delta == 0.0
        assert report.tests == []
        assert report.categories == {}


class TestRunAll:
    def test_run_all_returns_report(self, scenario, mock_deps, config):
        """run_all should return an EvalReport with all results."""
        memories, executor, judge = mock_deps
        # Need fresh side_effect for two calls to run_prompt per scenario
        executor.run_prompt.side_effect = [
            "I'm not sure, can you share the traceback?",
            "The auth middleware check is missing. Here's the fix.",
        ]
        runner = EvalRunner(
            config=config,
            memories_client=memories,
            cc_executor=executor,
            judge=judge,
        )

        report = runner.run_all([scenario])

        assert isinstance(report, EvalReport)
        assert len(report.tests) == 1
        assert report.tests[0].scenario_id == "coding-001"
