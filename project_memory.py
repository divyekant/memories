"""Shared project-memory namespace and authorship policy.

This module intentionally contains only the policy primitives needed at the
memory creation boundary.  Authentication and request handling construct a
``TrustedAuthorship`` value; ``MemoryEngine`` is responsible for applying it
after filtering caller metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Optional

from transcript_hygiene import redact_secrets


PROJECT_KINDS = frozenset({"decisions", "knowledge", "state", "operations"})
ALLOWED_ORIGIN_CLIENTS = frozenset(
    {"codex", "claude-code", "hook", "manual", "other"}
)

# These fields are owned by the server-side creation boundary.  In
# particular, a caller may not use metadata to impersonate an author or to
# attach provenance that has not been authenticated by the server.
RESERVED_METADATA_FIELDS = frozenset(
    {
        "author",
        "contributors",
        "origin_client",
        "source_memory_ids",
        "authorship_verified",
    }
)

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class ProjectMemoryPolicyError(ValueError):
    """Raised when a project-namespace write violates its policy boundary."""


@dataclass(frozen=True)
class MemorySource:
    """A parsed, strictly valid project/person namespace source."""

    namespace: str
    project_id: str
    kind: str
    principal_id: Optional[str] = None

    @property
    def is_project(self) -> bool:
        return self.namespace == "project"

    @property
    def is_person(self) -> bool:
        return self.namespace == "person"


def is_valid_slug(value: Any) -> bool:
    """Return whether *value* is a lowercase, path-safe identifier slug."""

    return isinstance(value, str) and _SLUG_RE.fullmatch(value) is not None


def parse_memory_source(source: Any) -> Optional[MemorySource]:
    """Parse a strict person/project source, or return ``None`` for legacy.

    Only exact namespace shapes are recognized.  Sources that merely begin
    with ``person/`` or ``project/`` but have an invalid slug/kind/segment
    count are deliberately treated as legacy sources so existing data keeps
    its historical behavior.
    """

    if not isinstance(source, str):
        return None
    parts = source.split("/")
    if len(parts) == 4 and parts[0] == "person":
        _, principal_id, project_id, kind = parts
        if (
            is_valid_slug(principal_id)
            and is_valid_slug(project_id)
            and kind in PROJECT_KINDS
        ):
            return MemorySource(
                namespace="person",
                principal_id=principal_id,
                project_id=project_id,
                kind=kind,
            )
        return None

    if len(parts) == 3 and parts[0] == "project":
        _, project_id, kind = parts
        if is_valid_slug(project_id) and kind in PROJECT_KINDS:
            return MemorySource(
                namespace="project",
                principal_id=None,
                project_id=project_id,
                kind=kind,
            )
    return None


def is_project_source(source: Any) -> bool:
    """Return ``True`` only for a strict ``project/<id>/<kind>`` source."""

    parsed = parse_memory_source(source)
    return parsed is not None and parsed.is_project


def is_person_source(source: Any) -> bool:
    """Return ``True`` only for a strict ``person/<principal>/<id>/<kind>``."""

    parsed = parse_memory_source(source)
    return parsed is not None and parsed.is_person


def normalize_origin_client(value: Any) -> str:
    """Normalize a producer label to the small, documented allowlist."""

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ALLOWED_ORIGIN_CLIENTS:
            return normalized
    return "other"


def _normalized_unique_strings(values: Iterable[Any] | None) -> tuple[str, ...]:
    """Normalize contributor labels while retaining first-seen order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def _normalized_unique_ids(values: Iterable[Any] | None) -> tuple[Any, ...]:
    """Normalize source-memory IDs while retaining first-seen order.

    Memory IDs are normally integers, but preserving other scalar IDs keeps
    this value object usable with imported/system-derived records that use a
    string identifier.
    """

    result: list[Any] = []
    seen: set[Any] = set()
    for value in values or ():
        try:
            hash(value)
        except TypeError:
            continue
        if value not in seen:
            result.append(value)
            seen.add(value)
    return tuple(result)


@dataclass(frozen=True)
class TrustedAuthorship:
    """Server-derived authorship applied after caller metadata is filtered."""

    author: str
    origin_client: str = "other"
    contributors: tuple[str, ...] = ()
    source_memory_ids: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if self.author != "system" and not is_valid_slug(self.author):
            raise ProjectMemoryPolicyError("trusted author must be a valid principal slug or system")
        object.__setattr__(self, "origin_client", normalize_origin_client(self.origin_client))
        object.__setattr__(
            self, "contributors", _normalized_unique_strings(self.contributors)
        )
        object.__setattr__(
            self, "source_memory_ids", _normalized_unique_ids(self.source_memory_ids)
        )

    @classmethod
    def principal(cls, principal_id: str, origin_client: Any = "other") -> "TrustedAuthorship":
        """Build trusted authorship for a principal-originated write."""

        return cls(author=principal_id, origin_client=normalize_origin_client(origin_client))

    @classmethod
    def system(
        cls,
        contributors: Iterable[Any] | None = None,
        source_memory_ids: Iterable[Any] | None = None,
        origin_client: Any = "other",
    ) -> "TrustedAuthorship":
        """Build trusted authorship for a server-derived memory."""

        return cls(
            author="system",
            origin_client=normalize_origin_client(origin_client),
            contributors=tuple(contributors or ()),
            source_memory_ids=tuple(source_memory_ids or ()),
        )

    def as_metadata(self) -> dict[str, Any]:
        """Return only the server-owned fields that should be persisted."""

        metadata: dict[str, Any] = {
            "author": self.author,
            "origin_client": self.origin_client,
            "authorship_verified": True,
        }
        if self.author == "system":
            metadata["contributors"] = list(self.contributors)
            metadata["source_memory_ids"] = list(self.source_memory_ids)
        return metadata


def validate_project_write(
    text: Any,
    source: Any,
    trusted_authorship: Optional[TrustedAuthorship],
) -> None:
    """Validate an exact project write before any mutation or storage work."""
    if isinstance(source, str) and source.startswith("project/") and not is_project_source(source):
        raise ProjectMemoryPolicyError(
            "project sources must be project/<project>/<decisions|knowledge|state|operations>"
        )
    if not is_project_source(source):
        return
    if trusted_authorship is None:
        raise ProjectMemoryPolicyError(
            "project-namespace memories require trusted principal or system authorship"
        )
    _, redacted_types = redact_secrets(str(text or ""))
    if redacted_types:
        raise ProjectMemoryPolicyError(
            "project-namespace memories cannot contain credential-shaped values"
        )
