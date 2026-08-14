"""Shared project-memory namespace and authorship policy.

This module intentionally contains only the policy primitives needed at the
memory creation boundary.  Authentication and request handling construct a
``TrustedAuthorship`` value; ``MemoryEngine`` is responsible for applying it
after filtering caller metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Optional

from transcript_hygiene import redact_secrets


PROJECT_KINDS = frozenset({"decisions", "knowledge", "state", "operations"})
ALLOWED_ORIGIN_CLIENTS = frozenset(
    {"codex", "claude-code", "hook", "manual", "other"}
)

# These fields are owned by the server-side creation boundary.  In
# particular, a caller may not use metadata to impersonate an author or to
# attach provenance that has not been authenticated by the server.
RESERVED_METADATA_FIELDS = frozenset(
    {"author", "contributors", "origin_client", "source_memory_ids"}
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
    """Parse a strict person/project source, or return ``None`` for non-strict data.

    Only exact namespace shapes are recognized.  A malformed ``project/``
    source is returned as ``None`` for compatibility with existing records and
    read/export/delete paths; :func:`validate_project_write` still treats that
    reserved prefix as non-writable, so parsing it as ``None`` must not be
    interpreted as permission to create or update it.
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


def is_project_namespace_prefix(source: Any) -> bool:
    """Return whether *source* begins with the reserved project namespace."""

    return isinstance(source, str) and source.startswith("project/")


def is_person_source(source: Any) -> bool:
    """Return ``True`` only for a strict ``person/<principal>/<id>/<kind>``."""

    parsed = parse_memory_source(source)
    return parsed is not None and parsed.is_person


def is_reserved_namespace_source(source: Any) -> bool:
    """Return whether *source* occupies a reserved project/person prefix.

    This intentionally includes malformed historical records.  They remain
    readable and deletable, but any move involving one is fail-closed until a
    deliberate migration path is used.
    """

    return isinstance(source, str) and source.startswith(("project/", "person/"))


def _source_policy_namespace(source: Any) -> tuple[str, ...]:
    """Return the complete authorship-policy identity for a source."""

    parsed = parse_memory_source(source)
    if parsed is not None:
        if parsed.is_person:
            return (parsed.namespace, parsed.principal_id or "", parsed.project_id)
        return (parsed.namespace, parsed.project_id)
    if is_reserved_namespace_source(source):
        return ("reserved", str(source))
    return ("legacy",)


def is_namespace_crossing_source_move(
    current_source: Any,
    new_source: Any,
) -> bool:
    """Return whether a source move crosses an authorship policy boundary."""

    return (
        new_source is not None
        and new_source != current_source
        and _source_policy_namespace(current_source)
        != _source_policy_namespace(new_source)
    )


def is_substantive_authored_content_replacement(
    *,
    current_text: Any = None,
    new_text: Any = None,
    current_source: Any = None,
    new_source: Any = None,
    metadata_patch: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Whether an edit replaces authored content and should restamp identity.

    Metadata-only patches intentionally return ``False``: metadata is mutable
    bookkeeping and must not erase contributor/provenance fields.  A changed
    text value is substantive.  Source changes are substantive only when they
    cross an authorship-policy identity. Kind-only moves within the same
    person/project preserve provenance; owner or project changes do not.
    """

    del metadata_patch  # Reserved metadata is filtered by the write boundary.
    if new_text is not None and new_text != current_text:
        return True
    return is_namespace_crossing_source_move(current_source, new_source)


def validate_namespace_preserving_replacement(
    existing_sources: Iterable[Any],
    target_source: Any,
    operation: str,
) -> None:
    """Reject supersede/merge/move operations across reserved namespaces.

    The guard is intentionally independent of caller type or authentication:
    an env-admin or unmanaged caller must not accidentally re-home a strict
    person/project record.  Exact-source replacements remain valid and are
    checked by the ordinary project-write policy separately.
    """

    for existing_source in existing_sources:
        if existing_source == target_source:
            continue
        if is_reserved_namespace_source(existing_source) or is_reserved_namespace_source(
            target_source
        ):
            raise ProjectMemoryPolicyError(
                f"{operation} source cannot cross project or person namespace boundaries"
            )


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
    if is_reserved_namespace_source(source) and parse_memory_source(source) is None:
        raise ProjectMemoryPolicyError(
            "project sources must be project/<project>/<kind>; person sources "
            "must be person/<principal>/<project>/<kind>, with a supported kind"
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
