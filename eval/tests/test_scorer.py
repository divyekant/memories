import pytest
from eval.models import Rubric, RubricResult
from eval.scorer import LLM_JUDGE_TYPES, score_rubric, score_all_rubrics


class TestScoreRubricContains:
    def test_contains_match(self):
        r = Rubric(type="contains", value="auth middleware", weight=0.5)
        result = score_rubric(r, "Use auth middleware to fix the issue")
        assert result.score == 1.0
        assert result.rubric_type == "contains"
        assert result.weight == 0.5

    def test_contains_no_match(self):
        r = Rubric(type="contains", value="auth middleware", weight=0.5)
        result = score_rubric(r, "Use a database connection pool")
        assert result.score == 0.0

    def test_contains_case_insensitive(self):
        r = Rubric(type="contains", value="Auth Middleware", weight=0.5)
        result = score_rubric(r, "add auth middleware here")
        assert result.score == 1.0


class TestScoreRubricNotContains:
    def test_not_contains_absent_scores_1(self):
        r = Rubric(type="not_contains", value="TODO", weight=0.3)
        result = score_rubric(r, "All tasks completed successfully")
        assert result.score == 1.0
        assert result.rubric_type == "not_contains"

    def test_not_contains_present_scores_0(self):
        r = Rubric(type="not_contains", value="TODO", weight=0.3)
        result = score_rubric(r, "There is still a TODO left")
        assert result.score == 0.0


class TestScoreRubricNoRetry:
    def test_no_retry_no_question_scores_1(self):
        r = Rubric(type="no_retry", weight=0.5)
        result = score_rubric(r, "Fixed the bug by adding a null check.")
        assert result.score == 1.0
        assert result.rubric_type == "no_retry"

    def test_no_retry_question_mark_scores_0(self):
        r = Rubric(type="no_retry", weight=0.5)
        result = score_rubric(r, "Could you clarify which file?")
        assert result.score == 0.0


class TestScoreRubricLLMJudge:
    @pytest.mark.parametrize("rubric_type", ["correct_fix", "recall_accuracy", "match_convention"])
    def test_llm_judge_returns_sentinel(self, rubric_type):
        r = Rubric(type=rubric_type, description="Some LLM check", weight=0.4)
        result = score_rubric(r, "any output text")
        assert result.score == -1.0
        assert result.reasoning == "pending_llm_judge"
        assert result.rubric_type == rubric_type
        assert result.weight == 0.4


class TestLLMJudgeTypesConstant:
    def test_llm_judge_types_set(self):
        assert LLM_JUDGE_TYPES == {"correct_fix", "recall_accuracy", "match_convention"}


class TestScoreAllRubrics:
    def test_weighted_average(self):
        rubrics = [
            Rubric(type="contains", value="auth", weight=0.6),
            Rubric(type="contains", value="middleware", weight=0.4),
        ]
        avg, details = score_all_rubrics(rubrics, "add auth middleware")
        assert avg == pytest.approx(1.0)
        assert len(details) == 2

    def test_partial_score(self):
        rubrics = [
            Rubric(type="contains", value="auth", weight=0.6),
            Rubric(type="contains", value="database", weight=0.4),
        ]
        avg, details = score_all_rubrics(rubrics, "add auth middleware")
        # only "auth" matches: 1.0*0.6 / (0.6+0.4) = 0.6
        assert avg == pytest.approx(0.6)
        assert details[0].score == 1.0
        assert details[1].score == 0.0

    def test_llm_rubrics_excluded_from_average(self):
        rubrics = [
            Rubric(type="contains", value="auth", weight=0.5),
            Rubric(type="correct_fix", description="check", weight=0.5),
        ]
        avg, details = score_all_rubrics(rubrics, "add auth middleware")
        # only the contains rubric is scored: 1.0*0.5 / 0.5 = 1.0
        assert avg == pytest.approx(1.0)
        assert len(details) == 2
        # LLM rubric is still returned unscored
        llm_detail = [d for d in details if d.rubric_type == "correct_fix"][0]
        assert llm_detail.score == -1.0
        assert llm_detail.reasoning == "pending_llm_judge"

    def test_all_llm_rubrics_returns_zero_average(self):
        rubrics = [
            Rubric(type="correct_fix", description="a", weight=0.5),
            Rubric(type="recall_accuracy", description="b", weight=0.5),
        ]
        avg, details = score_all_rubrics(rubrics, "some output")
        assert avg == 0.0
        assert len(details) == 2

    def test_empty_rubrics(self):
        avg, details = score_all_rubrics([], "some output")
        assert avg == 0.0
        assert details == []
