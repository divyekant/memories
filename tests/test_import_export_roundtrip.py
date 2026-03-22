"""Import/export round-trip validation: add → export → clear → import → verify."""

import json

import pytest

from memory_engine import MemoryEngine


@pytest.fixture
def engine(tmp_path):
    """Create a fresh MemoryEngine with a temp data dir."""
    return MemoryEngine(data_dir=str(tmp_path))


class TestImportExportRoundTrip:
    """Full lifecycle: add → export → clear → import(add) → verify."""

    def test_add_export_clear_import_verify(self, engine):
        """Memories survive a full export-then-import cycle."""
        # 1. Add memories
        engine.add_memories(
            texts=["Python uses indentation for scoping",
                   "Rust has zero-cost abstractions",
                   "PostgreSQL supports JSONB columns"],
            sources=["lang/python", "lang/rust", "db/postgres"],
        )
        assert engine.count_memories() == 3

        # 2. Export
        lines = engine.export_memories()
        header = json.loads(lines[0])
        assert header["_header"] is True
        assert header["count"] == 3
        assert len(lines) == 4  # header + 3 records

        # 3. Clear — delete all memories
        all_ids = [m["id"] for m in engine.metadata]
        engine.delete_memories(all_ids, skip_snapshot=True)
        assert engine.count_memories() == 0

        # 4. Import with strategy=add
        result = engine.import_memories(lines, strategy="add")
        assert result["imported"] == 3
        assert result["skipped"] == 0
        assert result["errors"] == []

        # 5. Verify all texts are back
        assert engine.count_memories() == 3
        texts = {m["text"] for m in engine.metadata}
        assert "Python uses indentation for scoping" in texts
        assert "Rust has zero-cost abstractions" in texts
        assert "PostgreSQL supports JSONB columns" in texts

    def test_roundtrip_preserves_sources(self, engine):
        """Sources survive the export/import cycle."""
        engine.add_memories(
            texts=["fact alpha", "fact beta"],
            sources=["src/alpha", "src/beta"],
        )

        lines = engine.export_memories()
        all_ids = [m["id"] for m in engine.metadata]
        engine.delete_memories(all_ids, skip_snapshot=True)

        engine.import_memories(lines, strategy="add")

        sources = {m["source"] for m in engine.metadata}
        assert "src/alpha" in sources
        assert "src/beta" in sources

    def test_roundtrip_preserves_custom_fields(self, engine):
        """Custom metadata fields survive the round-trip."""
        engine.add_memories(
            texts=["custom fact"],
            sources=["test/custom"],
            metadata_list=[{"priority": "high", "category": "ops"}],
        )

        lines = engine.export_memories()
        all_ids = [m["id"] for m in engine.metadata]
        engine.delete_memories(all_ids, skip_snapshot=True)

        engine.import_memories(lines, strategy="add")

        mem = engine.metadata[0]
        assert mem["text"] == "custom fact"
        assert mem["source"] == "test/custom"
        # Custom fields are stored in custom_fields during export and
        # should be re-applied on import
        custom = json.loads(lines[1]).get("custom_fields", {})
        assert custom.get("priority") == "high"
        assert custom.get("category") == "ops"
        # Verify custom fields survived the import (not just the export)
        assert mem.get("priority") == "high"
        assert mem.get("category") == "ops"


class TestSmartImportDedup:
    """Smart import skips exact duplicates."""

    def test_smart_skips_existing_memories(self, engine):
        """Importing already-existing memories with strategy=smart skips them."""
        engine.add_memories(
            texts=["The Earth orbits the Sun"],
            sources=["science/astro"],
        )

        lines = engine.export_memories()

        # Import the same data back — should skip the duplicate
        result = engine.import_memories(lines, strategy="smart")
        assert result["skipped"] >= 1
        assert result["imported"] == 0
        assert engine.count_memories() == 1

    def test_smart_adds_novel_memories(self, engine):
        """Smart import adds genuinely new memories."""
        engine.add_memories(
            texts=["The Earth orbits the Sun"],
            sources=["science/astro"],
        )

        # Export, then add a novel record to the export data
        lines = engine.export_memories()
        novel_record = json.dumps({
            "text": "Water freezes at zero degrees Celsius",
            "source": "science/chem",
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-01T00:00:00+00:00",
            "custom_fields": {},
        })
        # Update header count
        header = json.loads(lines[0])
        header["count"] = 2
        lines_with_novel = [json.dumps(header)] + lines[1:] + [novel_record]

        result = engine.import_memories(lines_with_novel, strategy="smart")
        assert result["imported"] == 1
        assert result["skipped"] >= 1
        assert engine.count_memories() == 2


class TestSourceFilteredExport:
    """Export with source prefix filter."""

    def test_source_filtered_export_only_matching(self, engine):
        """Export with source_prefix returns only matching memories."""
        engine.add_memories(
            texts=["alpha one", "beta one", "alpha two"],
            sources=["alpha/proj", "beta/proj", "alpha/other"],
        )

        lines = engine.export_memories(source_prefix="alpha/")
        header = json.loads(lines[0])
        assert header["count"] == 2
        assert header["source_filter"] == "alpha/"

        exported_texts = {json.loads(l)["text"] for l in lines[1:]}
        assert "alpha one" in exported_texts
        assert "alpha two" in exported_texts
        assert "beta one" not in exported_texts

    def test_source_filtered_export_import_roundtrip(self, engine):
        """Source-filtered export can be imported into a clean engine."""
        engine.add_memories(
            texts=["alpha fact", "beta fact"],
            sources=["alpha/proj", "beta/proj"],
        )

        # Export only alpha
        lines = engine.export_memories(source_prefix="alpha/")
        assert json.loads(lines[0])["count"] == 1

        # Clear everything
        all_ids = [m["id"] for m in engine.metadata]
        engine.delete_memories(all_ids, skip_snapshot=True)
        assert engine.count_memories() == 0

        # Import the filtered export
        result = engine.import_memories(lines, strategy="add")
        assert result["imported"] == 1
        assert engine.count_memories() == 1
        assert engine.metadata[0]["source"] == "alpha/proj"
