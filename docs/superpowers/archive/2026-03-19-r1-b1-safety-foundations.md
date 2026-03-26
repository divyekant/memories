# R1 Batch 1: Safety + Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make destructive memory operations safe and reversible — pre-delete Qdrant snapshots, pin/protect, soft archive, merge API, and CLI links command.

**Architecture:** Extends `QdrantStore` with snapshot methods, adds `pinned`/`archived` fields to memory payloads with Qdrant-level filtering, adds merge as a composite operation (add + link + archive), and adds a new CLI command group for links. All changes are additive — no existing behavior changes unless `pinned` or `archived` is explicitly set.

**Tech Stack:** Python 3.11+, FastAPI, Qdrant Python client, Click CLI, pytest

**Spec:** `docs/superpowers/specs/2026-03-19-r1-controllable-memory-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `qdrant_store.py` | Qdrant snapshot create/list/restore, archived payload index | Modify |
| `memory_engine.py` | Snapshot wrapper, pin/archive on update_memory, archive filter in search, merge_memories, delete exclusion for pinned | Modify |
| `app.py` | PatchMemoryRequest fields, snapshot endpoints, archive-batch, merge endpoint, audit events | Modify |
| `llm_extract.py` | Skip AUDN DELETE/UPDATE for pinned memories | Modify |
| `cli/commands/links.py` | Links list/add/remove CLI commands | Create |
| `cli/__init__.py` | Register links command group | Modify |
| `tests/test_snapshots.py` | Snapshot lifecycle tests | Create |
| `tests/test_pin_archive.py` | Pin/archive behavior + filter tests | Create |
| `tests/test_merge_api.py` | Merge endpoint + engine tests | Create |
| `tests/test_cli_links.py` | CLI links command tests | Create |

---

### Task 1: QdrantStore snapshot methods

**Files:**
- Modify: `qdrant_store.py:130-140` (after `ensure_payload_indexes`)
- Test: `tests/test_snapshots.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_snapshots.py`:

```python
"""Tests for QdrantStore snapshot operations."""
import pytest
from unittest.mock import MagicMock, patch
from qdrant_store import QdrantStore


class TestQdrantStoreSnapshots:
    """Test snapshot create/list/restore on QdrantStore."""

    def setup_method(self):
        self.mock_client = MagicMock()
        self.store = QdrantStore.__new__(QdrantStore)
        self.store.client = self.mock_client
        self.store.collection = "test_memories"
        self.store._local_path = None  # remote mode

    def test_create_snapshot_remote(self):
        self.mock_client.create_snapshot.return_value = MagicMock(
            name="snapshot-2026-03-19"
        )
        name = self.store.create_snapshot()
        assert name == "snapshot-2026-03-19"
        self.mock_client.create_snapshot.assert_called_once_with(
            collection_name="test_memories"
        )

    def test_list_snapshots_remote(self):
        self.mock_client.list_snapshots.return_value = [
            MagicMock(name="snap1", creation_time="2026-03-19T10:00:00", size=1024),
        ]
        result = self.store.list_snapshots()
        assert len(result) == 1
        assert result[0]["name"] == "snap1"

    def test_create_snapshot_local_mode(self):
        self.store._local_path = "/tmp/qdrant-test"
        with patch("shutil.copytree") as mock_copy, \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True):
            mock_copy.return_value = None
            name = self.store.create_snapshot()
            assert name.startswith("local-backup-")
            mock_copy.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_snapshots.py -v`
Expected: FAIL — `create_snapshot` not defined

- [ ] **Step 3: Implement snapshot methods in qdrant_store.py**

Add after `delete_points()` (line 208) in `qdrant_store.py`:

```python
    # -- Snapshots --------------------------------------------------------- #

    def create_snapshot(self) -> str:
        """Create a collection snapshot. Returns snapshot name.
        Remote mode: uses Qdrant snapshots API.
        Local mode: filesystem copy of storage directory."""
        if self._local_path:
            import shutil
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_name = f"local-backup-{ts}"
            backup_dir = os.path.join(self._local_path, ".snapshots", backup_name)
            os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
            shutil.copytree(self._local_path, backup_dir,
                            ignore=shutil.ignore_patterns(".snapshots"))
            return backup_name
        snapshot = self.client.create_snapshot(collection_name=self.collection)
        return snapshot.name

    def list_snapshots(self) -> list[dict]:
        """List available snapshots. Returns list of {name, creation_time, size}."""
        if self._local_path:
            snap_dir = os.path.join(self._local_path, ".snapshots")
            if not os.path.exists(snap_dir):
                return []
            return [
                {"name": d, "creation_time": "", "size": 0}
                for d in sorted(os.listdir(snap_dir))
                if os.path.isdir(os.path.join(snap_dir, d))
            ]
        snapshots = self.client.list_snapshots(collection_name=self.collection)
        return [
            {"name": s.name, "creation_time": str(s.creation_time), "size": s.size}
            for s in snapshots
        ]

    def restore_snapshot(self, name: str) -> None:
        """Restore from a snapshot. Local mode: replaces storage from backup."""
        if self._local_path:
            import shutil
            backup_dir = os.path.join(self._local_path, ".snapshots", name)
            if not os.path.exists(backup_dir):
                raise ValueError(f"Snapshot {name} not found")
            # Remove current data (except .snapshots) and copy back
            for item in os.listdir(self._local_path):
                if item == ".snapshots":
                    continue
                path = os.path.join(self._local_path, item)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            for item in os.listdir(backup_dir):
                src = os.path.join(backup_dir, item)
                dst = os.path.join(self._local_path, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            return
        self.client.recover_snapshot(
            collection_name=self.collection,
            location=name,
        )
```

Also add required imports at top of file: `import os` and `from datetime import datetime`.

Note: `_local_path` needs to be stored as an instance variable. In the constructor (lines 37-49), after `self.client = ...` add `self._local_path = None` for remote mode and `self._local_path = local_path` for local mode.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_snapshots.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add qdrant_store.py tests/test_snapshots.py
git commit -m "feat: add QdrantStore snapshot create/list/restore methods"
```

---

### Task 2: Engine snapshot-before-delete wrapper and API endpoints

**Files:**
- Modify: `memory_engine.py:738-764` (wrap delete_by_source, delete_by_prefix)
- Modify: `memory_engine.py:583-609` (wrap delete_memories)
- Modify: `app.py` (add snapshot endpoints, add dry_run to delete endpoints)
- Test: `tests/test_snapshots.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_snapshots.py`:

```python
class TestEngineSnapshotBeforeDelete:
    """Test that delete operations create snapshots first."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.list_snapshots.return_value = []
                mock_engine._snapshot_before_delete.return_value = "snap-001"
                mock_engine.delete_by_source.return_value = {"deleted_count": 2}
                mock_engine.delete_by_prefix.return_value = {"deleted_count": 2}
                mock_engine.add_memories.return_value = [1]
                mock_engine.qdrant_store = MagicMock()
                mock_engine.qdrant_store.create_snapshot.return_value = "snap-001"
                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_list_snapshots(self, client):
        tc, mock = client
        resp = tc.get("/snapshots")
        assert resp.status_code == 200

    def test_create_snapshot_manual(self, client):
        tc, mock = client
        resp = tc.post("/snapshots")
        assert resp.status_code == 200
        assert "name" in resp.json()

    def test_delete_by_source_with_dry_run(self, client):
        tc, mock = client
        mock.delete_by_source.return_value = {"count": 2, "would_delete": [1, 2]}
        resp = tc.post("/memory/delete-by-source",
                      json={"source_pattern": "test/", "dry_run": True})
        assert resp.status_code == 200
        mock.delete_by_source.assert_called_once()
        call_kwargs = mock.delete_by_source.call_args
        assert call_kwargs[1].get("dry_run") is True or (len(call_kwargs[0]) > 1 and call_kwargs[0][1] is True)
```

Note: These tests use the `client` fixture from `conftest.py` which provides a TestClient with a live in-memory engine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_snapshots.py::TestEngineSnapshotBeforeDelete -v`
Expected: FAIL — endpoints don't exist yet

- [ ] **Step 3: Add _snapshot_before_delete to memory_engine.py**

Add after `_delete_ids_targeted()` method in `memory_engine.py`:

```python
    def _snapshot_before_delete(self, reason: str) -> str:
        """Create Qdrant snapshot before a destructive operation.
        Stores metadata in /data/snapshots/manifest.json."""
        snapshot_name = self.qdrant_store.create_snapshot()
        manifest_path = os.path.join(self.data_dir, "snapshots", "manifest.json")
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        manifest = []
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
        manifest.append({
            "name": snapshot_name,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "point_count": len(self.metadata),
        })
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        return snapshot_name

    def list_snapshots(self) -> list[dict]:
        """List snapshots with metadata from manifest."""
        manifest_path = os.path.join(self.data_dir, "snapshots", "manifest.json")
        if not os.path.exists(manifest_path):
            return []
        with open(manifest_path) as f:
            return json.load(f)
```

- [ ] **Step 4: Wrap delete_by_source with auto-snapshot and dry_run**

Modify `delete_by_source()` (line 738) to add `skip_snapshot` and `dry_run` params:

```python
    def delete_by_source(self, source_pattern: str, skip_snapshot: bool = False,
                         dry_run: bool = False) -> Dict[str, Any]:
        """Delete all memories matching a source pattern."""
        with self._entity_locks.acquire_many(["__all__"]):
            with self._write_lock:
                matching = [m for m in self.metadata if source_pattern in m.get("source", "")]
                if not matching:
                    return {"deleted_count": 0} if not dry_run else {"count": 0, "would_delete": []}

                if dry_run:
                    return {"count": len(matching), "would_delete": [m["id"] for m in matching]}

                if not skip_snapshot:
                    self._snapshot_before_delete(f"delete_by_source:{source_pattern}")

                ids_to_remove = {m["id"] for m in matching}
                self._delete_ids_targeted(ids_to_remove)
                self.config["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._rebuild_bm25()
        return {"deleted_count": len(matching)}
```

Apply the same pattern to `delete_by_prefix()` (line 757) and `delete_memories()` (line 583, only snapshot when `len(ids) > 10`).

- [ ] **Step 5: Add snapshot API endpoints to app.py**

Add after the existing maintenance endpoints in `app.py`:

```python
# -- Snapshot endpoints ---------------------------------------------------- #

@app.get("/snapshots")
async def list_snapshots(request: Request):
    """List available Qdrant snapshots."""
    _get_auth(request)  # auth check
    return memory.list_snapshots()


@app.post("/snapshots")
async def create_snapshot(request: Request):
    """Create a manual Qdrant snapshot."""
    auth = _get_auth(request)
    name = memory._snapshot_before_delete("manual")
    _audit(request, "snapshot.created", resource_id=name)
    return {"name": name}


@app.post("/snapshots/{name}/restore")
async def restore_snapshot(name: str, request: Request):
    """Restore from a Qdrant snapshot."""
    auth = _get_auth(request)
    _require_admin(auth)
    try:
        memory.qdrant_store.restore_snapshot(name)
        _audit(request, "snapshot.restored", resource_id=name)
        return {"restored": name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

Also add `dry_run` and `skip_snapshot` fields to `DeleteBySourceRequest` and `DeleteByPrefixRequest` models.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_snapshots.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add memory_engine.py app.py qdrant_store.py tests/test_snapshots.py
git commit -m "feat: auto-snapshot before bulk deletes, dry-run mode, snapshot API"
```

---

### Task 3: Pin/protect — PatchMemoryRequest and engine changes

**Files:**
- Modify: `app.py:1229-1232` (PatchMemoryRequest)
- Modify: `memory_engine.py:828-849` (update_memory)
- Modify: `memory_engine.py:738-764` (exclude pinned from bulk delete)
- Test: `tests/test_pin_archive.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pin_archive.py`:

```python
"""Tests for pin/protect and soft archive features."""
import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestPinProtect:
    """Test pinning memories and protection from bulk operations."""

    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                mock_engine = MagicMock()
                mock_engine.update_memory.return_value = {"id": 1, "pinned": True, "updated_fields": ["pinned"]}
                mock_engine.get_memory.return_value = {"id": 1, "text": "test", "source": "test/"}
                mock_engine.delete_by_source.return_value = {"deleted_count": 1}
                mock_engine.delete_by_prefix.return_value = {"deleted_count": 1}
                app_module.memory = mock_engine
                yield TestClient(app_module.app), mock_engine

    def test_pin_memory(self, client):
        tc, mock = client
        patch_resp = tc.patch("/memory/1", json={"pinned": True})
        assert patch_resp.status_code == 200
        mock.update_memory.assert_called_once_with(
            memory_id=1, text=None, source=None,
            metadata_patch=None, pinned=True, archived=None,
        )

    def test_unpin_memory(self, client):
        tc, mock = client
        tc.patch("/memory/1", json={"pinned": False})
        mock.update_memory.assert_called_with(
            memory_id=1, text=None, source=None,
            metadata_patch=None, pinned=False, archived=None,
        )

    def test_patch_rejects_empty_body(self, client):
        tc, mock = client
        resp = tc.patch("/memory/1", json={})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_pin_archive.py::TestPinProtect -v`
Expected: FAIL — `pinned` not accepted by PatchMemoryRequest

- [ ] **Step 3: Add pinned/archived to PatchMemoryRequest**

In `app.py` (line 1229), update the model:

```python
class PatchMemoryRequest(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=50000)
    source: Optional[str] = Field(None, min_length=1, max_length=500)
    metadata_patch: Optional[dict] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
```

- [ ] **Step 4: Update update_memory() to handle pinned/archived**

In `memory_engine.py` (line 828), add `pinned` and `archived` params to `update_memory()`:

```python
    def update_memory(
        self,
        memory_id: int,
        text: Optional[str] = None,
        source: Optional[str] = None,
        metadata_patch: Optional[Dict[str, Any]] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> Dict[str, Any]:
```

In the method body, after the existing metadata_patch handling, add:

```python
        if pinned is not None:
            current["pinned"] = pinned
            self.qdrant_store.set_payload(memory_id, {"pinned": pinned})
            updated_fields.append("pinned")
        if archived is not None:
            current["archived"] = archived
            self.qdrant_store.set_payload(memory_id, {"archived": archived})
            updated_fields.append("archived")
```

- [ ] **Step 5: Update PATCH endpoint to pass pinned/archived**

In `app.py` (line 2029), update the `patch_memory` handler to pass the new fields:

```python
        result = memory.update_memory(
            memory_id=memory_id,
            text=request_body.text,
            source=request_body.source,
            metadata_patch=request_body.metadata_patch,
            pinned=request_body.pinned,
            archived=request_body.archived,
        )
```

Also update the empty-check at the top:

```python
        if (request_body.text is None and request_body.source is None
                and not request_body.metadata_patch
                and request_body.pinned is None and request_body.archived is None):
            raise HTTPException(status_code=400, detail="At least one field must be provided")
```

Add `pinned` query parameter to `GET /memories` endpoint (around line 2134 in `app.py`) so users can filter `?pinned=true`:

```python
@app.get("/memories")
async def list_memories(..., pinned: Optional[bool] = None):
    # After existing filtering, add:
    if pinned is not None:
        memories = [m for m in memories if m.get("pinned") == pinned]
```

Add audit calls for pin/archive:

```python
        if request_body.pinned is not None:
            action = "memory.pinned" if request_body.pinned else "memory.unpinned"
            _audit(request, action, resource_id=str(memory_id))
        if request_body.archived is not None:
            action = "memory.archived" if request_body.archived else "memory.unarchived"
            _audit(request, action, resource_id=str(memory_id))
```

- [ ] **Step 6: Exclude pinned from bulk deletes**

In `delete_by_source()` and `delete_by_prefix()` in `memory_engine.py`, filter out pinned:

```python
                matching = [m for m in self.metadata
                           if source_pattern in m.get("source", "")
                           and not m.get("pinned")]
```

Same for `delete_by_prefix`:

```python
                matching = [m for m in self.metadata
                           if m.get("source", "").startswith(source_prefix)
                           and not m.get("pinned")]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_pin_archive.py::TestPinProtect -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add app.py memory_engine.py tests/test_pin_archive.py
git commit -m "feat: pin/protect memories — exclude from bulk deletes and AUDN mutations"
```

---

### Task 4: Soft archive — search filter, archive-batch endpoint

**Files:**
- Modify: `qdrant_store.py:130-140` (add archived payload index)
- Modify: `memory_engine.py:985-1012` (_build_source_filter)
- Modify: `memory_engine.py:214-215` (_point_payload)
- Modify: `app.py` (archive-batch endpoint, include_archived on search)
- Test: `tests/test_pin_archive.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pin_archive.py`:

```python
class TestSoftArchive:
    """Test soft archive — archived memories excluded from search."""

    def test_archive_memory(self, client):
        resp = client.post("/memory/add", json={"text": "archive me", "source": "arch-test/"})
        mem_id = resp.json()["id"]

        patch_resp = client.patch(f"/memory/{mem_id}", json={"archived": True})
        assert patch_resp.status_code == 200

    def test_archived_excluded_from_search(self, client):
        resp = client.post("/memory/add", json={"text": "unique searchable archflag test", "source": "arch-search/"})
        mem_id = resp.json()["id"]

        # Should appear in search before archiving
        search_resp = client.post("/search", json={"query": "unique searchable archflag", "k": 5})
        ids_before = [r["id"] for r in search_resp.json()["results"]]
        assert mem_id in ids_before

        # Archive it
        client.patch(f"/memory/{mem_id}", json={"archived": True})

        # Should NOT appear in search after archiving
        search_resp2 = client.post("/search", json={"query": "unique searchable archflag", "k": 5})
        ids_after = [r["id"] for r in search_resp2.json()["results"]]
        assert mem_id not in ids_after

    def test_archived_included_with_flag(self, client):
        resp = client.post("/memory/add", json={"text": "archive include test", "source": "arch-inc/"})
        mem_id = resp.json()["id"]
        client.patch(f"/memory/{mem_id}", json={"archived": True})

        search_resp = client.post("/search",
                                 json={"query": "archive include test", "k": 5, "include_archived": True})
        ids = [r["id"] for r in search_resp.json()["results"]]
        assert mem_id in ids

    def test_unarchive_restores_searchability(self, client):
        resp = client.post("/memory/add", json={"text": "unarchive restore test xyz", "source": "arch-restore/"})
        mem_id = resp.json()["id"]

        client.patch(f"/memory/{mem_id}", json={"archived": True})
        client.patch(f"/memory/{mem_id}", json={"archived": False})

        search_resp = client.post("/search", json={"query": "unarchive restore test xyz", "k": 5})
        ids = [r["id"] for r in search_resp.json()["results"]]
        assert mem_id in ids

    def test_archive_batch(self, client):
        r1 = client.post("/memory/add", json={"text": "batch1", "source": "batch-arch/"})
        r2 = client.post("/memory/add", json={"text": "batch2", "source": "batch-arch/"})
        ids = [r1.json()["id"], r2.json()["id"]]

        resp = client.post("/memory/archive-batch", json={"ids": ids})
        assert resp.status_code == 200
        assert resp.json()["archived_count"] == 2

    def test_list_archived_memories(self, client):
        resp = client.post("/memory/add", json={"text": "list arch test", "source": "list-arch/"})
        mem_id = resp.json()["id"]
        client.patch(f"/memory/{mem_id}", json={"archived": True})

        list_resp = client.get("/stats")
        assert list_resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_pin_archive.py::TestSoftArchive -v`
Expected: FAIL

- [ ] **Step 3: Add archived to _point_payload and payload index**

No change needed to `_point_payload()` — the existing implementation already copies all meta fields to payload. When `archived: True` is set via `set_payload()`, it will be in the Qdrant payload. Points without `archived` in their payload correctly pass the `must_not: [archived == true]` filter (Qdrant treats missing fields as non-matching).

In `qdrant_store.py` `ensure_payload_indexes()` (line 130), add archived index:

```python
    def ensure_payload_indexes(self) -> None:
        """Create payload indexes for efficient filtering."""
        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="source",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass  # index may already exist
        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="archived",
                field_schema=models.PayloadSchemaType.BOOL,
            )
        except Exception:
            pass
```

- [ ] **Step 4: Extend _build_source_filter to exclude archived**

In `memory_engine.py` `_build_source_filter()` (line 985), add `include_archived` param:

```python
    def _build_source_filter(
        self,
        source_prefix: Optional[str] = None,
        allowed_prefixes: Optional[List[str]] = None,
        include_archived: bool = False,
    ) -> Optional[qdrant_models.Filter]:
```

At the end of the method, before returning the filter, add the archived exclusion:

```python
        conditions = list(filter_obj.must) if filter_obj and filter_obj.must else []
        must_not = []
        if not include_archived:
            must_not.append(
                qdrant_models.FieldCondition(
                    key="archived",
                    match=qdrant_models.MatchValue(value=True),
                )
            )
        if must_not:
            if filter_obj is None:
                filter_obj = qdrant_models.Filter(must_not=must_not)
            else:
                filter_obj.must_not = must_not
        return filter_obj
```

**Wire `include_archived` through search methods:**

In `memory_engine.py`, add `include_archived: bool = False` parameter to:
- `search()` (around line 1026) — pass to `_build_source_filter(include_archived=include_archived)`
- `hybrid_search()` (around line 1050) — same pattern
- `hybrid_search_explain()` — same pattern

In `app.py`, add `include_archived: bool = False` to `SearchRequest` model and pass it through the search endpoint handlers to the engine methods.

- [ ] **Step 5: Add include_archived to search endpoints and archive-batch**

In `app.py`, add `include_archived: bool = False` to `SearchRequest` model. Pass it through to the engine's search methods.

Add archive-batch endpoint:

```python
class ArchiveBatchRequest(BaseModel):
    ids: List[int]


@app.post("/memory/archive-batch")
async def archive_batch(request_body: ArchiveBatchRequest, request: Request):
    """Archive multiple memories at once."""
    auth = _get_auth(request)
    archived = 0
    for mid in request_body.ids:
        try:
            memory.update_memory(mid, archived=True)
            _audit(request, "memory.archived", resource_id=str(mid))
            archived += 1
        except ValueError:
            continue
    return {"archived_count": archived}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_pin_archive.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add qdrant_store.py memory_engine.py app.py tests/test_pin_archive.py
git commit -m "feat: soft archive — exclude archived from search, archive-batch endpoint"
```

---

### Task 5: Pin exclusion in extraction AUDN

**Files:**
- Modify: `llm_extract.py:320-431` (execute_actions)
- Test: `tests/test_pin_archive.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pin_archive.py`:

```python
class TestPinExtractionProtection:
    """Test that pinned memories are protected from AUDN DELETE/UPDATE."""

    def test_pinned_excluded_from_audn_delete(self):
        """execute_actions should skip DELETE for pinned memories."""
        from unittest.mock import MagicMock
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.get_memory.return_value = {"id": 42, "text": "pinned", "source": "test/", "pinned": True}
        engine.add_memories.return_value = []

        actions = [{"action": "DELETE", "fact_index": 0, "old_id": 42}]
        facts = [{"text": "delete this", "category": "detail"}]

        result = execute_actions(engine, actions, facts, source="test/")
        engine.delete_memory.assert_not_called()

    def test_pinned_excluded_from_audn_update(self):
        """execute_actions should skip UPDATE for pinned memories."""
        from unittest.mock import MagicMock
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.get_memory.return_value = {"id": 42, "text": "pinned original", "source": "test/", "pinned": True}
        engine.add_memories.return_value = [99]

        actions = [{"action": "UPDATE", "fact_index": 0, "old_id": 42,
                    "new_text": "updated text"}]
        facts = [{"text": "updated text", "category": "decision"}]

        result = execute_actions(engine, actions, facts, source="test/")
        engine.delete_memory.assert_not_called()

    def test_unpinned_allows_audn_delete(self):
        """Non-pinned memories should still be deletable by AUDN."""
        from unittest.mock import MagicMock
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.get_memory.return_value = {"id": 42, "text": "not pinned", "source": "test/"}
        engine.add_memories.return_value = []

        actions = [{"action": "DELETE", "fact_index": 0, "old_id": 42}]
        facts = [{"text": "delete this", "category": "detail"}]

        execute_actions(engine, actions, facts, source="test/")
        engine.delete_memory.assert_called_once_with(42)

    def test_archived_excluded_from_audn_delete(self):
        """Archived memories should also be protected from AUDN DELETE."""
        from unittest.mock import MagicMock
        from llm_extract import execute_actions

        engine = MagicMock()
        engine.get_memory.return_value = {"id": 42, "text": "archived", "source": "test/", "archived": True}

        actions = [{"action": "DELETE", "fact_index": 0, "old_id": 42}]
        facts = [{"text": "delete this", "category": "detail"}]

        execute_actions(engine, actions, facts, source="test/")
        engine.delete_memory.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_pin_archive.py::TestPinExtractionProtection -v`
Expected: FAIL — pinned not checked in execute_actions

- [ ] **Step 3: Add pinned check to execute_actions**

In `llm_extract.py` `execute_actions()`, add pin/archive checks before DELETE (line 382) and UPDATE (line 359) actions. Use `engine.get_memory()` (the public method, not `_get_meta_by_id`) and check `old_id` (not `existing_id`):

For DELETE (around line 382, before `engine.delete_memory(old_id)`):

```python
              elif act == "DELETE":
                  old_id = action.get("old_id")
                  if old_id is not None:
                      existing = engine.get_memory(old_id)
                      if existing and (existing.get("pinned") or existing.get("archived")):
                          result_actions.append({"action": "skipped", "reason": "protected",
                                                "old_id": old_id})
                          continue
                      # ... existing auth check and delete logic follows
```

For UPDATE (around line 359, before `engine.delete_memory(old_id)`):

```python
              elif act == "UPDATE":
                  old_id = action.get("old_id")
                  if old_id is not None:
                      existing = engine.get_memory(old_id)
                      if existing and (existing.get("pinned") or existing.get("archived")):
                          result_actions.append({"action": "skipped", "reason": "protected",
                                                "old_id": old_id})
                          continue
                      # ... existing auth check and update logic follows
```

This covers both the spec's "pinned excluded from AUDN DELETE/UPDATE" (Item 12) and "archived excluded from extraction AUDN operations" (Item 13).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_pin_archive.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add llm_extract.py tests/test_pin_archive.py
git commit -m "feat: protect pinned memories from AUDN DELETE and UPDATE actions"
```

---

### Task 6: Merge API — engine method and endpoint

**Files:**
- Modify: `memory_engine.py` (add merge_memories method)
- Modify: `app.py` (add merge endpoint)
- Test: `tests/test_merge_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_merge_api.py`:

```python
"""Tests for memory merge API."""
import pytest


class TestMergeMemories:
    """Test POST /memory/merge endpoint."""

    def test_merge_two_memories(self, client):
        r1 = client.post("/memory/add", json={"text": "fact A", "source": "merge-test/"})
        r2 = client.post("/memory/add", json={"text": "fact B", "source": "merge-test/"})
        id1, id2 = r1.json()["id"], r2.json()["id"]

        resp = client.post("/memory/merge", json={
            "ids": [id1, id2],
            "merged_text": "Combined fact A and B",
            "source": "merge-test/",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert set(data["archived"]) == {id1, id2}

        # New memory exists
        new_resp = client.get(f"/memory/{data['id']}")
        assert new_resp.status_code == 200
        assert new_resp.json()["text"] == "Combined fact A and B"

        # Originals are archived
        orig1 = client.get(f"/memory/{id1}")
        assert orig1.json().get("archived") is True
        orig2 = client.get(f"/memory/{id2}")
        assert orig2.json().get("archived") is True

    def test_merge_creates_supersedes_links(self, client):
        r1 = client.post("/memory/add", json={"text": "link A", "source": "merge-link/"})
        r2 = client.post("/memory/add", json={"text": "link B", "source": "merge-link/"})
        id1, id2 = r1.json()["id"], r2.json()["id"]

        resp = client.post("/memory/merge", json={
            "ids": [id1, id2],
            "merged_text": "Merged",
            "source": "merge-link/",
        })
        new_id = resp.json()["id"]

        # Check links
        links_resp = client.get(f"/memory/{new_id}/links")
        links = links_resp.json()["links"]
        superseded_ids = [l["to_id"] for l in links if l["link_type"] == "supersedes"]
        assert id1 in superseded_ids
        assert id2 in superseded_ids

    def test_merge_requires_at_least_two(self, client):
        r1 = client.post("/memory/add", json={"text": "solo", "source": "merge-fail/"})
        resp = client.post("/memory/merge", json={
            "ids": [r1.json()["id"]],
            "merged_text": "Not enough",
            "source": "merge-fail/",
        })
        assert resp.status_code == 400

    def test_merge_with_pinned_original(self, client):
        """Pinned originals should still be archivable during merge."""
        r1 = client.post("/memory/add", json={"text": "pinned merge", "source": "merge-pin/"})
        r2 = client.post("/memory/add", json={"text": "not pinned", "source": "merge-pin/"})
        id1 = r1.json()["id"]
        client.patch(f"/memory/{id1}", json={"pinned": True})

        resp = client.post("/memory/merge", json={
            "ids": [id1, r2.json()["id"]],
            "merged_text": "Merged pinned",
            "source": "merge-pin/",
        })
        assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_merge_api.py -v`
Expected: FAIL — merge endpoint doesn't exist

- [ ] **Step 3: Add merge_memories to memory_engine.py**

Add after `add_link()` method:

```python
    def merge_memories(self, ids: List[int], merged_text: str, source: str) -> Dict[str, Any]:
        """Merge multiple memories into one. Archives originals, creates supersedes links."""
        if len(ids) < 2:
            raise ValueError("At least 2 memories required for merge")
        for mid in ids:
            if not self._id_exists(mid):
                raise ValueError(f"Memory {mid} not found")

        # Create new memory
        new_ids = self.add_memories([merged_text], [source])
        new_id = new_ids[0]

        # Link new → originals via supersedes
        for mid in ids:
            self.add_link(new_id, mid, "supersedes")

        # Archive originals (bypass pinned protection for explicit merge)
        for mid in ids:
            meta = self._get_meta_by_id(mid)
            meta["archived"] = True
            self.qdrant_store.set_payload(mid, {"archived": True})

        self.save()
        return {"id": new_id, "archived": ids}
```

- [ ] **Step 4: Add merge endpoint to app.py**

```python
class MergeRequest(BaseModel):
    ids: List[int] = Field(..., min_length=2)
    merged_text: str = Field(..., min_length=1, max_length=50000)
    source: str = Field(..., min_length=1, max_length=500)


@app.post("/memory/merge")
async def merge_memories(request_body: MergeRequest, request: Request):
    """Merge multiple memories into one, archiving originals."""
    auth = _get_auth(request)
    for mid in request_body.ids:
        existing = memory.get_memory(mid)
        _require_write(auth, existing.get("source", ""))
    try:
        result = memory.merge_memories(
            ids=request_body.ids,
            merged_text=request_body.merged_text,
            source=request_body.source,
        )
        _audit(request, "memory.merged", resource_id=str(result["id"]),
               source=request_body.source)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_merge_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add memory_engine.py app.py tests/test_merge_api.py
git commit -m "feat: merge memories API — create combined memory, archive originals, supersedes links"
```

---

### Task 7: CLI links command

**Files:**
- Create: `cli/commands/links.py`
- Modify: `cli/__init__.py:38-47` (add import)
- Test: `tests/test_cli_links.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_links.py`:

```python
"""Tests for CLI links commands."""
import json

import httpx
import pytest
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _invoke(args, handler):
    """Invoke CLI with mock transport — patches MemoriesClient.__init__."""
    original_init = MemoriesClient.__init__

    def patched_init(self, url=None, api_key=None, transport=None):
        original_init(self, url=url, api_key=api_key,
                      transport=httpx.MockTransport(handler))

    MemoriesClient.__init__ = patched_init
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["--json"] + args, catch_exceptions=False)
    finally:
        MemoriesClient.__init__ = original_init
    return result


class TestLinksCommands:

    def test_links_list(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "links": [
                    {"from_id": 1, "to_id": 2, "link_type": "related_to", "direction": "outgoing"},
                ]
            })
        result = _invoke(["links", "list", "1"], handler)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_links_add(self):
        def handler(request: httpx.Request):
            if request.method == "POST":
                return httpx.Response(200, json={"from_id": 1, "to_id": 2, "type": "related_to"})
            return httpx.Response(404)
        result = _invoke(["links", "add", "1", "2", "--type", "related_to"], handler)
        assert result.exit_code == 0

    def test_links_remove(self):
        def handler(request: httpx.Request):
            if request.method == "DELETE":
                return httpx.Response(200, json={"deleted": True})
            return httpx.Response(404)
        result = _invoke(["links", "remove", "1", "2", "--type", "related_to"], handler)
        assert result.exit_code == 0

    def test_links_list_empty(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={"links": []})
        result = _invoke(["links", "list", "1"], handler)
        assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_cli_links.py -v`
Expected: FAIL — `links` command not found

- [ ] **Step 3: Create cli/commands/links.py**

```python
"""CLI commands for memory links."""
import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group()
def links():
    """Manage links between memories."""


@links.command("list")
@click.argument("memory_id", type=int)
@pass_ctx
@handle_errors
def links_list(ctx, memory_id):
    """List all links for a memory."""
    data = ctx.client.get(f"/memory/{memory_id}/links")

    def human(d):
        items = d.get("links", [])
        if not items:
            click.echo("No links found.")
            return
        click.secho(f"{len(items)} link(s):\n", fg="yellow")
        for link in items:
            direction = link.get("direction", "outgoing")
            target = link["to_id"] if direction == "outgoing" else link["from_id"]
            lt = link.get("link_type", link.get("type", "unknown"))
            arrow = "→" if direction == "outgoing" else "←"
            click.echo(f"  {memory_id} {arrow} {target}  ({lt})")

    ctx.fmt.echo(data, human)


@links.command("add")
@click.argument("from_id", type=int)
@click.argument("to_id", type=int)
@click.option("--type", "link_type", default="related_to",
              type=click.Choice(["related_to", "reinforces", "supersedes", "blocked_by", "caused_by"]),
              help="Relationship type")
@pass_ctx
@handle_errors
def links_add(ctx, from_id, to_id, link_type):
    """Create a link between two memories."""
    data = ctx.client.post(f"/memory/{from_id}/link",
                          json={"to_id": to_id, "type": link_type})

    def human(d):
        click.secho(f"Link created: {from_id} →({link_type})→ {to_id}", fg="green")

    ctx.fmt.echo(data, human)


@links.command("remove")
@click.argument("from_id", type=int)
@click.argument("to_id", type=int)
@click.option("--type", "link_type", default="related_to",
              type=click.Choice(["related_to", "reinforces", "supersedes", "blocked_by", "caused_by"]),
              help="Relationship type")
@pass_ctx
@handle_errors
def links_remove(ctx, from_id, to_id, link_type):
    """Remove a link between two memories."""
    data = ctx.client.delete(
        f"/memory/{from_id}/link/{to_id}?type={link_type}"
    )

    def human(d):
        click.secho(f"Link removed: {from_id} ✕ {to_id} ({link_type})", fg="yellow")

    ctx.fmt.echo(data, human)
```

- [ ] **Step 4: Register links command in cli/__init__.py**

Add after the existing imports (line 47):

```python
from cli.commands import links  # noqa: E402, F401
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_cli_links.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add cli/commands/links.py cli/__init__.py tests/test_cli_links.py
git commit -m "feat: add CLI links command — list, add, remove memory links"
```

---

### Task 8: MCP auto-snapshot for delete tools

**Files:**
- Modify: `mcp-server/index.js`
- Test: manual verification (MCP tools are tested via integration)

- [ ] **Step 1: Update memory_delete_by_source tool**

In `mcp-server/index.js`, find the `memory_delete_by_source` tool handler. Before the delete request, add a snapshot call:

```javascript
// Auto-snapshot before bulk delete (no opt-out for agents)
await memoriesRequest("/snapshots", { method: "POST" });
```

- [ ] **Step 2: Update memory_delete_batch tool**

Same pattern — add snapshot call before the batch delete request.

- [ ] **Step 3: Update MCP version**

Change `version: "2.0.0"` to `version: "3.1.0"` (aligns with R1 release).

- [ ] **Step 4: Commit**

```bash
cd /Users/divyekant/Projects/memories
git add mcp-server/index.js
git commit -m "feat: MCP auto-snapshot before delete operations, bump version to 3.1.0"
```

---

### Task 9: Run full B1 test suite

**Files:**
- All test files from Tasks 1-7

- [ ] **Step 1: Run all B1 tests**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/test_snapshots.py tests/test_pin_archive.py tests/test_merge_api.py tests/test_cli_links.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run existing test suite for regressions**

Run: `cd /Users/divyekant/Projects/memories && python -m pytest tests/ -v --timeout=120`
Expected: No regressions — all existing tests still pass

- [ ] **Step 3: Commit any fixes if needed**

If regressions found, fix and commit with `fix: address B1 test regressions`.
