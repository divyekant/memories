"""Contract tests for the Phase 2 promotion policy foundation."""

from dataclasses import FrozenInstanceError
from copy import deepcopy

import pytest

from project_promotion import (
    PromotionConfig,
    PromotionContext,
    PromotionMode,
    PromotionProposal,
    PromotionReview,
    PromotionState,
    PromotionStatus,
    ReviewDecision,
    canonical_project_text,
    load_promotion_config,
    parse_proposal,
    project_text_digest,
    promotion_state_from_memory,
    resolve_effective_mode,
    select_review_route,
)


def test_effective_mode_uses_more_restrictive_value():
    assert resolve_effective_mode(PromotionMode.SHADOW, PromotionMode.AUTO) is PromotionMode.SHADOW
    assert resolve_effective_mode(PromotionMode.AUTO, PromotionMode.OFF) is PromotionMode.OFF
    assert resolve_effective_mode(PromotionMode.AUTO, PromotionMode.SHADOW) is PromotionMode.SHADOW


def test_digest_normalizes_only_unicode_line_endings_and_whitespace():
    left = project_text_digest("fplguru", "knowledge", " Caf\u00e9  rule\r\n")
    right = project_text_digest("fplguru", "knowledge", "Cafe\u0301 rule\n")

    assert left == right
    assert left != project_text_digest("fplguru", "knowledge", "caf\u00e9 rule\n")
    assert canonical_project_text("  First   line\r\nSecond\tline  ") == "First line\nSecond line"


def test_digest_keeps_project_kind_punctuation_and_order_identity():
    base = project_text_digest("fplguru", "knowledge", "First claim.\nSecond claim!")

    assert base != project_text_digest("other-project", "knowledge", "First claim.\nSecond claim!")
    assert base != project_text_digest("fplguru", "decisions", "First claim.\nSecond claim!")
    assert base != project_text_digest("fplguru", "knowledge", "First claim?\nSecond claim!")
    assert base != project_text_digest("fplguru", "knowledge", "Second claim!\nFirst claim.")


def test_malformed_proposal_fails_private():
    assert parse_proposal({"visibility": "project", "confidence": "high"}) is None
    for field in ("project_relevance", "confidence"):
        malformed = {
            "project_relevance": 0.9,
            "visibility": "project",
            "assertion_status": "confirmed",
            "project_kind": "knowledge",
            "confidence": 0.9,
            "reason": "durable fact",
            "classifier_version": "classifier-v1",
        }
        malformed[field] = "0.9"
        assert parse_proposal(malformed) is None
    assert parse_proposal(
        {
            "project_relevance": 0.9,
            "visibility": "project",
            "assertion_status": "confirmed",
            "project_kind": "knowledge",
            "confidence": 0.9,
            "reason": "durable fact",
            "classifier_version": "classifier-v1",
        }
    ) == PromotionProposal(
        project_relevance=0.9,
        visibility="project",
        assertion_status="confirmed",
        project_kind="knowledge",
        confidence=0.9,
        reason="durable fact",
        classifier_version="classifier-v1",
    )


def test_config_defaults_are_off_and_unset_relevance_is_safe(monkeypatch):
    for key in tuple(key for key in __import__("os").environ if key.startswith("PROJECT_PROMOTION_")):
        monkeypatch.delenv(key, raising=False)

    config = load_promotion_config()

    assert config == PromotionConfig()
    assert config.host_mode is PromotionMode.OFF
    assert config.relevance_threshold is None
    assert config.reconcile_batch == 25
    assert config.reconcile_budget_seconds == 20


def test_config_parsing_is_strict_and_fails_closed(monkeypatch):
    monkeypatch.setenv("PROJECT_PROMOTION_MODE", "unknown")
    with pytest.raises(ValueError):
        load_promotion_config()

    monkeypatch.setenv("PROJECT_PROMOTION_MODE", "shadow")
    monkeypatch.setenv("PROJECT_PROMOTION_RELEVANCE_THRESHOLD", "not-a-number")
    with pytest.raises(ValueError):
        load_promotion_config()

    monkeypatch.delenv("PROJECT_PROMOTION_RELEVANCE_THRESHOLD")
    with pytest.raises(ValueError):
        load_promotion_config()

    monkeypatch.setenv("PROJECT_PROMOTION_MODE", "auto")
    with pytest.raises(ValueError):
        load_promotion_config()


@pytest.mark.parametrize("mode", [PromotionMode.SHADOW, PromotionMode.AUTO])
def test_active_config_requires_measured_relevance_threshold(mode):
    with pytest.raises(ValueError, match="relevance_threshold"):
        PromotionConfig(host_mode=mode)

    assert PromotionConfig(host_mode=PromotionMode.OFF).relevance_threshold is None


def test_context_keeps_classifier_and_reviewer_identities_independent():
    context = PromotionContext(
        project_id="fplguru",
        principal_id="alice",
        declared_mode=PromotionMode.SHADOW,
        effective_mode=PromotionMode.SHADOW,
        declaration_fingerprint="decl-sha256",
        classifier_version="classifier-v1",
        classifier_provider="anthropic",
        classifier_model="claude-haiku",
        reviewer_version="reviewer-v2",
        reviewer_provider="openai",
        reviewer_model="gpt-4.1-nano",
    )

    assert context.classifier_provider == "anthropic"
    assert context.classifier_model == "claude-haiku"
    assert context.reviewer_provider == "openai"
    assert context.reviewer_model == "gpt-4.1-nano"


def test_state_round_trip_is_typed_and_contains_policy_versions():
    proposal = PromotionProposal(
        project_relevance=0.95,
        visibility="project",
        assertion_status="confirmed",
        project_kind="knowledge",
        confidence=0.91,
        reason="durable project invariant",
        classifier_version="classifier-v1",
    )
    review = PromotionReview(
        decision=ReviewDecision.APPROVE,
        confidence=0.93,
        reason="entailed and shareable",
        shared_text="Use uv for dependencies.",
        reviewer_version="reviewer-v2",
        reviewed_at="2026-08-14T12:00:00+00:00",
    )
    state = PromotionState(
        status=PromotionStatus.CANDIDATE,
        owner="alice",
        project_id="fplguru",
        declaration_fingerprint="decl-sha256",
        classifier_provider="anthropic",
        classifier_model="claude-haiku",
        reviewer_provider="openai",
        reviewer_model="gpt-4.1-nano",
        capture_mode=PromotionMode.SHADOW,
        route="ordinary",
        proposal=proposal,
        review=review,
        evidence_fingerprint="evidence-sha256",
        captured_at="2026-08-14T11:00:00+00:00",
        attempt_count=1,
        target_memory_id=None,
        rejected_until=None,
    )

    metadata = state.as_metadata()
    restored = promotion_state_from_memory({"id": 42, **metadata})

    assert restored == state
    assert metadata["promotion"]["proposal"]["classifier_version"] == "classifier-v1"
    assert metadata["promotion"]["review"]["reviewer_version"] == "reviewer-v2"
    assert metadata["promotion"]["review"]["reviewed_at"] == "2026-08-14T12:00:00+00:00"
    assert metadata["promotion"]["declaration_fingerprint"] == "decl-sha256"
    assert metadata["promotion"]["classifier_provider"] == "anthropic"
    assert metadata["promotion"]["classifier_model"] == "claude-haiku"
    assert metadata["promotion"]["reviewer_provider"] == "openai"
    assert metadata["promotion"]["reviewer_model"] == "gpt-4.1-nano"
    assert "transcript" not in metadata["promotion"]
    with pytest.raises(FrozenInstanceError):
        state.status = PromotionStatus.PRIVATE


def test_state_parser_rejects_missing_or_spoofed_policy_identity():
    proposal = PromotionProposal(
        project_relevance=0.95,
        visibility="project",
        assertion_status="confirmed",
        project_kind="knowledge",
        confidence=0.91,
        reason="durable project invariant",
        classifier_version="classifier-v1",
    )
    state = PromotionState(
        status=PromotionStatus.CANDIDATE,
        owner="alice",
        project_id="fplguru",
        declaration_fingerprint="decl-sha256",
        classifier_provider="anthropic",
        classifier_model="claude-haiku",
        reviewer_provider="openai",
        reviewer_model="gpt-4.1-nano",
        capture_mode=PromotionMode.SHADOW,
        route="ordinary",
        proposal=proposal,
        review=None,
        evidence_fingerprint="evidence-sha256",
        captured_at="2026-08-14T11:00:00+00:00",
    )
    metadata = state.as_metadata()

    missing = deepcopy(metadata)
    del missing["promotion"]["classifier_provider"]
    assert promotion_state_from_memory(missing) is None

    spoofed = deepcopy(metadata)
    spoofed["promotion"]["reviewer_provider"] = "mallory-provider"
    assert promotion_state_from_memory(spoofed) is None

    unknown = deepcopy(metadata)
    unknown["promotion"]["classifier_prompt"] = "do not persist me"
    assert promotion_state_from_memory(unknown) is None


def test_review_route_uses_ordinary_threshold_then_fixed_audit_floor():
    proposal = PromotionProposal(
        project_relevance=0.4,
        visibility="project",
        assertion_status="confirmed",
        project_kind="knowledge",
        confidence=0.9,
        reason="durable fact",
        classifier_version="classifier-v1",
    )
    config = PromotionConfig(relevance_threshold=0.8, audit_floor=2)

    assert select_review_route(proposal, recent_audit_count=0, config=config) == "audit"
    assert select_review_route(proposal, recent_audit_count=2, config=config) is None
    assert select_review_route(
        PromotionProposal(**{**proposal.__dict__, "project_relevance": 0.8}),
        recent_audit_count=2,
        config=config,
    ) == "ordinary"


@pytest.mark.parametrize(
    ("visibility", "assertion_status"),
    [
        ("private", "confirmed"),
        ("uncertain", "confirmed"),
        ("project", "tentative"),
        ("project", "disputed"),
    ],
)
def test_review_route_rejects_private_uncertain_and_nonfinal_proposals(
    visibility, assertion_status
):
    proposal = PromotionProposal(
        project_relevance=0.95,
        visibility=visibility,
        assertion_status=assertion_status,
        project_kind="knowledge",
        confidence=0.9,
        reason="durable fact",
        classifier_version="classifier-v1",
    )

    assert (
        select_review_route(
            proposal,
            recent_audit_count=0,
            config=PromotionConfig(relevance_threshold=0.8),
        )
        is None
    )


def test_persisted_review_requires_reviewer_version_and_timestamp():
    proposal = PromotionProposal(
        project_relevance=0.95,
        visibility="project",
        assertion_status="confirmed",
        project_kind="knowledge",
        confidence=0.91,
        reason="durable project invariant",
        classifier_version="classifier-v1",
    )
    with pytest.raises(ValueError):
        PromotionState(
            status=PromotionStatus.CANDIDATE,
            owner="alice",
            project_id="fplguru",
            declaration_fingerprint="decl-sha256",
            classifier_provider="anthropic",
            classifier_model="claude-haiku",
            reviewer_provider="openai",
            reviewer_model="gpt-4.1-nano",
            capture_mode=PromotionMode.SHADOW,
            route="ordinary",
            proposal=proposal,
            review=PromotionReview(
                decision=ReviewDecision.DEFER,
                confidence=0.5,
                reason="insufficient evidence",
            ),
            evidence_fingerprint="evidence-sha256",
            captured_at="2026-08-14T11:00:00+00:00",
        )
    state = PromotionState(
        status=PromotionStatus.CANDIDATE,
        owner="alice",
        project_id="fplguru",
        declaration_fingerprint="decl-sha256",
        classifier_provider="anthropic",
        classifier_model="claude-haiku",
        reviewer_provider="openai",
        reviewer_model="gpt-4.1-nano",
        capture_mode=PromotionMode.SHADOW,
        route="ordinary",
        proposal=proposal,
        review=PromotionReview(
            decision=ReviewDecision.DEFER,
            confidence=0.5,
            reason="insufficient evidence",
            reviewer_version="reviewer-v2",
            reviewed_at="2026-08-14T12:00:00+00:00",
        ),
        evidence_fingerprint="evidence-sha256",
        captured_at="2026-08-14T11:00:00+00:00",
    )
    metadata = state.as_metadata()

    del metadata["promotion"]["review"]["reviewer_version"]
    assert promotion_state_from_memory(metadata) is None
    metadata = state.as_metadata()
    metadata["promotion"]["review"]["reviewer_version"] = ""
    assert promotion_state_from_memory(metadata) is None
    metadata = state.as_metadata()
    del metadata["promotion"]["review"]["reviewed_at"]
    assert promotion_state_from_memory(metadata) is None
    metadata = state.as_metadata()
    metadata["promotion"]["review"]["reviewed_at"] = ""
    assert promotion_state_from_memory(metadata) is None


def test_project_promotion_metadata_is_reserved():
    from project_memory import RESERVED_METADATA_FIELDS

    assert "promotion" in RESERVED_METADATA_FIELDS
