"""Qdrant runtime configuration."""

from dataclasses import dataclass
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


@dataclass(frozen=True)
class QdrantSettings:
    url: str
    api_key: str
    collection: str
    wait: bool
    write_ordering: str
    read_consistency: str
    replication_factor: int
    write_consistency_factor: int

    @classmethod
    def from_env(cls) -> "QdrantSettings":
        return cls(
            # Empty URL means embedded/local mode (used by tests and local dev).
            url=os.getenv("QDRANT_URL", "").strip(),
            api_key=os.getenv("QDRANT_API_KEY", "").strip(),
            collection=os.getenv("QDRANT_COLLECTION", "memories").strip() or "memories",
            wait=_env_bool("QDRANT_WAIT", True),
            write_ordering=os.getenv("QDRANT_WRITE_ORDERING", "strong").strip() or "strong",
            read_consistency=os.getenv("QDRANT_READ_CONSISTENCY", "majority").strip() or "majority",
            replication_factor=_env_int("QDRANT_REPLICATION_FACTOR", 1, minimum=1),
            write_consistency_factor=_env_int("QDRANT_WRITE_CONSISTENCY_FACTOR", 1, minimum=1),
        )
