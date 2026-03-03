"""Orchestrates scenario execution for the Memories efficacy eval harness."""

from __future__ import annotations

from datetime import datetime, timezone

from eval.cc_executor import CCExecutor
from eval.judge import LLMJudge
from eval.memories_client import MemoriesClient
from eval.models import (
    CategoryResult,
    EvalConfig,
    EvalReport,
    RubricResult,
    Scenario,
    ScenarioResult,
)
from eval.scorer import LLM_JUDGE_TYPES, score_all_rubrics

EVAL_PREFIX = "eval/"


class EvalRunner:
    """Orchestrates running scenarios with and without memories."""

    def __init__(
        self,
        config: EvalConfig,
        memories_client: MemoriesClient | None = None,
        cc_executor: CCExecutor | None = None,
        judge: LLMJudge | None = None,
    ) -> None:
        self.config = config
        self.memories = memories_client or MemoriesClient(
            url=config.memories_url, api_key=config.memories_api_key
        )
        self.executor = cc_executor or CCExecutor(timeout=config.cc_timeout)
        self.judge = judge

    def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """Run a single scenario with and without memories.

        1. Clear memories (eval/ prefix)
        2. Create isolated project (no memories)
        3. Run prompt without memories, score
        4. Clear memories, seed scenario memories
        5. Create isolated project (with memories)
        6. Run prompt with memories, score
        7. If judge exists, fill in LLM-judged rubrics (score==-1 sentinel)
        8. Return ScenarioResult with both scores
        """
        # --- Phase 1: Without memories ---
        self.memories.clear_by_prefix(EVAL_PREFIX)

        project_without = self.executor.create_isolated_project(with_memories=False)
        try:
            output_without = self.executor.run_prompt(scenario.prompt, project_without)
        finally:
            self.executor.cleanup_project(project_without)

        score_without, details_without = score_all_rubrics(scenario.expected, output_without)

        # --- Phase 2: With memories ---
        self.memories.clear_by_prefix(EVAL_PREFIX)
        self.memories.seed_memories(
            [{"text": m.text, "source": m.source} for m in scenario.memories]
        )

        project_with = self.executor.create_isolated_project(with_memories=True)
        try:
            output_with = self.executor.run_prompt(scenario.prompt, project_with)
        finally:
            self.executor.cleanup_project(project_with)

        score_with, details_with = score_all_rubrics(scenario.expected, output_with)

        # --- Phase 3: LLM judge for pending rubrics (both outputs) ---
        if self.judge:
            details_without = self._resolve_judge_rubrics(
                details_without, scenario, output_without
            )
            score_without = self._weighted_avg(details_without)

            details_with = self._resolve_judge_rubrics(
                details_with, scenario, output_with
            )
            score_with = self._weighted_avg(details_with)

        return ScenarioResult(
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
        """Run all scenarios, aggregate into EvalReport."""
        # Purge stale auto-memory from prior eval runs
        CCExecutor.cleanup_stale_auto_memory()
        results = [self.run_scenario(s) for s in scenarios]
        return self._aggregate(results)

    def _resolve_judge_rubrics(
        self, details: list[RubricResult], scenario: Scenario, output: str
    ) -> list[RubricResult]:
        """Replace sentinel-scored rubrics with LLM judge evaluations."""
        resolved: list[RubricResult] = []
        for i, detail in enumerate(details):
            if detail.score < 0 and detail.rubric_type in LLM_JUDGE_TYPES:
                judged = self.judge.evaluate(
                    scenario.expected[i], scenario.prompt, output
                )
                resolved.append(judged)
            else:
                resolved.append(detail)
        return resolved

    def _weighted_avg(self, details: list[RubricResult]) -> float:
        """Weighted average of scored rubrics (score >= 0)."""
        scored = [d for d in details if d.score >= 0]
        if not scored:
            return 0.0
        total_weight = sum(d.weight for d in scored)
        if total_weight == 0:
            return 0.0
        return sum(d.score * d.weight for d in scored) / total_weight

    def _aggregate(self, results: list[ScenarioResult]) -> EvalReport:
        """Aggregate results into category scores and overall scores using config weights."""
        if not results:
            return EvalReport(
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Group by category
        by_category: dict[str, list[ScenarioResult]] = {}
        for r in results:
            by_category.setdefault(r.category, []).append(r)

        categories: dict[str, CategoryResult] = {}
        for cat_name, cat_results in by_category.items():
            avg_with = sum(r.score_with_memory for r in cat_results) / len(cat_results)
            avg_without = sum(r.score_without_memory for r in cat_results) / len(cat_results)
            categories[cat_name] = CategoryResult(
                category=cat_name,
                with_memory=avg_with,
                without_memory=avg_without,
                delta=avg_with - avg_without,
            )

        # Weighted overall using config weights, renormalized to present categories
        present_weights = {
            cat: self.config.category_weights.get(cat, 0.0) for cat in categories
        }
        total_weight = sum(present_weights.values())

        if total_weight > 0:
            overall_with = sum(
                categories[cat].with_memory * w / total_weight
                for cat, w in present_weights.items()
            )
            overall_without = sum(
                categories[cat].without_memory * w / total_weight
                for cat, w in present_weights.items()
            )
        else:
            # Fallback: equal weight
            overall_with = sum(c.with_memory for c in categories.values()) / len(categories)
            overall_without = sum(c.without_memory for c in categories.values()) / len(
                categories
            )

        return EvalReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_with_memory=overall_with,
            overall_without_memory=overall_without,
            overall_efficacy_delta=overall_with - overall_without,
            categories=categories,
            tests=results,
        )
