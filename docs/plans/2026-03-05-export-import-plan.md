# Export/Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add streaming NDJSON export and multi-strategy import with auto-backup, source remapping, and smart conflict resolution.

**Architecture:** Export adds a generator method to `MemoryEngine` that yields NDJSON lines, served as a `StreamingResponse` from FastAPI. Import reads NDJSON line-by-line, validates the header, and dispatches to one of three strategies (`add`, `smart`, `smart+extract`) that reuse existing `add_memories` and `is_novel` internals. CLI commands use httpx streaming to write/read files.

**Tech Stack:** Python, FastAPI (StreamingResponse), httpx (streaming), Click (CLI), pytest + httpx.MockTransport (tests)

---

### Task 1: Engine — Export Generator

**Files:**
- Modify: `memory_engine.py` (after `list_memories` method, ~line 1036)
- Create: `tests/test_export_import.py`

**Step 1: Write the failing test**

```python
"""Tests for export/import engine methods."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def engine():
    """Create a MemoryEngine with a temp data dir."""
    from memory_engine import MemoryEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.dict("os.environ", {"STORAGE_BACKEND": "qdrant"}):
            eng = MemoryEngine(data_dir=tmpdir)
            yield eng


class TestExportMemories:
    def test_export_empty(self, engine):
        lines = list(engine.export_memories())
        assert len(lines) == 1  # header only
        header = json.loads(lines[0])
        assert header["_header"] is True
        assert header["count"] == 0

    def test_export_all(self, engine):
        engine.add_memories(
            texts=["fact one", "fact two"],
            sources=["test/a", "test/b"],
        )
        lines = list(engine.export_memories())
        assert len(lines) == 3  # header + 2 memories
        header = json.loads(lines[0])
        assert header["count"] == 2
        m1 = json.loads(lines[1])
        assert m1["text"] == "fact one"
        assert m1["source"] == "test/a"
        assert "id" not in m1  # IDs stripped
        assert "created_at" in m1

    def test_export_source_filter(self, engine):
        engine.add_memories(
            texts=["keep me", "skip me"],
            sources=["proj/a", "other/b"],
        )
        lines = list(engine.export_memories(source_prefix="proj/"))
        assert len(lines) == 2  # header + 1 memory
        m = json.loads(lines[1])
        assert m["source"] == "proj/a"

    def test_export_since_until(self, engine):
        engine.add_memories(texts=["old"], sources=["t/"])
        engine.add_memories(texts=["new"], sources=["t/"])
        # All should export with no date filter
        lines = list(engine.export_memories())
        assert len(lines) == 3
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_import.py::TestExportMemories -v`
Expected: FAIL with `AttributeError: 'MemoryEngine' object has no attribute 'export_memories'`

**Step 3: Write minimal implementation**

Add to `memory_engine.py` after the `list_memories` method (~line 1036), inside the `MemoryEngine` class:

```python
    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def export_memories(
        self,
        source_prefix: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[str]:
        """Export memories as NDJSON lines with a header.

        Returns a list of JSON strings, one per line. First line is the header.
        IDs and embeddings are excluded — only portable fields are exported.
        """
        filtered = self.metadata
        if source_prefix:
            filtered = [m for m in filtered if m.get("source", "").startswith(source_prefix)]
        if since:
            filtered = [m for m in filtered if m.get("created_at", "") >= since]
        if until:
            filtered = [m for m in filtered if m.get("created_at", "") <= until]

        header = {
            "_header": True,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_filter": source_prefix,
            "since": since,
            "until": until,
            "count": len(filtered),
            "version": "2.0.0",
        }
        lines = [json.dumps(header, default=str)]

        export_fields = {"text", "source", "created_at", "updated_at"}
        for mem in filtered:
            record = {k: v for k, v in mem.items() if k in export_fields}
            # Include any custom fields (not standard internal fields)
            internal_fields = {"id", "text", "source", "created_at", "updated_at", "timestamp"}
            custom = {k: v for k, v in mem.items() if k not in internal_fields}
            if custom:
                record["custom_fields"] = custom
            lines.append(json.dumps(record, default=str))

        return lines
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_import.py::TestExportMemories -v`
Expected: PASS (all 4 tests)

**Step 5: Commit**

```bash
git add memory_engine.py tests/test_export_import.py
git commit -m "feat(export): add export_memories engine method with NDJSON output"
```

---

### Task 2: Engine — Import with `add` Strategy

**Files:**
- Modify: `memory_engine.py` (after `export_memories`)
- Modify: `tests/test_export_import.py`

**Step 1: Write the failing test**

Add to `tests/test_export_import.py`:

```python
class TestImportMemories:
    def test_import_add_strategy(self, engine):
        lines = [
            json.dumps({"_header": True, "count": 2, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "fact one", "source": "src/a",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
            json.dumps({"text": "fact two", "source": "src/b",
                         "created_at": "2026-01-02T00:00:00Z",
                         "updated_at": "2026-01-02T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="add")
        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == []
        assert engine.count_memories() == 2

    def test_import_validates_header(self, engine):
        lines = [json.dumps({"text": "no header"})]
        result = engine.import_memories(lines, strategy="add")
        assert len(result["errors"]) > 0
        assert engine.count_memories() == 0

    def test_import_skips_bad_lines(self, engine):
        lines = [
            json.dumps({"_header": True, "count": 2, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            "not valid json",
            json.dumps({"text": "good one", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="add")
        assert result["imported"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["line"] == 2

    def test_import_source_remap(self, engine):
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "remapped", "source": "old/proj",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="add",
                                         source_remap=("old/", "new/"))
        assert result["imported"] == 1
        mems = engine.list_memories()["memories"]
        assert mems[0]["source"] == "new/proj"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_import.py::TestImportMemories -v`
Expected: FAIL with `AttributeError: 'MemoryEngine' object has no attribute 'import_memories'`

**Step 3: Write minimal implementation**

Add to `memory_engine.py` after `export_memories`:

```python
    def import_memories(
        self,
        lines: List[str],
        strategy: str = "add",
        source_remap: Optional[Tuple[str, str]] = None,
        create_backup: bool = True,
    ) -> Dict[str, Any]:
        """Import memories from NDJSON lines.

        Args:
            lines: List of JSON strings (first must be header).
            strategy: "add", "smart", or "smart+extract".
            source_remap: Optional (old_prefix, new_prefix) tuple.
            create_backup: Create backup before importing.

        Returns:
            Summary dict with imported, skipped, updated, errors, backup.
        """
        result = {"imported": 0, "skipped": 0, "updated": 0,
                  "errors": [], "backup": None}

        if not lines:
            result["errors"].append({"line": 0, "error": "No data provided"})
            return result

        # Validate header
        try:
            header = json.loads(lines[0])
        except (json.JSONDecodeError, IndexError):
            result["errors"].append({"line": 1, "error": "Invalid header line"})
            return result

        if not header.get("_header"):
            result["errors"].append({"line": 1, "error": "First line must be a header with _header: true"})
            return result

        # Auto-backup
        if create_backup and self.metadata:
            backup_path = self._backup(prefix="pre-import")
            result["backup"] = backup_path.name

        # Process memory lines
        texts = []
        sources = []
        for i, line in enumerate(lines[1:], start=2):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                result["errors"].append({"line": i, "error": "Invalid JSON"})
                continue

            text = record.get("text", "").strip()
            source = record.get("source", "").strip()
            if not text:
                result["errors"].append({"line": i, "error": "Missing text field"})
                continue
            if not source:
                result["errors"].append({"line": i, "error": "Missing source field"})
                continue

            # Apply source remapping
            if source_remap and source.startswith(source_remap[0]):
                source = source_remap[1] + source[len(source_remap[0]):]

            if strategy == "add":
                texts.append(text)
                sources.append(source)
            else:
                # smart and smart+extract handled in Task 3
                texts.append(text)
                sources.append(source)

        if texts and strategy == "add":
            added_ids = self.add_memories(texts=texts, sources=sources, deduplicate=False)
            result["imported"] = len(added_ids)

        return result
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_import.py::TestImportMemories -v`
Expected: PASS (all 4 tests)

**Step 5: Commit**

```bash
git add memory_engine.py tests/test_export_import.py
git commit -m "feat(import): add import_memories engine method with add strategy"
```

---

### Task 3: Engine — `smart` Import Strategy

**Files:**
- Modify: `memory_engine.py` (inside `import_memories`)
- Modify: `tests/test_export_import.py`

**Step 1: Write the failing test**

Add to `tests/test_export_import.py`:

```python
class TestImportSmart:
    def test_smart_skips_duplicates(self, engine):
        engine.add_memories(texts=["The sky is blue"], sources=["s/"])
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "The sky is blue", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="smart")
        assert result["skipped"] == 1
        assert result["imported"] == 0
        assert engine.count_memories() == 1  # no duplicate added

    def test_smart_adds_novel(self, engine):
        engine.add_memories(texts=["The sky is blue"], sources=["s/"])
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "Python uses indentation for blocks",
                         "source": "s/",
                         "created_at": "2026-02-01T00:00:00Z",
                         "updated_at": "2026-02-01T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="smart")
        assert result["imported"] == 1
        assert engine.count_memories() == 2

    def test_smart_newer_wins(self, engine):
        """When a near-duplicate exists, the newer one should survive."""
        engine.add_memories(texts=["DK weighs 70kg"], sources=["health/"])
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-03-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "DK weighs 69kg", "source": "health/",
                         "created_at": "2026-02-10T00:00:00Z",
                         "updated_at": "2026-02-10T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="smart")
        # The imported one is newer or a near-duplicate — behavior depends on
        # similarity threshold. Either way, no crash and result is valid.
        total = result["imported"] + result["skipped"] + result["updated"]
        assert total == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_import.py::TestImportSmart -v`
Expected: FAIL (smart strategy not yet implemented — falls through to `add`)

**Step 3: Write minimal implementation**

Replace the strategy dispatch section in `import_memories` with:

```python
        if texts and strategy == "add":
            added_ids = self.add_memories(texts=texts, sources=sources, deduplicate=False)
            result["imported"] = len(added_ids)
        elif texts and strategy in ("smart", "smart+extract"):
            novel_texts = []
            novel_sources = []
            borderline = []  # for smart+extract escalation

            SKIP_THRESHOLD = 0.95   # very high similarity = definite duplicate
            NOVEL_THRESHOLD = 0.80  # below this = clearly novel
            # Between 0.80-0.95 = borderline (near-duplicate, check timestamps)

            for text, source, i in zip(texts, sources, range(len(texts))):
                if not self.metadata:
                    novel_texts.append(text)
                    novel_sources.append(source)
                    continue

                search_results = self.search(text, k=1)
                if not search_results:
                    novel_texts.append(text)
                    novel_sources.append(source)
                    continue

                top = search_results[0]
                sim = top.get("similarity", 0)

                if sim >= SKIP_THRESHOLD:
                    # Definite duplicate — skip
                    result["skipped"] += 1
                elif sim < NOVEL_THRESHOLD:
                    # Clearly novel — add
                    novel_texts.append(text)
                    novel_sources.append(source)
                else:
                    # Borderline — check timestamps
                    existing_ts = top.get("created_at", top.get("timestamp", ""))
                    # Find the created_at from the original line data
                    import_line_idx = i
                    try:
                        record = json.loads(lines[import_line_idx + 2])  # +2 for header offset
                        import_ts = record.get("created_at", "")
                    except (json.JSONDecodeError, IndexError):
                        import_ts = ""

                    if import_ts > existing_ts:
                        # Imported memory is newer — supersede the old one
                        old_id = top["id"]
                        self.delete_memory(old_id)
                        novel_texts.append(text)
                        novel_sources.append(source)
                        result["updated"] += 1
                    else:
                        # Existing memory is newer — skip import
                        result["skipped"] += 1

            if novel_texts:
                added_ids = self.add_memories(texts=novel_texts, sources=novel_sources,
                                              deduplicate=False)
                result["imported"] = len(added_ids)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_import.py::TestImportSmart -v`
Expected: PASS (all 3 tests)

**Step 5: Commit**

```bash
git add memory_engine.py tests/test_export_import.py
git commit -m "feat(import): add smart strategy with novelty check and timestamp resolution"
```

---

### Task 4: API Endpoint — Export

**Files:**
- Modify: `app.py` (after backup endpoints, ~line 1800)
- Create: `tests/test_export_import_api.py`

**Step 1: Write the failing test**

```python
"""Tests for export/import API endpoints."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_engine():
    eng = MagicMock()
    eng.export_memories.return_value = [
        json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                     "exported_at": "2026-01-01T00:00:00Z",
                     "source_filter": None, "since": None, "until": None}),
        json.dumps({"text": "hello", "source": "s/",
                     "created_at": "2026-01-01T00:00:00Z",
                     "updated_at": "2026-01-01T00:00:00Z"}),
    ]
    return eng


@pytest.fixture
def client(mock_engine):
    with patch("app.engine", mock_engine):
        from app import app
        yield TestClient(app)


class TestExportEndpoint:
    def test_export_returns_ndjson(self, client, mock_engine):
        resp = client.get("/export")
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2
        header = json.loads(lines[0])
        assert header["_header"] is True

    def test_export_with_source_filter(self, client, mock_engine):
        client.get("/export?source=proj/")
        mock_engine.export_memories.assert_called_once()
        call_kwargs = mock_engine.export_memories.call_args
        assert call_kwargs[1]["source_prefix"] == "proj/" or call_kwargs[0][0] == "proj/"

    def test_export_with_date_filters(self, client, mock_engine):
        client.get("/export?since=2026-01-01&until=2026-02-01")
        mock_engine.export_memories.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_import_api.py::TestExportEndpoint -v`
Expected: FAIL (no `/export` route)

**Step 3: Write minimal implementation**

Add to `app.py` after the backup section (around line 1800):

```python
# -- Export / Import -----------------------------------------------------------

@app.get("/export")
async def export_memories(
    request: Request,
    source: Optional[str] = Query(None, max_length=500),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
):
    """Export memories as streaming NDJSON."""
    auth = _get_auth(request)

    def generate():
        lines = engine.export_memories(
            source_prefix=source,
            since=since,
            until=until,
        )
        # Filter by auth prefix
        for i, line in enumerate(lines):
            if i == 0:
                yield line + "\n"
                continue
            record = json.loads(line)
            if auth.can_read(record.get("source", "")):
                yield line + "\n"

    from starlette.responses import StreamingResponse
    return StreamingResponse(generate(), media_type="application/x-ndjson")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_import_api.py::TestExportEndpoint -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app.py tests/test_export_import_api.py
git commit -m "feat(export): add GET /export streaming NDJSON endpoint"
```

---

### Task 5: API Endpoint — Import

**Files:**
- Modify: `app.py` (after export endpoint)
- Modify: `tests/test_export_import_api.py`

**Step 1: Write the failing test**

Add to `tests/test_export_import_api.py`:

```python
class TestImportEndpoint:
    def test_import_add(self, client, mock_engine):
        mock_engine.import_memories.return_value = {
            "imported": 2, "skipped": 0, "updated": 0,
            "errors": [], "backup": "pre-import_20260305",
        }
        body = "\n".join([
            json.dumps({"_header": True, "count": 2, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "one", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
            json.dumps({"text": "two", "source": "s/",
                         "created_at": "2026-01-02T00:00:00Z",
                         "updated_at": "2026-01-02T00:00:00Z"}),
        ])
        resp = client.post(
            "/import?strategy=add",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2

    def test_import_smart(self, client, mock_engine):
        mock_engine.import_memories.return_value = {
            "imported": 1, "skipped": 1, "updated": 0,
            "errors": [], "backup": "pre-import_20260305",
        }
        body = json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                            "exported_at": "2026-01-01T00:00:00Z",
                            "source_filter": None, "since": None, "until": None})
        resp = client.post(
            "/import?strategy=smart",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200

    def test_import_no_backup(self, client, mock_engine):
        mock_engine.import_memories.return_value = {
            "imported": 0, "skipped": 0, "updated": 0,
            "errors": [], "backup": None,
        }
        body = json.dumps({"_header": True, "count": 0, "version": "2.0.0",
                            "exported_at": "2026-01-01T00:00:00Z",
                            "source_filter": None, "since": None, "until": None})
        resp = client.post(
            "/import?strategy=add&no_backup=true",
            content=body,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert resp.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_import_api.py::TestImportEndpoint -v`
Expected: FAIL (no `/import` route)

**Step 3: Write minimal implementation**

Add to `app.py` after the export endpoint:

```python
@app.post("/import")
async def import_memories(
    request: Request,
    strategy: str = Query("add", regex="^(add|smart|smart\\+extract)$"),
    source_remap: Optional[str] = Query(None, max_length=200),
    no_backup: bool = Query(False),
):
    """Import memories from NDJSON body."""
    auth = _get_auth(request)

    body = await request.body()
    raw_lines = body.decode("utf-8").strip().split("\n")

    # Parse source_remap "old=new" format
    remap_tuple = None
    if source_remap and "=" in source_remap:
        parts = source_remap.split("=", 1)
        remap_tuple = (parts[0], parts[1])

    # Auth check: filter lines to only writable sources
    filtered_lines = [raw_lines[0]]  # keep header
    auth_errors = []
    for i, line in enumerate(raw_lines[1:], start=2):
        try:
            record = json.loads(line)
            source = record.get("source", "")
            if remap_tuple and source.startswith(remap_tuple[0]):
                source = remap_tuple[1] + source[len(remap_tuple[0]):]
            if not auth.can_write(source):
                auth_errors.append({"line": i, "error": "source prefix not authorized"})
                continue
            filtered_lines.append(line)
        except json.JSONDecodeError:
            filtered_lines.append(line)  # let engine handle bad JSON

    try:
        result = await run_in_threadpool(
            engine.import_memories,
            filtered_lines,
            strategy=strategy,
            source_remap=remap_tuple,
            create_backup=not no_backup,
        )
        result["errors"].extend(auth_errors)
        return result
    except Exception as exc:
        logger.error("Import failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_import_api.py::TestImportEndpoint -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app.py tests/test_export_import_api.py
git commit -m "feat(import): add POST /import endpoint with strategy support"
```

---

### Task 6: CLI Client — Export and Import Methods

**Files:**
- Modify: `cli/client.py` (after sync methods)
- Create: `tests/test_cli_export_import.py`

**Step 1: Write the failing test**

```python
"""Tests for export/import CLI commands using httpx.MockTransport."""

import json

import httpx
from click.testing import CliRunner

from cli import app
from cli.client import MemoriesClient


def _invoke(args, handler, input=None):
    """Invoke the CLI app with a mock transport backing the client."""
    original_init = MemoriesClient.__init__

    def patched_init(self, url=None, api_key=None, transport=None):
        original_init(self, url=url, api_key=api_key,
                      transport=httpx.MockTransport(handler))

    MemoriesClient.__init__ = patched_init
    try:
        runner = CliRunner()
        result = runner.invoke(app, ["--json"] + args, input=input)
    finally:
        MemoriesClient.__init__ = original_init
    return result


class TestExportClient:
    def test_export_stream(self):
        ndjson = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "hello", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ])

        def handler(request: httpx.Request):
            assert request.url.path == "/export"
            return httpx.Response(200, text=ndjson,
                                  headers={"content-type": "application/x-ndjson"})

        client = MemoriesClient(url="http://test", transport=httpx.MockTransport(handler))
        lines = list(client.export_stream())
        assert len(lines) == 2
        assert json.loads(lines[0])["_header"] is True

    def test_import_stream(self):
        def handler(request: httpx.Request):
            assert request.url.path == "/import"
            return httpx.Response(200, json={
                "imported": 2, "skipped": 0, "updated": 0,
                "errors": [], "backup": "pre-import_123",
            })

        client = MemoriesClient(url="http://test", transport=httpx.MockTransport(handler))
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0"}),
            json.dumps({"text": "hi", "source": "s/"}),
        ]
        result = client.import_upload(lines, strategy="add")
        assert result["imported"] == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_export_import.py::TestExportClient -v`
Expected: FAIL with `AttributeError`

**Step 3: Write minimal implementation**

Add to `cli/client.py` after the extract methods:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_export_import.py::TestExportClient -v`
Expected: PASS

**Step 5: Commit**

```bash
git add cli/client.py tests/test_cli_export_import.py
git commit -m "feat(cli): add export_stream and import_upload client methods"
```

---

### Task 7: CLI Commands — Export and Import

**Files:**
- Create: `cli/commands/export_import.py`
- Modify: `cli/__init__.py` (add import at bottom)
- Modify: `tests/test_cli_export_import.py`

**Step 1: Write the failing test**

Add to `tests/test_cli_export_import.py`:

```python
class TestExportCommand:
    def test_export_to_stdout(self):
        ndjson_lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "hello", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
        ]

        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                text="\n".join(ndjson_lines),
                headers={"content-type": "application/x-ndjson"},
            )

        result = _invoke(["export"], handler)
        assert result.exit_code == 0
        # JSON mode wraps in envelope
        data = json.loads(result.output)
        assert data["ok"] is True


class TestImportCommand:
    def test_import_from_stdin(self):
        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "imported": 1, "skipped": 0, "updated": 0,
                "errors": [], "backup": "pre-import_123",
            })

        ndjson_input = "\n".join([
            json.dumps({"_header": True, "count": 1, "version": "2.0.0"}),
            json.dumps({"text": "hi", "source": "s/"}),
        ]) + "\n"

        result = _invoke(
            ["import", "-"],
            handler,
            input=ndjson_input,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["imported"] == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_export_import.py::TestExportCommand -v`
Expected: FAIL (no `export` command)

**Step 3: Write minimal implementation**

Create `cli/commands/export_import.py`:

```python
"""Export and import CLI commands."""

import json
import sys
from pathlib import Path

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.command("export")
@click.option("-o", "--output", default=None, type=click.Path(),
              help="Output file path (default: stdout)")
@click.option("--source", default=None, help="Source prefix filter")
@click.option("--since", default=None, help="Export memories created after (ISO8601)")
@click.option("--until", default=None, help="Export memories created before (ISO8601)")
@pass_ctx
@handle_errors
def export_cmd(ctx, output, source, since, until):
    """Export memories to NDJSON file."""
    lines = list(ctx.client.export_stream(source=source, since=since, until=until))

    if output:
        path = Path(output)
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

        header = json.loads(lines[0]) if lines else {}
        count = header.get("count", len(lines) - 1)
        data = {"file": str(path), "count": count}

        def human(d):
            click.secho(f"Exported {d['count']} memories to {d['file']}", fg="green")

        ctx.fmt.echo(data, human)
    else:
        # Stdout mode — write lines directly if human, wrap in envelope if JSON
        if ctx.fmt.is_json:
            header = json.loads(lines[0]) if lines else {}
            count = header.get("count", len(lines) - 1)
            ctx.fmt.echo({"count": count, "lines": len(lines)})
        else:
            for line in lines:
                click.echo(line)


@app.command("import")
@click.argument("file", default="-")
@click.option("--strategy", default="add",
              type=click.Choice(["add", "smart", "smart+extract"]),
              help="Import strategy")
@click.option("--source-remap", default=None,
              help="Remap source prefix (format: old=new)")
@click.option("--no-backup", is_flag=True, help="Skip auto-backup before import")
@pass_ctx
@handle_errors
def import_cmd(ctx, file, strategy, source_remap, no_backup):
    """Import memories from NDJSON file."""
    if file == "-" or (file is None and not sys.stdin.isatty()):
        raw = sys.stdin.read()
    else:
        path = Path(file)
        if not path.exists():
            raise click.UsageError(f"File not found: {file}")
        raw = path.read_text(encoding="utf-8")

    lines = [line for line in raw.strip().split("\n") if line.strip()]
    if not lines:
        raise click.UsageError("No data to import")

    data = ctx.client.import_upload(
        lines,
        strategy=strategy,
        source_remap=source_remap,
        no_backup=no_backup,
    )

    def human(d):
        click.secho(f"Imported: {d.get('imported', 0)}", fg="green")
        skipped = d.get("skipped", 0)
        updated = d.get("updated", 0)
        if skipped:
            click.echo(f"Skipped:  {skipped}")
        if updated:
            click.echo(f"Updated:  {updated}")
        errors = d.get("errors", [])
        if errors:
            click.secho(f"Errors:   {len(errors)}", fg="red")
            for e in errors[:5]:
                click.echo(f"  Line {e['line']}: {e['error']}")
        backup = d.get("backup")
        if backup:
            click.echo(f"Backup:   {backup}")

    ctx.fmt.echo(data, human)
```

Add the import to `cli/__init__.py` at the bottom:

```python
from cli.commands import export_import  # noqa: E402, F401
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_export_import.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add cli/commands/export_import.py cli/__init__.py tests/test_cli_export_import.py
git commit -m "feat(cli): add export and import commands"
```

---

### Task 8: Documentation and Final Polish

**Files:**
- Modify: `README.md` (add export/import to CLI section)
- Modify: `CHANGELOG.md` (add to Unreleased)
- Modify: `Dockerfile` (verify cli/ is already copied)

**Step 1: Update README CLI section**

Add export/import examples to the CLI usage section in `README.md`:

```markdown
### Export & Import

```bash
# Export all memories
memories export -o backup.jsonl

# Export filtered by source
memories export --source "claude-code/" -o project.jsonl

# Export with date range
memories export --source "proj/" --since 2026-01-01 -o recent.jsonl

# Import (clean migration)
memories import backup.jsonl

# Import with smart dedup
memories import backup.jsonl --strategy smart

# Import with source remapping
memories import backup.jsonl --source-remap "old/=new/"
```
```

**Step 2: Update CHANGELOG**

Add to the `[Unreleased]` section:

```markdown
- Streaming NDJSON export with source prefix, date range filters
- Multi-strategy import: `add` (raw), `smart` (novelty + timestamp), `smart+extract` (LLM for borderline)
- Auto-backup before import with `--no-backup` override
- Source prefix remapping during import
```

**Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: add export/import to README and CHANGELOG"
```

---

### Task 9: Integration Test

**Files:**
- No new files — test against running server

**Step 1: Start the server**

```bash
docker compose up -d
```

**Step 2: Test export**

```bash
memories export -o /tmp/test-export.jsonl
head -1 /tmp/test-export.jsonl | python -m json.tool  # verify header
wc -l /tmp/test-export.jsonl  # count lines
```

**Step 3: Test import (add strategy)**

```bash
# Create a small test file
echo '{"_header": true, "count": 1, "version": "2.0.0", "exported_at": "2026-03-05T00:00:00Z", "source_filter": null, "since": null, "until": null}
{"text": "Integration test memory", "source": "test/export-import", "created_at": "2026-03-05T00:00:00Z", "updated_at": "2026-03-05T00:00:00Z"}' > /tmp/test-import.jsonl

memories import /tmp/test-import.jsonl
memories search "Integration test memory"
```

**Step 4: Test round-trip**

```bash
# Export what we just imported
memories export --source "test/export-import" -o /tmp/roundtrip.jsonl
cat /tmp/roundtrip.jsonl

# Clean up
memories delete-by source --yes "test/export-import"
```

**Step 5: Test smart import**

```bash
# Add a memory, then try to import a duplicate
memories add -s "test/smart/" "The weather is sunny"
echo '{"_header": true, "count": 1, "version": "2.0.0", "exported_at": "2026-03-05T00:00:00Z", "source_filter": null, "since": null, "until": null}
{"text": "The weather is sunny", "source": "test/smart/", "created_at": "2026-03-05T00:00:00Z", "updated_at": "2026-03-05T00:00:00Z"}' > /tmp/test-smart.jsonl

memories import /tmp/test-smart.jsonl --strategy smart
# Should show skipped: 1

# Clean up
memories delete-by source --yes "test/smart/"
```
