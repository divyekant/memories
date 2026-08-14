"""Bounded reconciliation and text-free metrics for promotion workflow."""

from dataclasses import replace

from project_promotion import PromotionConfig, PromotionMode, PromotionStatus
from promotion_service import PromotionReviewer, PromotionService
from tests.test_promotion_service import (
    FakeEngine,
    FakeProvider,
    _approved_state,
    _candidate,
    _state,
    key_store,
)


def _service(engine, key_store, mode=PromotionMode.AUTO):
    return PromotionService(
        engine,
        key_store,
        config=PromotionConfig(host_mode=mode, relevance_threshold=0.5),
        reviewer=PromotionReviewer(provider=FakeProvider()),
    )


def test_reconcile_repairs_existing_target_without_creating_duplicate(key_store):
    candidate = _candidate(state=_approved_state())
    target = {
        "id": 9,
        "text": candidate["text"],
        "source": "project/fplguru/knowledge",
        "author": "alice",
        "contributors": ["alice"],
        "source_memory_ids": [1],
    }
    engine = FakeEngine([candidate, target])

    result = _service(engine, key_store, mode=PromotionMode.OFF).reconcile(
        max_candidates=25,
        budget_seconds=2,
    )

    assert result["repaired"] == 1
    assert "target_add" not in engine.events
    assert engine.get_memory(1)["archived"] is True


def test_reconcile_promotes_current_shadow_approval_only_when_auto(key_store):
    candidate = _candidate(
        state=_approved_state(
            mode=PromotionMode.SHADOW,
            status=PromotionStatus.SHADOW_APPROVED,
        )
    )
    engine = FakeEngine([candidate])
    service = _service(engine, key_store, mode=PromotionMode.AUTO)
    service.project_modes["fplguru"] = PromotionMode.AUTO

    result = service.reconcile(max_candidates=25, budget_seconds=2)

    assert result["promoted"] == 1
    assert engine.get_memory(1)["archived"] is True


def test_reconcile_retries_approved_candidate_after_interrupted_auto_promotion(key_store):
    candidate = _candidate(state=_approved_state())
    engine = FakeEngine([candidate])

    result = _service(engine, key_store).reconcile(
        max_candidates=25,
        budget_seconds=2,
    )

    assert result["promoted"] == 1
    assert engine.get_memory(1)["archived"] is True


def test_reconcile_marks_lost_evidence_unreviewable_and_respects_batch(key_store):
    memories = [_candidate(i, _state()) for i in range(1, 4)]
    engine = FakeEngine(memories)

    result = _service(engine, key_store).reconcile(max_candidates=2, budget_seconds=2)

    assert result["processed"] == 2
    assert result["unreviewable"] == 2
    assert engine.get_memory(3)["promotion"]["status"] == "candidate"


def test_metrics_snapshot_contains_no_private_payload_fields(key_store):
    candidate = _candidate(
        state=_state(status=PromotionStatus.UNREVIEWABLE),
        text="private secret transcript",
    )
    engine = FakeEngine([candidate])

    snapshot = _service(engine, key_store).metrics_snapshot()
    rendered = str(snapshot)

    assert snapshot["status_counts"]["unreviewable"] == 1
    assert "unreviewable_rate_alert" in snapshot["alerts"]
    assert "private secret transcript" not in rendered
    assert "evidence_fingerprint" not in rendered
    assert "reason" not in rendered
