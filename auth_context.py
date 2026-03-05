"""Request-scoped auth context — role checks and prefix enforcement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AuthContext:
    role: str  # "admin", "read-write", "read-only"
    prefixes: Optional[List[str]]  # None = unrestricted (admin/env)
    key_type: str  # "env", "managed", "none"
    key_id: Optional[str] = None
    key_name: Optional[str] = None

    # -- constructors --------------------------------------------------------

    @classmethod
    def unrestricted(cls) -> AuthContext:
        """Admin context with no prefix restrictions."""
        return cls(role="admin", prefixes=None, key_type="none")

    # -- prefix helpers ------------------------------------------------------

    def _matches_prefix(self, source: str) -> bool:
        """Check *source* against allowed prefixes.

        If prefixes is None the caller is unrestricted → always True.
        Otherwise each prefix is normalised (strip trailing ``/*`` and ``/``)
        then tested with ``source.startswith(base + "/")``.
        """
        if self.prefixes is None:
            return True
        for pfx in self.prefixes:
            base = pfx.rstrip("/").removesuffix("/*").rstrip("/")
            if source.startswith(base + "/"):
                return True
        return False

    # -- permission checks ---------------------------------------------------

    def can_read(self, source: str) -> bool:
        return self._matches_prefix(source)

    def can_write(self, source: str) -> bool:
        if self.role == "read-only":
            return False
        return self._matches_prefix(source)

    @property
    def can_manage_keys(self) -> bool:
        return self.role == "admin"

    # -- result filtering ----------------------------------------------------

    def filter_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return only results whose source matches allowed prefixes."""
        if self.prefixes is None:
            return results
        return [r for r in results if self._matches_prefix(r.get("source", ""))]

    # -- introspection -------------------------------------------------------

    def to_me_response(self) -> Dict[str, Any]:
        """Dict suitable for the ``/me`` endpoint."""
        resp: Dict[str, Any] = {"type": self.key_type, "role": self.role}
        if self.prefixes is not None:
            resp["prefixes"] = self.prefixes
        if self.key_id is not None:
            resp["id"] = self.key_id
        if self.key_name is not None:
            resp["name"] = self.key_name
        return resp
