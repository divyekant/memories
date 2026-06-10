"""Tests for scripts/reembed.py — blue/green re-embedding migration.

Uses a fake embedder and qdrant-client's in-memory local mode, following
the fake-driven patterns of tests/test_qdrant_store.py.
"""

import json

import numpy as np
import pytest
from qdrant_client import QdrantClient, models

from scripts.reembed import (
    ReembedError,
    ReembedMigrator,
    apply_cutover,
    build_env_updates,
    check_cutover_ready,
)


class FakeEmbedder:
    """Deterministic hash-seeded embedder (no model download)."""

    def __init__(self, dim):
        self._dim = dim
        self.encode_calls = 0

    def get_sentence_embedding_dimension(self):
        return self._dim

    def encode(self, sentences, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        self.encode_calls += 1
        out = np.zeros((len(sentences), self._dim), dtype=np.float32)
        for i, text in enumerate(sentences):
            seed = abs(hash(("fake", text))) % (2**32)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self._dim).astype(np.float32)
            out[i] = vec / max(np.linalg.norm(vec), 1e-9)
        return out


SOURCE = "memories"
TARGET = "memories__new_model_16d"


@pytest.fixture
def client():
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=SOURCE,
        vectors_config=models.VectorParams(size=8, distance=models.Distance.COSINE),
    )
    old = FakeEmbedder(8)
    texts = [f"memory number {i} about topic {i % 5}" for i in range(25)]
    vectors = old.encode(texts)
    client.upsert(
        collection_name=SOURCE,
        points=[
            models.PointStruct(
                id=i,
                vector=vectors[i].tolist(),
                payload={"text": texts[i], "source": f"test/{i % 3}", "is_latest": True},
            )
            for i in range(25)
        ],
    )
    return client


def _migrator(client, tmp_path, batch_size=10, target=TARGET):
    return ReembedMigrator(
        client=client,
        embedder=FakeEmbedder(16),
        source=SOURCE,
        target=target,
        batch_size=batch_size,
        state_path=tmp_path / "state.json",
        signature="fake:new-model:16d",
    )


class TestMigration:
    def test_full_migration(self, client, tmp_path):
        result = _migrator(client, tmp_path).run()
        assert result["done"] is True
        assert result["migrated"] == 25
        assert client.count(collection_name=TARGET, exact=True).count == 25

        info = client.get_collection(TARGET)
        assert info.config.params.vectors.size == 16

        records = client.retrieve(collection_name=TARGET, ids=[3], with_payload=True)
        assert records[0].payload["text"] == "memory number 3 about topic 3"
        assert records[0].payload["source"] == "test/0"

    def test_resumable_across_instances(self, client, tmp_path):
        first = _migrator(client, tmp_path).run(max_batches=1)
        assert first["done"] is False
        assert first["migrated"] == 10
        assert (tmp_path / "state.json").exists()

        second = _migrator(client, tmp_path).run()
        assert second["done"] is True
        assert second["migrated"] == 25
        assert client.count(collection_name=TARGET, exact=True).count == 25

        state = json.loads((tmp_path / "state.json").read_text())
        assert state["done"] is True
        assert state["migrated"] == 25

    def test_completed_run_is_idempotent(self, client, tmp_path):
        _migrator(client, tmp_path).run()
        again = _migrator(client, tmp_path).run()
        assert again["done"] is True
        assert client.count(collection_name=TARGET, exact=True).count == 25

    def test_skips_points_without_text(self, client, tmp_path):
        client.upsert(
            collection_name=SOURCE,
            points=[models.PointStruct(id=999, vector=[0.1] * 8, payload={"source": "x"})],
        )
        result = _migrator(client, tmp_path).run()
        assert result["skipped"] == 1
        assert result["migrated"] == 25
        assert client.count(collection_name=TARGET, exact=True).count == 25

    def test_state_mismatch_refused(self, client, tmp_path):
        (tmp_path / "state.json").write_text(
            json.dumps(
                {
                    "source": SOURCE,
                    "target": "some_other_collection",
                    "signature": "fake:other:8d",
                    "offset": None,
                    "migrated": 5,
                    "done": False,
                }
            )
        )
        with pytest.raises(ReembedError, match="state"):
            _migrator(client, tmp_path).run()

    def test_existing_target_with_wrong_dim_refused(self, client, tmp_path):
        client.create_collection(
            collection_name=TARGET,
            vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
        )
        with pytest.raises(ReembedError, match="dimension"):
            _migrator(client, tmp_path).run()

    def test_same_source_and_target_refused(self, client, tmp_path):
        with pytest.raises(ReembedError, match="source"):
            _migrator(client, tmp_path, target=SOURCE).run()


class TestVerification:
    def test_verify_reports_neighbor_overlap(self, client, tmp_path):
        migrator = _migrator(client, tmp_path)
        migrator.run()
        report = migrator.verify(samples=10, k=5, seed=42)
        assert report["samples"] == 10
        assert report["k"] == 5
        assert 0.0 <= report["mean_overlap"] <= 1.0
        assert len(report["per_sample"]) == 10
        for row in report["per_sample"]:
            assert "id" in row and "overlap" in row
            assert 0.0 <= row["overlap"] <= 1.0
        assert report["source_count"] == report["target_count"] == 25

    def test_verify_caps_samples_at_population(self, client, tmp_path):
        migrator = _migrator(client, tmp_path)
        migrator.run()
        report = migrator.verify(samples=500, k=5, seed=1)
        assert report["samples"] == 25


class TestCutover:
    def test_check_cutover_ready(self, client, tmp_path):
        migrator = _migrator(client, tmp_path)
        migrator.run()
        ok, detail = check_cutover_ready(client, SOURCE, TARGET)
        assert ok is True

        client.delete(
            collection_name=TARGET,
            points_selector=models.PointIdsList(points=[0]),
        )
        ok, detail = check_cutover_ready(client, SOURCE, TARGET)
        assert ok is False
        assert "24" in detail and "25" in detail

    def test_build_env_updates(self):
        updates = build_env_updates(
            provider="openai",
            model="qwen3-embedding-0.6b",
            base_url="http://host.docker.internal:11434/v1",
            dimension=16,
            target_collection=TARGET,
        )
        assert updates["EMBED_PROVIDER"] == "openai"
        assert updates["EMBED_MODEL"] == "qwen3-embedding-0.6b"
        assert updates["EMBED_BASE_URL"] == "http://host.docker.internal:11434/v1"
        assert updates["EMBED_DIMENSION"] == "16"
        assert updates["EMBED_COLLECTION"] == TARGET
        assert "EMBED_API_KEY" not in updates  # secrets are never written

    def test_dry_run_does_not_touch_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=secret\nEMBED_MODEL=old-model\n")
        before = env_file.read_text()

        result = apply_cutover(
            env_file, {"EMBED_MODEL": "new-model", "EMBED_COLLECTION": TARGET}, execute=False
        )
        assert env_file.read_text() == before
        assert result["executed"] is False
        assert result["rollback"]["EMBED_MODEL"] == "old-model"

    def test_execute_rewrites_env_and_backs_up(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_KEY=secret\nEMBED_MODEL=old-model\n")

        result = apply_cutover(
            env_file, {"EMBED_MODEL": "new-model", "EMBED_COLLECTION": TARGET}, execute=True
        )
        content = env_file.read_text()
        assert "EMBED_MODEL=new-model" in content
        assert f"EMBED_COLLECTION={TARGET}" in content
        assert "API_KEY=secret" in content  # unrelated lines preserved
        assert "EMBED_MODEL=old-model" not in content
        assert result["executed"] is True

        backups = list(tmp_path.glob(".env.bak-*"))
        assert len(backups) == 1
        assert "EMBED_MODEL=old-model" in backups[0].read_text()

    def test_execute_creates_env_file_when_missing(self, tmp_path):
        env_file = tmp_path / "new.env"
        result = apply_cutover(env_file, {"EMBED_COLLECTION": TARGET}, execute=True)
        assert result["executed"] is True
        assert f"EMBED_COLLECTION={TARGET}" in env_file.read_text()
