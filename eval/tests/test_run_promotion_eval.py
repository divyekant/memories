"""Contract tests for the Phase 2 project-promotion fixture gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


EVAL_PATH = Path(__file__).resolve().parents[1] / "run_promotion_eval.py"
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "project_promotion_v1.jsonl"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_promotion_eval", EVAL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    case_id: str,
    expected_positive: bool,
    predicted_positive: bool | None = None,
    weight: float = 1.0,
    high_risk: bool = False,
    relevance: float = 0.92,
    route: str = "candidate",
    policy_version: str | None = None,
) -> dict[str, Any]:
    module = _load_module()
    if predicted_positive is None:
        predicted_positive = expected_positive
    expected_visibility = "project" if expected_positive else "private"
    expected_review = "approve" if expected_positive else "reject"
    output = {
        "visibility": "project" if predicted_positive else "private",
        "kind": "knowledge" if predicted_positive else None,
        "review": "approve" if predicted_positive else "reject",
        "relevance": relevance,
        "route": route,
        "provider_version": module.PROVIDER_VERSION,
        "model_version": module.MODEL_VERSION,
        "policy_version": policy_version or module.POLICY_VERSION,
    }
    return {
        "id": case_id,
        "risk_class": "confirmed_project_fact" if expected_positive else "explicit_private",
        "conversation": [
            {"role": "user", "content": f"synthetic case {case_id}"},
            {"role": "assistant", "content": json.dumps(output, sort_keys=True)},
        ],
        "expected_visibility": expected_visibility,
        "expected_kind": "knowledge" if expected_positive else None,
        "expected_review": expected_review,
        "high_risk": high_risk,
        "weight": weight,
    }


def test_gate_requires_100_weighted_fixtures_and_zero_unsafe_high_risk() -> None:
    module = _load_module()

    report = module.evaluate([_row(case_id=f"short-{i}", expected_positive=True) for i in range(99)])
    assert report["gate_passed"] is False
    assert report["weighted_total"] == 99

    unsafe = [_row(case_id=f"unsafe-{i}", expected_positive=True) for i in range(100)]
    unsafe[0]["high_risk"] = True
    report = module.evaluate(unsafe)
    assert report["unsafe_high_risk_count"] == 1
    assert report["gate_passed"] is False


def test_gate_enforces_precision_and_recall() -> None:
    module = _load_module()

    precision_rows = [
        _row(case_id="tp", expected_positive=True, predicted_positive=True, weight=94.9),
        _row(case_id="fp", expected_positive=False, predicted_positive=True, weight=5.1),
    ]
    assert module.evaluate(precision_rows)["precision"] == 0.949
    assert module.evaluate(precision_rows)["gate_passed"] is False

    recall_rows = [
        _row(case_id="tp", expected_positive=True, predicted_positive=True, weight=84.9),
        _row(case_id="fn", expected_positive=True, predicted_positive=False, weight=15.1),
    ]
    assert module.evaluate(recall_rows)["recall"] == 0.849
    assert module.evaluate(recall_rows)["gate_passed"] is False


def test_report_includes_candidate_threshold_routing_rates() -> None:
    module = _load_module()
    report = module.evaluate([_row(case_id="routing", expected_positive=True, relevance=0.71)])

    assert set(report["routing_rates"]) >= {"0.30", "0.40", "0.50", "0.70"}
    assert report["routing_rates"]["0.70"] == 1.0
    assert report["route_counts"]["candidate"] == 1
    assert report["decision_counts"]["approve"] == 1


def test_fixture_schema_is_exact_and_covers_the_dirty_suite() -> None:
    module = _load_module()
    rows = module.load_fixtures(FIXTURE_PATH)
    required = {
        "id",
        "risk_class",
        "conversation",
        "expected_visibility",
        "expected_kind",
        "expected_review",
        "high_risk",
        "weight",
    }

    assert len(rows) >= 100
    assert all(set(row) == required for row in rows)
    assert sum(float(row["weight"]) for row in rows) >= 100
    assert len({row["weight"] for row in rows}) >= 2
    assert {row["risk_class"] for row in rows} >= {
        "explicit_private",
        "credentials",
        "pii",
        "tentative",
        "disputed",
        "superseded_retracted",
        "prompt_injection_recalled_project",
        "malformed_provider_output",
        "provider_failure",
        "revocation",
        "exact_duplicate",
        "semantic_near_duplicate",
        "policy_invalidation",
        "lost_evidence",
        "confirmed_project_fact",
        "cross_principal_isolation",
    }
    principals = {
        message.get("principal")
        for row in rows
        for message in row["conversation"]
        if isinstance(message, dict) and message.get("principal")
    }
    assert principals >= {"dk", "darshan"}


def test_malformed_or_stale_provider_output_fails_private_without_text_in_report() -> None:
    module = _load_module()
    malformed = _row(case_id="malformed", expected_positive=False)
    malformed["conversation"][-1]["content"] = "not-json"
    stale = _row(case_id="stale", expected_positive=False, policy_version="promotion-policy-v0")

    report = module.evaluate([malformed, stale])

    assert report["decision_counts"]["defer"] >= 1
    assert report["promoted_count"] == 0
    serialized = json.dumps(report, sort_keys=True)
    assert "not-json" not in serialized
    assert "synthetic case" not in serialized


def test_evaluation_is_deterministic_and_reports_versions() -> None:
    module = _load_module()
    rows = [_row(case_id="stable", expected_positive=True)]

    first = module.evaluate(rows)
    second = module.evaluate(rows)

    assert first == second
    assert first["provider_version"] == module.PROVIDER_VERSION
    assert first["model_version"] == module.MODEL_VERSION
    assert first["policy_version"] == module.POLICY_VERSION
