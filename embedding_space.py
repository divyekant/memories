"""Explicit embedding-space configuration and guard rails.

An "embedding space" is the combination of provider + model + dimension
(+ any query/document prefixes). Vectors from different spaces must never
be mixed in one Qdrant collection: same-dimension model swaps would corrupt
search silently. This module makes the space explicit:

- ``EmbedderSettings.from_env()`` — single source of truth for EMBED_* env.
- ``resolve_collection`` — collection names carry model + dimension
  (``memories__qwen3_embedding_0_6b_1024d``); the legacy default embedder
  (onnx / all-MiniLM-L6-v2, no prefixes) keeps the bare base name for
  backward compatibility with existing deployments.
- ``EmbeddingSpaceRegistry`` — a sidecar JSON file recording which signature
  created each collection; writes into a collection recorded under a
  different signature are refused (``EmbeddingSpaceMismatchError``).

Environment variables:
    EMBED_PROVIDER            onnx (default) | openai
    EMBED_MODEL               model name (provider-specific default if unset)
    EMBED_BASE_URL            OpenAI-compatible endpoint (e.g. oMLX at
                              http://host.docker.internal:11434/v1)
    EMBED_API_KEY             key for EMBED_BASE_URL (falls back to
                              OPENAI_API_KEY; optional for local endpoints)
    EMBED_DIMENSION           declared dimension; validated against the
                              loaded embedder, fails fast on mismatch
    EMBED_COLLECTION          pin the exact collection name (skips auto naming)
    EMBED_QUERY_PREFIX        prepended to query texts (e.g. "search_query: ")
    EMBED_DOC_PREFIX          prepended to document texts
    EMBED_ALLOW_SPACE_REBIND  allow re-recording a collection under a new
                              signature (requires a re-embed afterwards)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("memories")

DEFAULT_ONNX_MODEL = "all-MiniLM-L6-v2"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class EmbeddingSpaceMismatchError(RuntimeError):
    """Refusal to write vectors into a collection created by a different embedder."""


def model_slug(model: str) -> str:
    """Filesystem/collection-safe slug for a model name or HF repo id."""
    slug = _SLUG_RE.sub("_", model.strip().lower()).strip("_")
    return slug or "model"


def _prefix_fingerprint(query_prefix: str, document_prefix: str) -> str:
    if not query_prefix and not document_prefix:
        return ""
    digest = hashlib.sha1(
        f"q={query_prefix}\x00d={document_prefix}".encode("utf-8")
    ).hexdigest()[:8]
    return f"+pfx-{digest}"


def embedding_signature(
    provider: str,
    model: str,
    dim: int,
    query_prefix: str = "",
    document_prefix: str = "",
) -> str:
    """Canonical signature of an embedding space."""
    base = f"{provider}:{model}:{int(dim)}d"
    return base + _prefix_fingerprint(query_prefix, document_prefix)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class EmbedderSettings:
    provider: str = "onnx"
    model: str = DEFAULT_ONNX_MODEL
    base_url: str = ""
    api_key: str = ""
    declared_dim: Optional[int] = None
    collection_override: str = ""
    query_prefix: str = ""
    document_prefix: str = ""
    allow_space_rebind: bool = False

    @classmethod
    def from_env(cls, default_onnx_model: str = DEFAULT_ONNX_MODEL) -> "EmbedderSettings":
        provider = os.getenv("EMBED_PROVIDER", "onnx").strip().lower() or "onnx"
        model = os.getenv("EMBED_MODEL", "").strip()
        if not model:
            model = DEFAULT_OPENAI_MODEL if provider == "openai" else default_onnx_model

        declared_dim: Optional[int] = None
        raw_dim = os.getenv("EMBED_DIMENSION", "").strip()
        if raw_dim:
            try:
                declared_dim = max(1, int(raw_dim))
            except ValueError:
                logger.warning("Ignoring non-integer EMBED_DIMENSION=%r", raw_dim)

        return cls(
            provider=provider,
            model=model,
            base_url=os.getenv("EMBED_BASE_URL", "").strip(),
            api_key=os.getenv("EMBED_API_KEY", "").strip(),
            declared_dim=declared_dim,
            collection_override=os.getenv("EMBED_COLLECTION", "").strip(),
            query_prefix=os.getenv("EMBED_QUERY_PREFIX", ""),
            document_prefix=os.getenv("EMBED_DOC_PREFIX", ""),
            allow_space_rebind=_env_truthy("EMBED_ALLOW_SPACE_REBIND"),
        )

    @property
    def is_legacy_default(self) -> bool:
        """The original embedding space existing deployments were built with."""
        return (
            self.provider == "onnx"
            and self.model == DEFAULT_ONNX_MODEL
            and not self.query_prefix
            and not self.document_prefix
        )

    def signature(self, dim: int) -> str:
        return embedding_signature(
            self.provider,
            self.model,
            dim,
            query_prefix=self.query_prefix,
            document_prefix=self.document_prefix,
        )

    def resolve_collection(self, base: str, dim: int) -> str:
        """Collection name for this embedding space.

        Pinned override > legacy passthrough > explicit ``base__{model}_{dim}d``.
        """
        if self.collection_override:
            return self.collection_override
        if self.is_legacy_default:
            return base
        return f"{base}__{model_slug(self.model)}_{int(dim)}d"


class EmbeddingSpaceRegistry:
    """Sidecar JSON registry mapping collection name -> embedding signature.

    Qdrant has no collection-level custom metadata, so the signature that
    created each collection is recorded next to the engine's data. Existing
    collections are adopted on first sight (grandfathering pre-registry
    deployments); afterwards a signature change is refused unless rebinding
    is explicitly allowed.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- persistence ---------------------------------------------------

    def _load(self) -> Dict:
        if not self.path.exists():
            return {"collections": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("collections"), dict):
                raise ValueError("unexpected registry shape")
            return data
        except (ValueError, OSError) as exc:
            quarantine = self.path.with_name(
                f"{self.path.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
            )
            try:
                self.path.rename(quarantine)
                logger.error(
                    "Embedding space registry %s is corrupt (%s); quarantined to %s",
                    self.path, exc, quarantine,
                )
            except OSError:
                logger.error("Embedding space registry %s is corrupt (%s)", self.path, exc)
            return {"collections": {}}

    def _save(self, data: Dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- API -----------------------------------------------------------

    def signature_of(self, collection: str) -> Optional[str]:
        with self._lock:
            entry = self._load()["collections"].get(collection)
        if isinstance(entry, dict):
            return entry.get("signature")
        return None

    def check_and_record(
        self,
        collection: str,
        signature: str,
        allow_rebind: bool = False,
    ) -> str:
        """Validate the active signature against the recorded one.

        Returns "adopted" (first sight), "match", or "rebound".
        Raises EmbeddingSpaceMismatchError on conflict without rebind.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            data = self._load()
            entry = data["collections"].get(collection)
            recorded = entry.get("signature") if isinstance(entry, dict) else None

            if recorded == signature:
                return "match"

            if recorded is None:
                data["collections"][collection] = {
                    "signature": signature,
                    "recorded_at": now,
                }
                self._save(data)
                return "adopted"

            if not allow_rebind:
                raise EmbeddingSpaceMismatchError(
                    f"Collection {collection!r} was created with embedding space "
                    f"{recorded!r} but the active embedder is {signature!r}. "
                    "Refusing to mix embedding spaces. Either point at the right "
                    "collection (EMBED_COLLECTION / EMBED_MODEL), migrate with "
                    "scripts/reembed.py, or set EMBED_ALLOW_SPACE_REBIND=1 and "
                    "re-embed this collection in place."
                )

            previous = entry.get("previous", []) if isinstance(entry, dict) else []
            previous.append({"signature": recorded, "replaced_at": now})
            data["collections"][collection] = {
                "signature": signature,
                "recorded_at": now,
                "previous": previous,
            }
            self._save(data)
            return "rebound"
