"""Typed policy and durable state primitives for project-memory promotion.

This module deliberately contains policy data only.  The promotion service in
later phases owns authorization, reviewer calls, and memory mutations.  The
objects here are immutable so that a caller cannot change a reviewed decision
or its policy identity after it has crossed a persistence boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from numbers import Real
import os
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Literal


class PromotionMode(str, Enum):
    """The rollout modes supported by the host and project declarations."""

    OFF = "off"
    SHADOW = "shadow"
    AUTO = "auto"


class PromotionStatus(str, Enum):
    """Durable status values for a private promotion candidate."""

    PRIVATE = "private"
    CANDIDATE = "candidate"
    SHADOW_APPROVED = "shadow_approved"
    DEFERRED = "deferred"
    FAILED = "failed"
    UNREVIEWABLE = "unreviewable"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class ReviewDecision(str, Enum):
    """The only decisions a promotion reviewer may return."""

    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"


_PROJECT_KINDS = frozenset({"decisions", "knowledge", "state", "operations"})
_PROPOSAL_VISIBILITIES = frozenset({"project", "private", "uncertain"})
_ASSERTION_STATUSES = frozenset({"confirmed", "tentative", "disputed"})
_REVIEW_ROUTES = frozenset({"ordinary", "audit"})
_PROVIDER_NAMES = frozenset(
    {"anthropic", "openai", "chatgpt-subscription", "ollama", "omlx"}
)


def _coerce_enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValueError(f"invalid {field_name}")


def _finite_number(value: Any, field_name: str, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    # bool is an int subclass but is never a valid semantic score.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"invalid {field_name}")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"invalid {field_name}")
    return result


def _non_empty_string(value: Any, field_name: str, *, strip: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {field_name}")
    result = value.strip() if strip else value
    if not result:
        raise ValueError(f"invalid {field_name}")
    return result


def _provider_identity(value: Any, field_name: str) -> str:
    """Validate and normalize a persisted provider identity."""

    provider = _non_empty_string(value, field_name, strip=True).lower()
    if provider not in _PROVIDER_NAMES:
        raise ValueError(f"invalid {field_name}")
    return provider


def _model_identity(value: Any, field_name: str) -> str:
    """Validate a persisted model identity without constraining model names."""

    return _non_empty_string(value, field_name, strip=True)


@dataclass(frozen=True)
class PromotionConfig:
    """Operator-owned promotion settings.

    ``relevance_threshold`` intentionally has no default.  A missing value is
    safe for an off host, while route selection returns no route until an
    operator supplies a measured threshold.
    """

    host_mode: PromotionMode = PromotionMode.OFF
    relevance_threshold: float | None = None
    near_duplicate_threshold: float = 0.88
    audit_floor: int = 10
    audit_period_days: int = 7
    reconcile_batch: int = 25
    reconcile_budget_seconds: int = 20
    rejected_retention_days: int = 90
    unreviewable_rate_count: int = 5
    unreviewable_rate_window_hours: int = 1
    unreviewable_backlog_age_hours: int = 168
    review_provider: str = ""
    review_model: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "host_mode", _coerce_enum(self.host_mode, PromotionMode, "host_mode")
        )
        if self.relevance_threshold is not None:
            object.__setattr__(
                self,
                "relevance_threshold",
                _finite_number(self.relevance_threshold, "relevance_threshold"),
            )
        object.__setattr__(
            self,
            "near_duplicate_threshold",
            _finite_number(self.near_duplicate_threshold, "near_duplicate_threshold"),
        )
        for field_name, minimum in (
            ("audit_floor", 0),
            ("audit_period_days", 1),
            ("reconcile_batch", 1),
            ("reconcile_budget_seconds", 1),
            ("rejected_retention_days", 0),
            ("unreviewable_rate_count", 1),
            ("unreviewable_rate_window_hours", 1),
            ("unreviewable_backlog_age_hours", 1),
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"invalid {field_name}")
        for field_name in ("review_provider", "review_model"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"invalid {field_name}")
            object.__setattr__(self, field_name, value.strip())
        if self.review_provider and self.review_provider.lower() not in _PROVIDER_NAMES:
            raise ValueError("invalid review_provider")
        if self.review_provider:
            object.__setattr__(self, "review_provider", self.review_provider.lower())


@dataclass(frozen=True)
class PromotionContext:
    """The authenticated project declaration and policy identity for a run."""

    project_id: str
    principal_id: str
    declared_mode: PromotionMode
    effective_mode: PromotionMode
    declaration_fingerprint: str
    classifier_version: str
    classifier_provider: str
    classifier_model: str
    reviewer_version: str
    reviewer_provider: str
    reviewer_model: str

    def __post_init__(self) -> None:
        for field_name in (
            "project_id",
            "principal_id",
            "declaration_fingerprint",
            "classifier_version",
            "reviewer_version",
        ):
            _non_empty_string(getattr(self, field_name), field_name)
        object.__setattr__(
            self,
            "classifier_provider",
            _provider_identity(self.classifier_provider, "classifier_provider"),
        )
        object.__setattr__(
            self,
            "classifier_model",
            _model_identity(self.classifier_model, "classifier_model"),
        )
        object.__setattr__(
            self,
            "reviewer_provider",
            _provider_identity(self.reviewer_provider, "reviewer_provider"),
        )
        object.__setattr__(
            self,
            "reviewer_model",
            _model_identity(self.reviewer_model, "reviewer_model"),
        )
        object.__setattr__(
            self,
            "declared_mode",
            _coerce_enum(self.declared_mode, PromotionMode, "declared_mode"),
        )
        object.__setattr__(
            self,
            "effective_mode",
            _coerce_enum(self.effective_mode, PromotionMode, "effective_mode"),
        )


@dataclass(frozen=True)
class PromotionProposal:
    """Strict, additive classifier output used to route a private fact."""

    project_relevance: float
    visibility: str
    assertion_status: str
    project_kind: str
    confidence: float
    reason: str
    classifier_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_relevance",
            _finite_number(self.project_relevance, "project_relevance"),
        )
        object.__setattr__(
            self,
            "confidence",
            _finite_number(self.confidence, "confidence"),
        )
        if self.visibility not in _PROPOSAL_VISIBILITIES:
            raise ValueError("invalid visibility")
        if self.assertion_status not in _ASSERTION_STATUSES:
            raise ValueError("invalid assertion_status")
        if self.project_kind not in _PROJECT_KINDS:
            raise ValueError("invalid project_kind")
        object.__setattr__(
            self, "reason", _non_empty_string(self.reason, "reason", strip=True)
        )
        object.__setattr__(
            self,
            "classifier_version",
            _non_empty_string(self.classifier_version, "classifier_version", strip=True),
        )


@dataclass(frozen=True)
class PromotionReview:
    """Narrow reviewer output and its independently versioned identity."""

    decision: ReviewDecision
    confidence: float
    reason: str
    shared_text: str | None = None
    reviewer_version: str = ""
    reviewed_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision",
            _coerce_enum(self.decision, ReviewDecision, "decision"),
        )
        object.__setattr__(
            self,
            "confidence",
            _finite_number(self.confidence, "confidence"),
        )
        object.__setattr__(
            self, "reason", _non_empty_string(self.reason, "reason", strip=True)
        )
        if self.shared_text is not None and not isinstance(self.shared_text, str):
            raise ValueError("invalid shared_text")
        for field_name in ("reviewer_version", "reviewed_at"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"invalid {field_name}")
            object.__setattr__(self, field_name, value.strip())


@dataclass(frozen=True)
class PromotionState:
    """Server-owned workflow state persisted under a private memory's metadata."""

    status: PromotionStatus
    owner: str
    project_id: str
    declaration_fingerprint: str
    classifier_provider: str
    classifier_model: str
    reviewer_provider: str
    reviewer_model: str
    capture_mode: PromotionMode
    route: str | None
    proposal: PromotionProposal | None
    review: PromotionReview | None
    evidence_fingerprint: str
    captured_at: str
    attempt_count: int = 0
    target_memory_id: int | None = None
    rejected_until: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            _coerce_enum(self.status, PromotionStatus, "status"),
        )
        object.__setattr__(
            self,
            "capture_mode",
            _coerce_enum(self.capture_mode, PromotionMode, "capture_mode"),
        )
        for field_name in (
            "owner",
            "project_id",
            "declaration_fingerprint",
            "evidence_fingerprint",
            "captured_at",
        ):
            _non_empty_string(getattr(self, field_name), field_name)
        object.__setattr__(
            self,
            "classifier_provider",
            _provider_identity(self.classifier_provider, "classifier_provider"),
        )
        object.__setattr__(
            self,
            "classifier_model",
            _model_identity(self.classifier_model, "classifier_model"),
        )
        object.__setattr__(
            self,
            "reviewer_provider",
            _provider_identity(self.reviewer_provider, "reviewer_provider"),
        )
        object.__setattr__(
            self,
            "reviewer_model",
            _model_identity(self.reviewer_model, "reviewer_model"),
        )
        if self.route is not None and self.route not in _REVIEW_ROUTES:
            raise ValueError("invalid route")
        if isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int) or self.attempt_count < 0:
            raise ValueError("invalid attempt_count")
        value = self.target_memory_id
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError("invalid target_memory_id")
        if self.rejected_until is not None and not isinstance(self.rejected_until, str):
            raise ValueError("invalid rejected_until")
        if self.proposal is not None and not isinstance(self.proposal, PromotionProposal):
            raise ValueError("invalid proposal")
        if self.review is not None and not isinstance(self.review, PromotionReview):
            raise ValueError("invalid review")
        if self.review is not None and (
            not self.review.reviewer_version or not self.review.reviewed_at
        ):
            raise ValueError("persisted reviews require reviewer_version and reviewed_at")

    @property
    def reviewed_at(self) -> str:
        """The review timestamp carried by the persisted review."""

        return self.review.reviewed_at if self.review else ""

    def as_metadata(self) -> dict[str, Any]:
        """Return the complete server-owned metadata envelope.

        This whitelist is intentional: transcript snippets and caller-supplied
        arbitrary metadata must never become promotion state.
        """

        payload: dict[str, Any] = {
            "status": self.status.value,
            "owner": self.owner,
            "project_id": self.project_id,
            "declaration_fingerprint": self.declaration_fingerprint,
            "classifier_provider": self.classifier_provider,
            "classifier_model": self.classifier_model,
            "reviewer_provider": self.reviewer_provider,
            "reviewer_model": self.reviewer_model,
            "capture_mode": self.capture_mode.value,
            "route": self.route,
            "proposal": _proposal_as_metadata(self.proposal),
            "review": _review_as_metadata(self.review),
            "evidence_fingerprint": self.evidence_fingerprint,
            "captured_at": self.captured_at,
            "attempt_count": self.attempt_count,
            "target_memory_id": self.target_memory_id,
            "rejected_until": self.rejected_until,
        }
        return {"promotion": payload}


def _proposal_as_metadata(proposal: PromotionProposal | None) -> dict[str, Any] | None:
    if proposal is None:
        return None
    return {
        "project_relevance": proposal.project_relevance,
        "visibility": proposal.visibility,
        "assertion_status": proposal.assertion_status,
        "project_kind": proposal.project_kind,
        "confidence": proposal.confidence,
        "reason": proposal.reason,
        "classifier_version": proposal.classifier_version,
    }


def _review_as_metadata(review: PromotionReview | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "decision": review.decision.value,
        "confidence": review.confidence,
        "reason": review.reason,
        "shared_text": review.shared_text,
        "reviewer_version": review.reviewer_version,
        "reviewed_at": review.reviewed_at,
    }


def parse_proposal(value: Mapping[str, Any]) -> PromotionProposal | None:
    """Parse classifier output, returning ``None`` for any malformed value.

    ``category`` and ``text`` are accepted as compatibility fields from the
    existing extraction schema, but are intentionally not persisted in the
    promotion envelope.  Every other unknown field is rejected.
    """

    if not isinstance(value, Mapping):
        return None
    required = {
        "project_relevance",
        "visibility",
        "assertion_status",
        "project_kind",
        "confidence",
        "reason",
        "classifier_version",
    }
    allowed = required | {"category", "text"}
    if set(value) - allowed or not required.issubset(value):
        return None
    try:
        return PromotionProposal(
            project_relevance=value["project_relevance"],
            visibility=value["visibility"],
            assertion_status=value["assertion_status"],
            project_kind=value["project_kind"],
            confidence=value["confidence"],
            reason=value["reason"],
            classifier_version=value["classifier_version"],
        )
    except (TypeError, ValueError):
        return None


def _parse_review(value: Any) -> PromotionReview | None:
    if not isinstance(value, Mapping):
        return None
    required = {"decision", "confidence", "reason"}
    allowed = required | {"shared_text", "reviewer_version", "reviewed_at"}
    if set(value) - allowed or not required.issubset(value):
        return None
    try:
        review = PromotionReview(
            decision=value["decision"],
            confidence=value["confidence"],
            reason=value["reason"],
            shared_text=value.get("shared_text"),
            reviewer_version=value.get("reviewer_version", ""),
            reviewed_at=value.get("reviewed_at", ""),
        )
    except (TypeError, ValueError):
        return None
    if not review.reviewer_version or not review.reviewed_at:
        return None
    return review


def promotion_state_from_memory(memory: Mapping[str, Any]) -> PromotionState | None:
    """Decode only the typed ``promotion`` envelope from a memory mapping."""

    if not isinstance(memory, Mapping):
        return None
    value = memory.get("promotion")
    if not isinstance(value, Mapping):
        return None
    required = {
        "status",
        "owner",
        "project_id",
        "declaration_fingerprint",
        "classifier_provider",
        "classifier_model",
        "reviewer_provider",
        "reviewer_model",
        "capture_mode",
        "route",
        "proposal",
        "review",
        "evidence_fingerprint",
        "captured_at",
        "attempt_count",
        "target_memory_id",
        "rejected_until",
    }
    if set(value) - required or not required.issubset(value):
        return None
    proposal_value = value["proposal"]
    proposal = None if proposal_value is None else parse_proposal(proposal_value)
    if proposal_value is not None and proposal is None:
        return None
    review_value = value["review"]
    review = None if review_value is None else _parse_review(review_value)
    if review_value is not None and review is None:
        return None
    try:
        return PromotionState(
            status=value["status"],
            owner=value["owner"],
            project_id=value["project_id"],
            declaration_fingerprint=value["declaration_fingerprint"],
            classifier_provider=value["classifier_provider"],
            classifier_model=value["classifier_model"],
            reviewer_provider=value["reviewer_provider"],
            reviewer_model=value["reviewer_model"],
            capture_mode=value["capture_mode"],
            route=value["route"],
            proposal=proposal,
            review=review,
            evidence_fingerprint=value["evidence_fingerprint"],
            captured_at=value["captured_at"],
            attempt_count=value["attempt_count"],
            target_memory_id=value["target_memory_id"],
            rejected_until=value["rejected_until"],
        )
    except (TypeError, ValueError):
        return None


def canonical_project_text(text: str) -> str:
    """Canonicalize only Unicode, line-ending, and whitespace differences."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line.strip()) for line in normalized.split("\n")]
    return "\n".join(lines).strip()


def project_text_digest(project_id: str, kind: str, text: str) -> str:
    """Return a SHA-256 digest over project identity and canonical text."""

    if not isinstance(project_id, str) or not isinstance(kind, str):
        raise TypeError("project_id and kind must be strings")
    canonical = canonical_project_text(text)
    # JSON framing keeps identity components unambiguous while remaining
    # deterministic and Unicode-preserving.
    payload = json.dumps(
        [project_id, kind, canonical], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resolve_effective_mode(host: PromotionMode, declared: PromotionMode) -> PromotionMode:
    """Return the more restrictive mode, with invalid input failing closed."""

    try:
        host_mode = _coerce_enum(host, PromotionMode, "host_mode")
        declared_mode = _coerce_enum(declared, PromotionMode, "declared_mode")
    except ValueError:
        return PromotionMode.OFF
    order = {
        PromotionMode.OFF: 0,
        PromotionMode.SHADOW: 1,
        PromotionMode.AUTO: 2,
    }
    return host_mode if order[host_mode] <= order[declared_mode] else declared_mode


def select_review_route(
    proposal: PromotionProposal,
    *,
    recent_audit_count: int,
    config: PromotionConfig,
) -> Literal["ordinary", "audit"] | None:
    """Select ordinary or fixed-floor audit review for an eligible proposal."""

    if not isinstance(proposal, PromotionProposal) or not isinstance(config, PromotionConfig):
        return None
    threshold = config.relevance_threshold
    if threshold is None:
        return None
    # Explicit private/uncertain visibility and non-final assertions are never
    # allowed to route merely because they mention a project.
    if proposal.visibility != "project" or proposal.assertion_status != "confirmed":
        return None
    if proposal.project_relevance >= threshold:
        return "ordinary"
    if not isinstance(recent_audit_count, int) or isinstance(recent_audit_count, bool):
        return None
    if recent_audit_count < 0:
        return None
    if recent_audit_count < config.audit_floor:
        return "audit"
    return None


def _env_text(name: str) -> str:
    return os.environ.get(name, "").strip()


def _parse_env_mode(name: str, default: PromotionMode) -> PromotionMode:
    raw = _env_text(name)
    if not raw:
        return default
    try:
        return PromotionMode(raw.lower())
    except ValueError:
        raise ValueError(f"invalid {name}: {raw!r}") from None


def _parse_env_float(
    name: str,
    default: float | None,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float | None:
    raw = _env_text(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"invalid {name}: {raw!r}") from None
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"invalid {name}: {raw!r}")
    return value


def _parse_env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = _env_text(name)
    if not raw or not re.fullmatch(r"[0-9]+", raw):
        if not raw:
            return default
        raise ValueError(f"invalid {name}: {raw!r}")
    value = int(raw)
    if value < minimum:
        raise ValueError(f"invalid {name}: {raw!r}")
    return value


def load_promotion_config() -> PromotionConfig:
    """Load strict operator settings from ``PROJECT_PROMOTION_*`` variables."""

    review_provider = _env_text("PROJECT_PROMOTION_REVIEW_PROVIDER").lower()
    if review_provider and review_provider not in _PROVIDER_NAMES:
        raise ValueError(f"invalid PROJECT_PROMOTION_REVIEW_PROVIDER: {review_provider!r}")
    config = PromotionConfig(
        host_mode=_parse_env_mode("PROJECT_PROMOTION_MODE", PromotionMode.OFF),
        relevance_threshold=_parse_env_float(
            "PROJECT_PROMOTION_RELEVANCE_THRESHOLD", None
        ),
        near_duplicate_threshold=_parse_env_float(
            "PROJECT_PROMOTION_NEAR_DUPLICATE_THRESHOLD", 0.88
        ),
        audit_floor=_parse_env_int("PROJECT_PROMOTION_AUDIT_FLOOR", 10, minimum=0),
        audit_period_days=_parse_env_int(
            "PROJECT_PROMOTION_AUDIT_PERIOD_DAYS", 7, minimum=1
        ),
        reconcile_batch=_parse_env_int(
            "PROJECT_PROMOTION_RECONCILE_BATCH", 25, minimum=1
        ),
        reconcile_budget_seconds=_parse_env_int(
            "PROJECT_PROMOTION_RECONCILE_BUDGET_SECONDS", 20, minimum=1
        ),
        rejected_retention_days=_parse_env_int(
            "PROJECT_PROMOTION_REJECTED_RETENTION_DAYS", 90, minimum=0
        ),
        unreviewable_rate_count=_parse_env_int(
            "PROJECT_PROMOTION_UNREVIEWABLE_RATE_COUNT", 5, minimum=1
        ),
        unreviewable_rate_window_hours=_parse_env_int(
            "PROJECT_PROMOTION_UNREVIEWABLE_RATE_WINDOW_HOURS", 1, minimum=1
        ),
        unreviewable_backlog_age_hours=_parse_env_int(
            "PROJECT_PROMOTION_UNREVIEWABLE_BACKLOG_AGE_HOURS", 168, minimum=1
        ),
        review_provider=review_provider,
        review_model=_env_text("PROJECT_PROMOTION_REVIEW_MODEL"),
    )
    if config.host_mode is not PromotionMode.OFF and config.relevance_threshold is None:
        raise ValueError(
            "PROJECT_PROMOTION_RELEVANCE_THRESHOLD is required when "
            "PROJECT_PROMOTION_MODE is shadow or auto"
        )
    return config


__all__ = [
    "PromotionConfig",
    "PromotionContext",
    "PromotionMode",
    "PromotionProposal",
    "PromotionReview",
    "PromotionState",
    "PromotionStatus",
    "ReviewDecision",
    "canonical_project_text",
    "load_promotion_config",
    "parse_proposal",
    "project_text_digest",
    "promotion_state_from_memory",
    "resolve_effective_mode",
    "select_review_route",
]
