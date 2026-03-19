"""Tests for export/import engine methods."""

import json
import tempfile

import pytest

from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    """Create a fresh MemoryEngine with a temp data dir."""
    return MemoryEngine(data_dir=str(tmp_path))


class TestExportMemories:
    def test_export_empty(self, engine):
        """Export from empty engine returns only header with count=0."""
        lines = engine.export_memories()
        assert len(lines) == 1
        header = json.loads(lines[0])
        assert header["_header"] is True
        assert header["count"] == 0
        assert header["version"] == "3.0.0"
        assert "exported_at" in header

    def test_export_all(self, engine):
        """Add 2 memories, export returns header + 2 lines, no id field in records."""
        engine.add_memories(
            texts=["first memory", "second memory"],
            sources=["src/a", "src/b"],
        )
        lines = engine.export_memories()
        assert len(lines) == 3  # header + 2 records

        header = json.loads(lines[0])
        assert header["_header"] is True
        assert header["count"] == 2

        for line in lines[1:]:
            record = json.loads(line)
            assert "id" not in record
            assert "text" in record
            assert "source" in record
            assert "created_at" in record
            assert "updated_at" in record
            assert "custom_fields" in record
            # timestamp is excluded (backward-compat alias)
            assert "timestamp" not in record

    def test_export_source_filter(self, engine):
        """Add memories with different sources, filter by prefix."""
        engine.add_memories(
            texts=["alpha fact", "beta fact", "alpha detail"],
            sources=["alpha/one", "beta/two", "alpha/three"],
        )
        lines = engine.export_memories(source_prefix="alpha/")
        header = json.loads(lines[0])
        assert header["count"] == 2
        assert header["source_filter"] == "alpha/"
        assert len(lines) == 3  # header + 2 matching records

        sources = [json.loads(l)["source"] for l in lines[1:]]
        assert all(s.startswith("alpha/") for s in sources)

    def test_export_since_until(self, engine):
        """Verify date filtering — at minimum, all export without filters works."""
        engine.add_memories(
            texts=["time fact one", "time fact two"],
            sources=["time/a", "time/b"],
        )

        # Export all (no date filter) should return both
        lines = engine.export_memories()
        header = json.loads(lines[0])
        assert header["count"] == 2

        # Export with a 'since' far in the future should return 0 records
        lines_future = engine.export_memories(since="2099-01-01T00:00:00+00:00")
        header_future = json.loads(lines_future[0])
        assert header_future["count"] == 0
        assert len(lines_future) == 1  # header only

        # Export with an 'until' far in the past should return 0 records
        lines_past = engine.export_memories(until="2000-01-01T00:00:00+00:00")
        header_past = json.loads(lines_past[0])
        assert header_past["count"] == 0
        assert len(lines_past) == 1  # header only


class TestImportMemories:
    def test_import_add_strategy(self, engine):
        """Create valid NDJSON lines, import, verify count == 2."""
        header = json.dumps({"_header": True, "count": 2, "version": "2.0.0"})
        rec1 = json.dumps({"text": "fact one", "source": "test/a"})
        rec2 = json.dumps({"text": "fact two", "source": "test/b"})
        lines = [header, rec1, rec2]

        result = engine.import_memories(lines, strategy="add")

        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["updated"] == 0
        assert result["errors"] == []
        assert result["backup"] is None  # no existing memories → no backup
        assert len(engine.metadata) == 2

    def test_import_validates_header(self, engine):
        """First line has no _header field → errors, nothing imported."""
        lines = [
            json.dumps({"not_a_header": True}),
            json.dumps({"text": "fact", "source": "src/a"}),
        ]

        result = engine.import_memories(lines, strategy="add")

        assert result["imported"] == 0
        assert len(result["errors"]) == 1
        assert "header" in result["errors"][0]["error"].lower()

    def test_import_skips_bad_lines(self, engine):
        """Mix of valid JSON, invalid JSON, valid record → imports 1, errors 1."""
        header = json.dumps({"_header": True, "count": 2, "version": "2.0.0"})
        bad_line = "NOT VALID JSON {{{}"
        good_rec = json.dumps({"text": "good fact", "source": "src/ok"})
        lines = [header, bad_line, good_rec]

        result = engine.import_memories(lines, strategy="add")

        assert result["imported"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["line"] == 2  # 1-indexed, line 2 is the bad one

    def test_import_source_remap(self, engine):
        """Import with source_remap=("old/", "new/") → source is remapped."""
        header = json.dumps({"_header": True, "count": 1, "version": "2.0.0"})
        rec = json.dumps({"text": "remapped fact", "source": "old/path"})
        lines = [header, rec]

        result = engine.import_memories(lines, strategy="add", source_remap=("old/", "new/"))

        assert result["imported"] == 1
        assert engine.metadata[0]["source"] == "new/path"

    def test_import_creates_backup_when_existing(self, engine):
        """When memories already exist and create_backup=True, backup is created."""
        engine.add_memories(texts=["existing"], sources=["pre/exist"])

        header = json.dumps({"_header": True, "count": 1, "version": "2.0.0"})
        rec = json.dumps({"text": "new fact", "source": "imp/a"})
        lines = [header, rec]

        result = engine.import_memories(lines, strategy="add")

        assert result["backup"] is not None
        assert "pre-import" in result["backup"]
        assert len(engine.metadata) == 2  # 1 existing + 1 imported

    def test_import_skips_records_missing_fields(self, engine):
        """Records missing text or source are skipped with errors."""
        header = json.dumps({"_header": True, "count": 2, "version": "2.0.0"})
        no_text = json.dumps({"source": "src/a"})
        no_source = json.dumps({"text": "no source fact"})
        lines = [header, no_text, no_source]

        result = engine.import_memories(lines, strategy="add")

        assert result["imported"] == 0
        assert len(result["errors"]) == 2


class TestImportSmart:
    def test_smart_skips_exact_duplicates(self, engine):
        """Importing the exact same text should be skipped."""
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
        assert result["skipped"] >= 1
        assert result["imported"] == 0
        assert engine.count_memories() == 1  # no duplicate added

    def test_smart_adds_novel(self, engine):
        """Clearly different text should be added."""
        engine.add_memories(texts=["The sky is blue"], sources=["s/"])
        lines = [
            json.dumps({"_header": True, "count": 1, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "Python uses indentation for blocks and scoping",
                         "source": "s/",
                         "created_at": "2026-02-01T00:00:00Z",
                         "updated_at": "2026-02-01T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="smart")
        assert result["imported"] == 1
        assert engine.count_memories() == 2

    def test_smart_into_empty_engine(self, engine):
        """Importing into empty engine adds everything."""
        lines = [
            json.dumps({"_header": True, "count": 2, "version": "2.0.0",
                         "exported_at": "2026-01-01T00:00:00Z",
                         "source_filter": None, "since": None, "until": None}),
            json.dumps({"text": "fact one", "source": "s/",
                         "created_at": "2026-01-01T00:00:00Z",
                         "updated_at": "2026-01-01T00:00:00Z"}),
            json.dumps({"text": "fact two", "source": "s/",
                         "created_at": "2026-01-02T00:00:00Z",
                         "updated_at": "2026-01-02T00:00:00Z"}),
        ]
        result = engine.import_memories(lines, strategy="smart")
        assert result["imported"] == 2
        assert engine.count_memories() == 2
