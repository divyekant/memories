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
        assert header["version"] == "2.0.0"
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
