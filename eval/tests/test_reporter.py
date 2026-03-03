import json
import os

import pytest

from eval.models import CategoryResult, EvalReport, ScenarioResult
from eval.reporter import format_summary, save_report


@pytest.fixture
def sample_report():
    return EvalReport(
        timestamp="2026-03-03T12:00:00+00:00",
        overall_with_memory=0.82,
        overall_without_memory=0.41,
        overall_efficacy_delta=0.41,
        categories={
            "coding": CategoryResult(
                category="coding", with_memory=0.85, without_memory=0.38, delta=0.47
            ),
        },
        tests=[
            ScenarioResult(
                scenario_id="coding-001",
                scenario_name="Fix bug",
                category="coding",
                score_with_memory=0.85,
                score_without_memory=0.38,
                output_with_memory="fixed",
                output_without_memory="confused",
                rubric_details=[],
            ),
        ],
    )


class TestSaveReport:
    def test_saves_json(self, sample_report, tmp_path):
        results_dir = str(tmp_path / "results")
        path = save_report(sample_report, results_dir)

        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["overall_efficacy_delta"] == pytest.approx(0.41)

    def test_filename_includes_timestamp(self, sample_report, tmp_path):
        results_dir = str(tmp_path / "results")
        path = save_report(sample_report, results_dir)

        assert "2026-03-03" in os.path.basename(path)

    def test_creates_results_dir(self, sample_report, tmp_path):
        results_dir = str(tmp_path / "nested" / "results")
        path = save_report(sample_report, results_dir)

        assert os.path.isdir(results_dir)
        assert os.path.isfile(path)


class TestFormatSummary:
    def test_summary_contains_scores(self, sample_report):
        summary = format_summary(sample_report)

        assert "0.82" in summary
        assert "0.41" in summary
        assert "coding" in summary

    def test_summary_contains_header(self, sample_report):
        summary = format_summary(sample_report)

        assert "MEMORIES EFFICACY REPORT" in summary

    def test_summary_contains_timestamp(self, sample_report):
        summary = format_summary(sample_report)

        assert "2026-03-03T12:00:00+00:00" in summary

    def test_summary_contains_test_count(self, sample_report):
        summary = format_summary(sample_report)

        assert "1" in summary
