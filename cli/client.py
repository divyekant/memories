"""HTTP client wrapping all Memories API endpoints with typed errors."""

import time

import httpx


class CliConnectionError(Exception):
    """Server unreachable."""


class CliAuthError(Exception):
    """Authentication failed (401/403)."""


class CliNotFoundError(Exception):
    """Resource not found (404)."""


class CliServerError(Exception):
    """Server error (5xx)."""


class MemoriesClient:
    """HTTP client for the Memories API."""

    def __init__(self, url: str, api_key: str | None = None, transport=None):
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        kwargs = {"base_url": url.rstrip("/"), "headers": headers, "timeout": 30.0}
        if transport:
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)

    def _request(self, method: str, path: str, **kwargs):
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise CliConnectionError(f"Cannot connect to server: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise CliConnectionError(f"Request timed out: {exc}") from exc

        if resp.status_code in (401, 403):
            raise CliAuthError(f"Authentication failed: {resp.status_code}")
        if resp.status_code == 404:
            raise CliNotFoundError(f"Not found: {path}")
        if resp.status_code >= 500:
            raise CliServerError(f"Server error {resp.status_code}: {resp.text}")
        if resp.status_code == 422:
            detail = resp.json().get("detail", resp.text)
            raise ValueError(f"Validation error: {detail}")
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()

    # --- Health / Stats ---

    def health(self):
        return self._request("GET", "/health")

    def health_ready(self):
        return self._request("GET", "/health/ready")

    def stats(self):
        return self._request("GET", "/stats")

    def metrics(self):
        return self._request("GET", "/metrics")

    def usage(self, period: str = "daily"):
        return self._request("GET", "/usage", params={"period": period})

    # --- Search ---

    def search(self, query: str, k: int = 5, hybrid: bool = True,
               threshold: float | None = None, source_prefix: str | None = None):
        body: dict = {"query": query, "k": k, "hybrid": hybrid}
        if threshold is not None:
            body["threshold"] = threshold
        if source_prefix is not None:
            body["source_prefix"] = source_prefix
        return self._request("POST", "/search", json=body)

    def search_batch(self, queries: list[dict]):
        return self._request("POST", "/search/batch", json={"queries": queries})

    # --- Memory CRUD ---

    def add(self, text: str, source: str = "cli",
            deduplicate: bool = True, metadata: dict | None = None):
        body: dict = {"text": text, "source": source, "deduplicate": deduplicate}
        if metadata is not None:
            body["metadata"] = metadata
        return self._request("POST", "/memory/add", json=body)

    def add_batch(self, memories: list[dict], deduplicate: bool = True):
        return self._request("POST", "/memory/add-batch",
                             json={"memories": memories, "deduplicate": deduplicate})

    def get_memory(self, memory_id: str):
        return self._request("GET", f"/memory/{memory_id}")

    def get_batch(self, ids: list[str]):
        return self._request("POST", "/memory/get-batch", json={"ids": ids})

    def delete_memory(self, memory_id: str):
        return self._request("DELETE", f"/memory/{memory_id}")

    def delete_batch(self, ids: list[str]):
        return self._request("POST", "/memory/delete-batch", json={"ids": ids})

    def delete_by_source(self, pattern: str):
        return self._request("POST", "/memory/delete-by-source",
                             json={"source_pattern": pattern})

    def delete_by_prefix(self, prefix: str):
        return self._request("POST", "/memory/delete-by-prefix",
                             json={"source_prefix": prefix})

    def delete_bulk(self, source: str):
        return self._request("DELETE", "/memories", params={"source": source})

    def update_memory(self, memory_id: str, text: str | None = None,
                      source: str | None = None, metadata_patch: dict | None = None):
        body: dict = {}
        if text is not None:
            body["text"] = text
        if source is not None:
            body["source"] = source
        if metadata_patch is not None:
            body["metadata_patch"] = metadata_patch
        return self._request("PATCH", f"/memory/{memory_id}", json=body)

    # --- Upsert ---

    def upsert(self, text: str, source: str = "cli",
               key: str | None = None, metadata: dict | None = None):
        body: dict = {"text": text, "source": source}
        if key is not None:
            body["key"] = key
        if metadata is not None:
            body["metadata"] = metadata
        return self._request("POST", "/memory/upsert", json=body)

    def upsert_batch(self, memories: list[dict]):
        return self._request("POST", "/memory/upsert-batch",
                             json={"memories": memories})

    # --- Novelty / Supersede ---

    def is_novel(self, text: str, threshold: float = 0.85):
        return self._request("POST", "/memory/is-novel",
                             json={"text": text, "threshold": threshold})

    def supersede(self, old_id: str, text: str, source: str = "cli"):
        return self._request("POST", "/memory/supersede",
                             json={"old_id": old_id, "text": text, "source": source})

    # --- List / Count / Folders ---

    def list_memories(self, offset: int = 0, limit: int = 50,
                      source: str | None = None):
        params: dict = {"offset": offset, "limit": limit}
        if source is not None:
            params["source"] = source
        return self._request("GET", "/memories", params=params)

    def count(self, source: str | None = None):
        params = {}
        if source is not None:
            params["source"] = source
        return self._request("GET", "/memories/count", params=params)

    def folders(self):
        return self._request("GET", "/folders")

    def rename_folder(self, old: str, new: str):
        return self._request("POST", "/folders/rename",
                             json={"old_prefix": old, "new_prefix": new})

    # --- Maintenance ---

    def deduplicate(self, threshold: float = 0.95, dry_run: bool = False):
        return self._request("POST", "/memory/deduplicate",
                             json={"threshold": threshold, "dry_run": dry_run})

    def consolidate(self):
        return self._request("POST", "/maintenance/consolidate")

    def prune(self):
        return self._request("POST", "/maintenance/prune")

    def reload_embedder(self):
        return self._request("POST", "/maintenance/embedder/reload")

    # --- Backup ---

    def backup_list(self):
        return self._request("GET", "/backups")

    def backup_create(self, prefix: str | None = None):
        body = {}
        if prefix is not None:
            body["prefix"] = prefix
        return self._request("POST", "/backup", json=body)

    def backup_restore(self, name: str):
        return self._request("POST", "/restore", json={"backup_name": name})

    # --- Sync ---

    def sync_status(self):
        return self._request("GET", "/sync/status")

    def sync_upload(self):
        return self._request("POST", "/sync/upload")

    def sync_download(self, backup_name: str | None = None, confirm: bool = False):
        body: dict = {"confirm": confirm}
        if backup_name is not None:
            body["backup_name"] = backup_name
        return self._request("POST", "/sync/download", json=body)

    def sync_snapshots(self):
        return self._request("GET", "/sync/snapshots")

    def sync_restore(self, name: str, confirm: bool = False):
        return self._request("POST", f"/sync/restore/{name}",
                             json={"confirm": confirm})

    # --- Extract ---

    def extract_submit(self, messages: str, source: str = "cli",
                       context: str | None = None):
        body: dict = {"messages": messages, "source": source}
        if context is not None:
            body["context"] = context
        return self._request("POST", "/memory/extract", json=body)

    def extract_status(self, job_id: str):
        return self._request("GET", f"/memory/extract/{job_id}")

    def extract_system_status(self):
        return self._request("GET", "/extract/status")

    def extract_poll(self, job_id: str, timeout: float = 60.0,
                     interval: float = 1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.extract_status(job_id)
            status = result.get("status")
            if status in ("completed", "failed"):
                return result
            time.sleep(interval)
        raise TimeoutError(f"Extract job {job_id} did not complete in {timeout}s")

    # --- Export / Import ---

    def export_stream(self, source: str | None = None,
                      since: str | None = None, until: str | None = None):
        """Stream export lines from the server."""
        params = {}
        if source:
            params["source"] = source
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        with self._client.stream("GET", "/export", params=params) as resp:
            if resp.status_code in (401, 403):
                raise CliAuthError(f"Authentication failed: {resp.status_code}")
            if resp.status_code >= 500:
                raise CliServerError(f"Server error {resp.status_code}")
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.strip():
                    yield line

    def import_upload(self, lines: list[str], strategy: str = "add",
                      source_remap: str | None = None,
                      no_backup: bool = False):
        """Upload NDJSON lines for import."""
        params: dict = {"strategy": strategy}
        if source_remap:
            params["source_remap"] = source_remap
        if no_backup:
            params["no_backup"] = "true"
        body = "\n".join(lines)
        return self._request("POST", "/import", params=params,
                             content=body,
                             headers={"Content-Type": "application/x-ndjson"})
