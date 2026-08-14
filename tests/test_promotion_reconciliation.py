"""Bounded reconciliation and text-free metrics for promotion workflow."""

from dataclasses import replace

from project_promotion import PromotionConfig, PromotionMode, PromotionStatus
from promotion_service import PromotionReviewer, PromotionService
from tests.test_promotion_service import (
    CollectingAudit,
    FakeEngine,
    FakeProvider,
    _approved_state,
    _candidate,
    _state,
    key_store,
)


def _service(engine, key_store, mode=PromotionMode.AUTO, **kwargs):
    return PromotionService(
        engine,
        key_store,
        config=PromotionConfig(host_mode=mode, relevance_threshold=0.5),
        reviewer=PromotionReviewer(provider=FakeProvider()),
        project_modes={"fplguru": mode},
        declaration_fingerprints={"fplguru": "d" * 64},
        **kwargs,
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


def test_reconcile_leaves_current_shadow_approval_for_staged_manual_release(key_store):
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

    assert result["promoted"] == 0
    assert engine.get_memory(1).get("archived") is not True


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
    audit = CollectingAudit()

    result = _service(engine, key_store, audit_log=audit).reconcile(
        max_candidates=2,
        budget_seconds=2,
    )

    assert result["processed"] == 2
    assert result["unreviewable"] == 2
    assert engine.get_memory(3)["promotion"]["status"] == "candidate"
    assert [entry["action"] for entry in audit.entries] == [
        "promotion.unreviewable",
        "promotion.unreviewable",
    ]


def test_reconcile_filters_terminal_history_before_applying_batch_limit(key_store):
    terminal = [
        _candidate(i, _state(status=PromotionStatus.PRIVATE)) for i in range(1, 31)
    ]
    actionable = _candidate(100, _approved_state())
    engine = FakeEngine([*terminal, actionable])
    service = _service(engine, key_store)
    service.project_modes["fplguru"] = PromotionMode.AUTO
    service.declaration_fingerprints["fplguru"] = "d" * 64

    result = service.reconcile(max_candidates=1, budget_seconds=2)

    assert result["processed"] == 1
    assert result["promoted"] == 1
    assert engine.get_memory(100)["archived"] is True


def test_reconcile_does_not_bulk_publish_shadow_backlog(key_store):
    candidate = _candidate(
        state=_approved_state(
            mode=PromotionMode.SHADOW,
            status=PromotionStatus.SHADOW_APPROVED,
        )
    )
    engine = FakeEngine([candidate])
    service = _service(engine, key_store, mode=PromotionMode.AUTO)
    service.project_modes["fplguru"] = PromotionMode.AUTO
    service.declaration_fingerprints["fplguru"] = "d" * 64

    result = service.reconcile(max_candidates=25, budget_seconds=2)

    assert result["promoted"] == 0
    assert engine.get_memory(1).get("archived") is not True
    assert engine.get_memory(1)["promotion"]["status"] == "shadow_approved"


def test_reconcile_remediates_rejected_link_instead_of_retrying_forever(key_store):
    candidate = _candidate(state=_state(status=PromotionStatus.REJECTED))
    target = {
        "id": 9,
        "text": candidate["text"],
        "source": "project/fplguru/knowledge",
        "author": "alice",
        "contributors": ["alice"],
        "source_memory_ids": [1],
    }
    engine = FakeEngine([candidate, target])
    service = _service(engine, key_store)

    result = service.reconcile(max_candidates=25, budget_seconds=2)

    assert result["errors"] == 0
    assert result["remediated"] == 1
    assert engine.get_memory(9)["archived"] is True


def test_reconcile_caps_deterministic_failures_and_stops_retrying(key_store):
    candidate = _candidate(state=_approved_state())
    engine = FakeEngine([candidate])

    def fail_add(*args, **kwargs):
        raise RuntimeError("deterministic target failure")

    engine.add_memories = fail_add
    service = _service(engine, key_store)
    service.project_modes["fplguru"] = PromotionMode.AUTO
    service.declaration_fingerprints["fplguru"] = "d" * 64

    for _ in range(5):
        service.reconcile(max_candidates=1, budget_seconds=2)

    assert engine.get_memory(1)["promotion"]["status"] == "unreviewable"
    final = service.reconcile(max_candidates=1, budget_seconds=2)
    assert final["processed"] == 0
    assert final["errors"] == 0


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


def test_metrics_measure_semantic_near_duplicates_without_returning_text(key_store):
    candidate = _candidate(state=_approved_state(), text="Use an idempotency tuple for writes.")
    target = {
        "id": 9,
        "text": "Writes use an idempotency tuple.",
        "source": "project/fplguru/knowledge",
        "author": "alice",
        "source_memory_ids": [8],
    }

    class SimilarEngine(FakeEngine):
        def search(self, query, **kwargs):
            assert kwargs["source_exact"] == "project/fplguru/knowledge"
            assert kwargs["reinforce_results"] is False
            return [{**target, "similarity": 0.93}]

    snapshot = _service(SimilarEngine([candidate, target]), key_store).metrics_snapshot()

    assert snapshot["semantic_near_duplicate_count"] == 1
    assert snapshot["semantic_near_duplicate_rate"] == 1.0
    assert "idempotency" not in str(snapshot).lower()
