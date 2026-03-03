"""HTTP client for the Memories API, used by the eval harness."""

from __future__ import annotations

import httpx


class MemoriesClient:
    """Thin wrapper around the Memories REST API."""

    def __init__(self, url: str = "http://localhost:8900", api_key: str = "") -> None:
        self._client = httpx.Client(
            base_url=url,
            headers={"X-API-Key": api_key},
            timeout=30.0,
        )

    def seed_memories(self, memories: list[dict]) -> list[int]:
        """POST /memory/add for each memory. Returns list of assigned IDs."""
        ids: list[int] = []
        for mem in memories:
            resp = self._client.post(
                "/memory/add",
                json={"text": mem["text"], "source": mem["source"], "deduplicate": False},
            )
            resp.raise_for_status()
            ids.append(resp.json()["id"])
        return ids

    def clear_by_prefix(self, prefix: str) -> int:
        """POST /memory/delete-by-prefix. Returns number of deleted memories."""
        resp = self._client.post(
            "/memory/delete-by-prefix",
            json={"source_prefix": prefix},
        )
        resp.raise_for_status()
        return resp.json().get("deleted_count", resp.json().get("deleted", 0))

    def health_check(self) -> bool:
        """GET /health/ready. Returns True if 200, False on any error."""
        try:
            resp = self._client.get("/health/ready")
            return resp.status_code == 200
        except (httpx.HTTPError, Exception):
            return False

    def get_stats(self) -> dict:
        """GET /stats. Returns parsed JSON dict."""
        resp = self._client.get("/stats")
        resp.raise_for_status()
        return resp.json()
