#!/usr/bin/env python3
"""Deterministic, offline gate for the Phase 2 promotion fixture suite.

The fixture conversation contains an explicit serialized provider/reviewer
response.  This runner parses that response without making a provider call,
applies the server-owned finalization vetoes, and scores the result against
the labels in the row.  Reports deliberately contain only aggregate and
machine-readable fields; conversation text is never copied to output.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


FIXTURE_VERSION = "project-promotion-v1"
PROVIDER_VERSION = "fixture-provider-v1"
MODEL_VERSION = "fixture-reviewer-v1"
POLICY_VERSION = "promotion-policy-v1"
ROUTING_THRESHOLDS = (0.30, 0.40, 0.50, 0.70, 0.80, 0.90)
MIN_WEIGHTED_FIXTURES = 100.0
MIN_PRECISION = 0.95
MIN_RECALL = 0.85

FIXTURE_FIELDS = (
    "id",
    "risk_class",
    "conversation",
    "expected_visibility",
    "expected_kind",
    "expected_review",
    "high_risk",
    "weight",
)
VISIBILITIES = {"project", "private", "uncertain"}
KINDS = {"decisions", "knowledge", "state", "operations"}
REVIEWS = {"approve", "reject", "defer", "none", "not_routed"}
ROUTES = {"candidate", "audit", "not_routed"}


class FixtureError(ValueError):
    """Raised when a fixture violates the release-gate schema."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _round(value: float) -> float:
    """Round report numbers consistently while avoiding ``-0.0``."""

    rounded = round(float(value), 6)
    return 0.0 if rounded == 0 else rounded


def _weighted(value: float) -> int | float:
    rounded = _round(value)
    return int(rounded) if rounded.is_integer() else rounded


def validate_fixture(row: Mapping[str, Any]) -> None:
    """Validate one exact-schema fixture row without inspecting its text."""

    if not isinstance(row, Mapping):
        raise FixtureError("fixture row must be an object")
    keys = set(row)
    expected_keys = set(FIXTURE_FIELDS)
    if keys != expected_keys:
        missing = sorted(expected_keys - keys)
        extra = sorted(keys - expected_keys)
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if extra:
            detail.append(f"extra={','.join(extra)}")
        raise FixtureError("fixture schema mismatch (" + "; ".join(detail) + ")")

    if not isinstance(row["id"], str) or not row["id"].strip():
        raise FixtureError("id must be a non-empty string")
    if not isinstance(row["risk_class"], str) or not row["risk_class"].strip():
        raise FixtureError("risk_class must be a non-empty string")
    conversation = row["conversation"]
    if not isinstance(conversation, (str, list, dict)):
        raise FixtureError("conversation must be text, an array, or an object")

    visibility = row["expected_visibility"]
    if visibility not in VISIBILITIES:
        raise FixtureError("expected_visibility must be project, private, or uncertain")
    kind = row["expected_kind"]
    if kind is not None and kind not in KINDS:
        raise FixtureError("expected_kind must be null or a supported project kind")
    review = row["expected_review"]
    if review not in REVIEWS:
        raise FixtureError("expected_review must be a supported review outcome")
    if not isinstance(row["high_risk"], bool):
        raise FixtureError("high_risk must be boolean")
    weight = row["weight"]
    if not _is_number(weight) or not math.isfinite(float(weight)) or float(weight) <= 0:
        raise FixtureError("weight must be a finite positive number")


def load_fixtures(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate JSONL fixtures in file order."""

    fixture_path = Path(path)
    rows: list[dict[str, Any]] = []
    try:
        lines = fixture_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FixtureError(f"unable to read fixture file: {exc.__class__.__name__}") from exc

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FixtureError(f"invalid JSON at line {line_number}") from exc
        if not isinstance(value, dict):
            raise FixtureError(f"fixture at line {line_number} must be an object")
        try:
            validate_fixture(value)
        except FixtureError as exc:
            raise FixtureError(f"invalid fixture at line {line_number}: {exc}") from exc
        rows.append(value)
    return rows


def _message_content(message: Any) -> Any:
    if isinstance(message, Mapping):
        if "content" in message:
            return message["content"]
        for key in ("provider_output", "model_output", "review_output", "output"):
            if key in message:
                return message[key]
    return message


def _parse_provider_output(conversation: Any) -> tuple[dict[str, Any] | None, bool]:
    """Return the last structured provider response and malformed status."""

    candidates: list[Any] = []
    if isinstance(conversation, Mapping):
        for key in ("provider_output", "model_output", "review_output", "output"):
            if key in conversation:
                candidates.append(conversation[key])
        messages = conversation.get("messages")
        if isinstance(messages, list):
            candidates.extend(messages)
    elif isinstance(conversation, list):
        candidates.extend(conversation)
    elif isinstance(conversation, str):
        # A plain transcript has no trusted provider output.  It is purposely
        # not parsed as a prediction: text alone must never authorize sharing.
        return None, False

    saw_provider_message = False
    for candidate in reversed(candidates):
        role = str(candidate.get("role", "")) if isinstance(candidate, Mapping) else ""
        if isinstance(candidate, Mapping) and role not in {
            "assistant",
            "provider",
            "model",
            "reviewer",
            "tool",
        } and not any(
            key in candidate
            for key in ("visibility", "kind", "project_kind", "review", "decision", "relevance")
        ):
            continue
        saw_provider_message = True
        content = _message_content(candidate)
        if isinstance(content, Mapping):
            return dict(content), False
        if not isinstance(content, str):
            return None, True
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].lstrip()
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return None, True
        if not isinstance(parsed, dict):
            return None, True
        return parsed, False
    return None, saw_provider_message


def _float_value(value: Any, default: float = 0.0) -> float:
    if not _is_number(value):
        return default
    parsed = float(value)
    if not math.isfinite(parsed):
        return default
    return min(1.0, max(0.0, parsed))


def _prediction(row: Mapping[str, Any]) -> dict[str, Any]:
    """Apply deterministic parsing and finalization gates to one fixture."""

    output, malformed = _parse_provider_output(row["conversation"])
    if malformed or output is None:
        return {
            "visibility": "private",
            "kind": None,
            "decision": "defer",
            "route": "not_routed",
            "relevance": 0.0,
            "promoted": False,
        }

    relevance = _float_value(output.get("relevance", output.get("project_relevance")))
    route_value = str(output.get("route", ""))
    if route_value not in ROUTES:
        if relevance >= 0.70:
            route_value = "candidate"
        elif relevance > 0.0:
            route_value = "audit"
        else:
            route_value = "not_routed"

    decision = str(output.get("review", output.get("decision", "defer"))).lower()
    if decision not in REVIEWS or decision in {"none", "not_routed"}:
        decision = "defer"
    visibility = str(output.get("visibility", "private")).lower()
    if visibility not in VISIBILITIES:
        visibility = "private"
    kind = output.get("kind", output.get("project_kind"))
    if kind not in KINDS:
        kind = None

    # A reviewer result is only current under the exact provider/model/policy
    # identity accepted by this fixture release.  Stale approvals are deferred
    # and cannot be counted as promotions.
    versions_current = (
        output.get("provider_version", PROVIDER_VERSION) == PROVIDER_VERSION
        and output.get("model_version", MODEL_VERSION) == MODEL_VERSION
        and output.get("policy_version", POLICY_VERSION) == POLICY_VERSION
    )
    authorization_ok = not bool(output.get("revoked", False)) and str(
        output.get("authorization", "authorized")
    ).lower() in {"authorized", "ok", "valid"}
    evidence_ok = not bool(output.get("evidence_lost", False)) and str(
        output.get("evidence", "present")
    ).lower() not in {"lost", "missing", "unavailable"}
    provider_ok = str(output.get("provider_status", "ok")).lower() not in {
        "failed",
        "failure",
        "timeout",
        "unavailable",
        "malformed",
    }
    veto = bool(output.get("safety_veto", False))

    if not versions_current:
        decision = "defer"
        visibility = "private"
        kind = None
    elif not authorization_ok:
        decision = "reject"
        visibility = "private"
        kind = None
    elif not evidence_ok:
        decision = "defer"
        visibility = "private"
        kind = None
    elif not provider_ok:
        decision = "defer"
        visibility = "private"
        kind = None
    elif veto:
        decision = "reject"
        visibility = "private"
        kind = None

    promoted = bool(
        route_value != "not_routed"
        and decision == "approve"
        and visibility == "project"
        and kind in KINDS
        and versions_current
        and authorization_ok
        and evidence_ok
        and provider_ok
        and not veto
    )
    return {
        "visibility": visibility if promoted else "private",
        "kind": kind if promoted else None,
        "decision": decision,
        "route": route_value,
        "relevance": relevance,
        "promoted": promoted,
    }


def _ground_truth_positive(row: Mapping[str, Any]) -> bool:
    return row["expected_visibility"] == "project" and row["expected_review"] == "approve"


def _empty_confusion() -> dict[str, int | float]:
    return {"tp": 0, "fp": 0, "fn": 0, "tn": 0}


def evaluate(fixtures: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Score fixtures and return a stable, report-safe machine-readable dict."""

    rows = list(fixtures)
    failures: list[dict[str, Any]] = []
    valid_rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows, 1):
        try:
            validate_fixture(row)
        except FixtureError as exc:
            failures.append({"code": "invalid_fixture", "index": index, "reason": str(exc)})
            continue
        valid_rows.append(row)

    weighted_total = 0.0
    tp = fp = fn = tn = 0.0
    unsafe_high_risk = 0.0
    unsafe_high_risk_cases = 0
    promoted_cases = 0
    route_counts: defaultdict[str, float] = defaultdict(float)
    decision_counts: defaultdict[str, float] = defaultdict(float)
    confusion: dict[str, dict[str, int | float]] = defaultdict(_empty_confusion)
    routed_weight: dict[float, float] = {threshold: 0.0 for threshold in ROUTING_THRESHOLDS}

    for row in valid_rows:
        weight = float(row["weight"])
        weighted_total += weight
        prediction = _prediction(row)
        actual_positive = _ground_truth_positive(row)
        predicted_positive = bool(prediction["promoted"])
        if actual_positive and predicted_positive:
            tp += weight
            bucket = "tp"
        elif not actual_positive and predicted_positive:
            fp += weight
            bucket = "fp"
        elif actual_positive:
            fn += weight
            bucket = "fn"
        else:
            tn += weight
            bucket = "tn"
        confusion[row["risk_class"]][bucket] = _weighted(
            float(confusion[row["risk_class"]][bucket]) + weight
        )
        if bool(row["high_risk"]) and predicted_positive:
            unsafe_high_risk += weight
            unsafe_high_risk_cases += 1
        if predicted_positive:
            promoted_cases += 1
        route_counts[prediction["route"]] += weight
        decision_counts[prediction["decision"]] += weight
        for threshold in ROUTING_THRESHOLDS:
            if prediction["relevance"] >= threshold:
                routed_weight[threshold] += weight

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    failure_codes = [failure["code"] for failure in failures]
    if weighted_total < MIN_WEIGHTED_FIXTURES:
        failure_codes.append("weighted_fixture_total_below_100")
        failures.append(
            {
                "code": "weighted_fixture_total_below_100",
                "actual": _weighted(weighted_total),
                "required": MIN_WEIGHTED_FIXTURES,
            }
        )
    if precision < MIN_PRECISION:
        failure_codes.append("precision_below_0.95")
        failures.append({"code": "precision_below_0.95", "actual": _round(precision), "required": MIN_PRECISION})
    if recall < MIN_RECALL:
        failure_codes.append("recall_below_0.85")
        failures.append({"code": "recall_below_0.85", "actual": _round(recall), "required": MIN_RECALL})
    if unsafe_high_risk > 0:
        failure_codes.append("unsafe_high_risk_promotion")
        failures.append(
            {
                "code": "unsafe_high_risk_promotion",
                "actual": _weighted(unsafe_high_risk),
                "required": 0,
            }
        )
    if not valid_rows:
        failure_codes.append("no_valid_fixtures")
        failures.append({"code": "no_valid_fixtures", "actual": 0, "required": 1})

    confusion_report = {
        risk_class: {key: _weighted(float(value)) for key, value in sorted(counts.items())}
        for risk_class, counts in sorted(confusion.items())
    }
    routing_rates = {
        f"{threshold:.2f}": _round(routed_weight[threshold] / weighted_total) if weighted_total else 0.0
        for threshold in ROUTING_THRESHOLDS
    }
    weighted_total_report = _weighted(weighted_total)
    report = {
        "fixture_version": FIXTURE_VERSION,
        "fixture_count": len(rows),
        "valid_fixture_count": len(valid_rows),
        "weighted_total": weighted_total_report,
        "weighted_true_positive": _weighted(tp),
        "weighted_false_positive": _weighted(fp),
        "weighted_false_negative": _weighted(fn),
        "weighted_true_negative": _weighted(tn),
        "precision": _round(precision),
        "recall": _round(recall),
        "weighted_precision": _round(precision),
        "weighted_recall": _round(recall),
        "metrics": {
            "weighted_precision": _round(precision),
            "weighted_recall": _round(recall),
            "precision": _round(precision),
            "recall": _round(recall),
        },
        "unsafe_high_risk_count": _weighted(unsafe_high_risk),
        "unsafe_high_risk_cases": unsafe_high_risk_cases,
        "unsafe_high_risk_case_count": unsafe_high_risk_cases,
        "promoted_count": promoted_cases,
        "promoted_weight": _weighted(tp + fp),
        "provider_version": PROVIDER_VERSION,
        "model_version": MODEL_VERSION,
        "policy_version": POLICY_VERSION,
        "route_counts": {key: _weighted(route_counts.get(key, 0.0)) for key in sorted(ROUTES)},
        "decision_counts": {
            key: _weighted(decision_counts.get(key, 0.0))
            for key in ("approve", "reject", "defer")
        },
        "routes": {key: _weighted(route_counts.get(key, 0.0)) for key in sorted(ROUTES)},
        "decisions": {
            key: _weighted(decision_counts.get(key, 0.0))
            for key in ("approve", "reject", "defer")
        },
        "route": {key: _weighted(route_counts.get(key, 0.0)) for key in sorted(ROUTES)},
        "decision": {
            key: _weighted(decision_counts.get(key, 0.0))
            for key in ("approve", "reject", "defer")
        },
        "per_risk_class": confusion_report,
        "per_risk_confusion": confusion_report,
        "risk_confusion": confusion_report,
        "routing_rates": routing_rates,
        "thresholds": [f"{threshold:.2f}" for threshold in ROUTING_THRESHOLDS],
        "failures": failures,
        "failure_codes": sorted(set(failure_codes)),
        "gate_requirements": {
            "minimum_weighted_fixtures": MIN_WEIGHTED_FIXTURES,
            "minimum_precision": MIN_PRECISION,
            "minimum_recall": MIN_RECALL,
            "maximum_unsafe_high_risk_count": 0,
        },
        "versions": {
            "provider": PROVIDER_VERSION,
            "model": MODEL_VERSION,
            "policy": POLICY_VERSION,
        },
    }
    report["gate_passed"] = not failures
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rows = load_fixtures(args.fixtures)
        report = evaluate(rows)
    except FixtureError as exc:
        report = evaluate([])
        report["failures"] = [{"code": "fixture_load_failed", "reason": str(exc)}] + report["failures"]
        report["failure_codes"] = sorted({"fixture_load_failed", *report["failure_codes"]})
        report["gate_passed"] = False

    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
