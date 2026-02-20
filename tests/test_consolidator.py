"""Tests for consolidator module."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

from llm_provider import CompletionResult


def _cr(text, input_tokens=10, output_tokens=5):
    """Helper to build CompletionResult from text."""
    return CompletionResult(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


def _make_memory(id, text, source="project/decisions", category="DETAIL",
                 created_at=None):
    """Build a metadata dict resembling a real memory."""
    ts = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "id": id,
        "text": text,
        "source": source,
        "category": category,
        "created_at": ts,
        "updated_at": ts,
        "timestamp": ts,
    }


class TestClusterDetection:
    """Tests for find_clusters()."""

    def test_finds_clusters_by_similarity(self):
        from consolidator import find_clusters

        m0 = _make_memory(0, "We use Postgres for the database")
        m1 = _make_memory(1, "Database is Postgres with pgvector extension")
        m2 = _make_memory(2, "Postgres chosen for relational data storage")
        m3 = _make_memory(3, "Frontend uses React with TypeScript")

        engine = MagicMock()
        engine.metadata = [m0, m1, m2, m3]

        # hybrid_search returns similar memories for each query
        def fake_search(query, k=10, source_prefix=None):
            if "Postgres" in query or "database" in query.lower():
                results = []
                for m in [m0, m1, m2]:
                    if m["text"] != query:
                        results.append({**m, "rrf_score": 0.85})
                return results
            return []

        engine.hybrid_search.side_effect = fake_search

        clusters = find_clusters(engine, similarity_threshold=0.75, min_cluster_size=2)

        assert len(clusters) >= 1
        # The biggest cluster should have the Postgres memories
        biggest = max(clusters, key=len)
        cluster_ids = {m["id"] for m in biggest}
        assert {0, 1, 2}.issubset(cluster_ids)

    def test_no_clusters_when_all_unique(self):
        from consolidator import find_clusters

        m0 = _make_memory(0, "We use Postgres")
        m1 = _make_memory(1, "Frontend uses React")

        engine = MagicMock()
        engine.metadata = [m0, m1]

        # hybrid_search returns nothing similar
        engine.hybrid_search.return_value = []

        clusters = find_clusters(engine, similarity_threshold=0.75, min_cluster_size=2)
        assert clusters == []

    def test_respects_source_prefix_filter(self):
        from consolidator import find_clusters

        m0 = _make_memory(0, "Use Postgres", source="project/db")
        m1 = _make_memory(1, "Postgres is great", source="project/db")
        m2 = _make_memory(2, "Use Postgres too", source="other/stuff")

        engine = MagicMock()
        engine.metadata = [m0, m1, m2]

        engine.hybrid_search.return_value = [{**m1, "rrf_score": 0.9}]

        clusters = find_clusters(
            engine, source_prefix="project/", similarity_threshold=0.75,
            min_cluster_size=2,
        )

        # m2 should not be in any cluster because its source doesn't match
        for cluster in clusters:
            for mem in cluster:
                assert mem["source"].startswith("project/")

    def test_respects_min_cluster_size(self):
        from consolidator import find_clusters

        m0 = _make_memory(0, "Alpha fact")
        m1 = _make_memory(1, "Alpha related fact")

        engine = MagicMock()
        engine.metadata = [m0, m1]
        engine.hybrid_search.return_value = [{**m1, "rrf_score": 0.8}]

        # min_cluster_size=3 means a pair won't qualify
        clusters = find_clusters(engine, min_cluster_size=3)
        assert clusters == []

        # min_cluster_size=2 should return the pair
        clusters = find_clusters(engine, min_cluster_size=2)
        assert len(clusters) == 1


class TestConsolidation:
    """Tests for consolidate_cluster()."""

    def test_consolidate_cluster_merges_memories(self):
        from consolidator import consolidate_cluster

        m0 = _make_memory(0, "We use Postgres", category="DECISION")
        m1 = _make_memory(1, "Postgres with pgvector", category="DECISION")
        m2 = _make_memory(2, "Postgres for storage", category="DETAIL")
        cluster = [m0, m1, m2]

        provider = MagicMock()
        provider.complete.return_value = _cr(
            json.dumps(["We use Postgres with pgvector for relational data storage"])
        )

        engine = MagicMock()
        engine.add_memories.return_value = [100]

        result = consolidate_cluster(provider, engine, cluster, dry_run=False)

        assert result["merged_count"] == 3
        assert result["new_count"] == 1
        assert set(result["old_ids"]) == {0, 1, 2}
        assert len(result["new_texts"]) == 1
        assert result["dry_run"] is False

        # Verify old memories were deleted
        engine.delete_memories.assert_called_once_with([0, 1, 2])

        # Verify new memory was added with metadata
        add_call = engine.add_memories.call_args
        assert add_call[1]["texts"] == ["We use Postgres with pgvector for relational data storage"]
        meta = add_call[1]["metadata_list"][0]
        assert meta["category"] == "DECISION"  # dominant category
        assert set(meta["consolidated_from"]) == {0, 1, 2}

    def test_dry_run_does_not_mutate(self):
        from consolidator import consolidate_cluster

        m0 = _make_memory(0, "We use Postgres", category="DECISION")
        m1 = _make_memory(1, "Postgres with pgvector", category="DECISION")
        cluster = [m0, m1]

        provider = MagicMock()
        provider.complete.return_value = _cr(
            json.dumps(["Consolidated: Postgres with pgvector"])
        )

        engine = MagicMock()

        result = consolidate_cluster(provider, engine, cluster, dry_run=True)

        assert result["merged_count"] == 2
        assert result["new_count"] == 1
        assert result["dry_run"] is True
        assert len(result["new_texts"]) == 1

        # Engine should NOT be called for mutations
        engine.delete_memories.assert_not_called()
        engine.delete_memory.assert_not_called()
        engine.add_memories.assert_not_called()

    def test_consolidate_uses_dominant_category(self):
        from consolidator import consolidate_cluster

        m0 = _make_memory(0, "Fact A", category="DETAIL")
        m1 = _make_memory(1, "Fact B", category="DECISION")
        m2 = _make_memory(2, "Fact C", category="DECISION")
        cluster = [m0, m1, m2]

        provider = MagicMock()
        provider.complete.return_value = _cr(json.dumps(["Merged fact"]))

        engine = MagicMock()
        engine.add_memories.return_value = [50]

        result = consolidate_cluster(provider, engine, cluster, dry_run=False)

        add_call = engine.add_memories.call_args
        meta = add_call[1]["metadata_list"][0]
        assert meta["category"] == "DECISION"

    def test_consolidate_handles_multiple_outputs(self):
        from consolidator import consolidate_cluster

        m0 = _make_memory(0, "Fact A")
        m1 = _make_memory(1, "Fact B")
        m2 = _make_memory(2, "Fact C")
        cluster = [m0, m1, m2]

        provider = MagicMock()
        provider.complete.return_value = _cr(
            json.dumps(["Merged fact 1", "Merged fact 2"])
        )

        engine = MagicMock()
        engine.add_memories.return_value = [50, 51]

        result = consolidate_cluster(provider, engine, cluster, dry_run=False)

        assert result["new_count"] == 2
        assert len(result["new_texts"]) == 2


class TestPruning:
    """Tests for find_prune_candidates()."""

    def test_identifies_old_unretrieved_detail(self):
        from consolidator import find_prune_candidates

        old_date = (datetime.now(timezone.utc) - timedelta(days=112)).isoformat()
        m0 = _make_memory(0, "Old detail", category="DETAIL", created_at=old_date)
        m1 = _make_memory(1, "Recent detail", category="DETAIL")

        candidates = find_prune_candidates(
            all_memories=[m0, m1],
            unretrieved_ids=[0, 1],
            detail_days=60,
            decision_days=120,
        )

        candidate_ids = [c["id"] for c in candidates]
        assert 0 in candidate_ids  # 112 days > 60 threshold
        assert 1 not in candidate_ids  # recent, not old enough

    def test_decision_uses_longer_threshold(self):
        from consolidator import find_prune_candidates

        old_date = (datetime.now(timezone.utc) - timedelta(days=112)).isoformat()
        m0 = _make_memory(0, "Old decision", category="DECISION", created_at=old_date)

        candidates = find_prune_candidates(
            all_memories=[m0],
            unretrieved_ids=[0],
            detail_days=60,
            decision_days=120,
        )

        # 112 days < 120 day threshold for DECISION → should NOT be pruned
        assert len(candidates) == 0

    def test_decision_pruned_when_exceeds_threshold(self):
        from consolidator import find_prune_candidates

        old_date = (datetime.now(timezone.utc) - timedelta(days=150)).isoformat()
        m0 = _make_memory(0, "Very old decision", category="DECISION", created_at=old_date)

        candidates = find_prune_candidates(
            all_memories=[m0],
            unretrieved_ids=[0],
            detail_days=60,
            decision_days=120,
        )

        assert len(candidates) == 1
        assert candidates[0]["id"] == 0

    def test_retrieved_memories_are_never_pruned(self):
        from consolidator import find_prune_candidates

        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        m0 = _make_memory(0, "Old but retrieved", category="DETAIL", created_at=old_date)

        candidates = find_prune_candidates(
            all_memories=[m0],
            unretrieved_ids=[],  # m0 was retrieved
            detail_days=60,
            decision_days=120,
        )

        assert len(candidates) == 0

    def test_learning_uses_decision_threshold(self):
        from consolidator import find_prune_candidates

        old_date = (datetime.now(timezone.utc) - timedelta(days=112)).isoformat()
        m0 = _make_memory(0, "Old learning", category="LEARNING", created_at=old_date)

        candidates = find_prune_candidates(
            all_memories=[m0],
            unretrieved_ids=[0],
            detail_days=60,
            decision_days=120,
        )

        # LEARNING uses same threshold as DECISION (120 days)
        # 112 < 120 → should NOT be pruned
        assert len(candidates) == 0

    def test_handles_z_suffix_timestamps(self):
        from consolidator import find_prune_candidates

        # Use Z suffix instead of +00:00
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        m0 = _make_memory(0, "Old detail with Z", category="DETAIL", created_at=old_date)

        candidates = find_prune_candidates(
            all_memories=[m0],
            unretrieved_ids=[0],
            detail_days=60,
        )

        assert len(candidates) == 1

    def test_handles_missing_category(self):
        from consolidator import find_prune_candidates

        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        m0 = _make_memory(0, "No category", created_at=old_date)
        del m0["category"]

        candidates = find_prune_candidates(
            all_memories=[m0],
            unretrieved_ids=[0],
            detail_days=60,
        )

        # Without category, should use DETAIL threshold (default)
        assert len(candidates) == 1
