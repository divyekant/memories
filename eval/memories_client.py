"""HTTP client for the Memories API, used by the eval harness."""

from __future__ import annotations

import httpx


class MemoriesClient:
    """Thin wrapper around the Memories REST API."""

    MAX_BATCH_MEMORIES = 1000

    def __init__(self, url: str = "http://localhost:8900", api_key: str = "") -> None:
        self._client = httpx.Client(
            base_url=url,
            headers={"X-API-Key": api_key},
            timeout=30.0,
        )

    def seed_memories(self, memories: list[dict]) -> list[int]:
        """Add memories, preferring batch mode for larger payloads."""
        if not memories:
            return []
        if len(memories) == 1:
            mem = memories[0]
            resp = self._client.post(
                "/memory/add",
                json={
                    "text": mem["text"],
                    "source": mem["source"],
                    "metadata": mem.get("metadata"),
                    "deduplicate": False,
                },
            )
            resp.raise_for_status()
            return [resp.json()["id"]]
        return self.add_batch(memories, deduplicate=False)

    def add_batch(self, memories: list[dict], deduplicate: bool = False) -> list[int]:
        """POST /memory/add-batch. Returns list of assigned IDs."""
        if not memories:
            return []
        ids: list[int] = []
        for start in range(0, len(memories), self.MAX_BATCH_MEMORIES):
            batch = memories[start:start + self.MAX_BATCH_MEMORIES]
            resp = self._client.post(
                "/memory/add-batch",
                json={"memories": batch, "deduplicate": deduplicate},
            )
            resp.raise_for_status()
            ids.extend(resp.json().get("ids", []))
        return ids

    def clear_by_prefix(self, prefix: str) -> int:
        """POST /memory/delete-by-prefix. Returns number of deleted memories."""
        resp = self._client.post(
            "/memory/delete-by-prefix",
            json={"source_prefix": prefix},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("deleted_count", data.get("deleted", 0))

    def health_check(self) -> bool:
        """GET /health/ready. Returns True if 200, False on any error."""
        try:
            resp = self._client.get("/health/ready")
            return resp.status_code == 200
        except (httpx.HTTPError, Exception):
            return False

    def search(self, query: str, k: int = 5, hybrid: bool = True, feedback_weight: float = 0.0, source_prefix: str | None = None) -> list[dict]:
        """POST /search — returns results list."""
        body: dict = {"query": query, "k": k, "hybrid": hybrid, "feedback_weight": feedback_weight}
        if source_prefix is not None:
            body["source_prefix"] = source_prefix
        resp = self._client.post("/search", json=body)
        resp.raise_for_status()
        return resp.json().get("results", [])

    def extract(self, messages: str, source: str, context: str = "stop", dry_run: bool = False) -> dict:
        """POST /memory/extract — submits extraction job, polls until complete."""
        import time

        resp = self._client.post(
            "/memory/extract",
            json={"messages": messages, "source": source, "context": context, "dry_run": dry_run},
        )
        resp.raise_for_status()
        job_id = resp.json().get("job_id")
        if not job_id:
            return resp.json()
        # Poll until complete (max 30s)
        for _ in range(30):
            time.sleep(1)
            poll = self._client.get(f"/memory/extract/{job_id}")
            poll.raise_for_status()
            data = poll.json()
            if data.get("status") in ("completed", "failed"):
                return data
        return {"status": "timeout", "job_id": job_id}

    def get_stats(self) -> dict:
        """GET /stats. Returns parsed JSON dict."""
        resp = self._client.get("/stats")
        resp.raise_for_status()
        return resp.json()
