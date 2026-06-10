"""Tests for extraction dry-run and commit endpoint."""
import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

import memory_engine as _me


class _StubEmbedder:
    def get_sentence_embedding_dimension(self):
        return 8

    def encode(self, sentences, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        if isinstance(sentences, str):
            sentences = [sentences]
        out = np.zeros((len(sentences), 8), dtype=np.float32)
        for i, t in enumerate(sentences):
            rng = np.random.default_rng(abs(hash(t)) % (2**32))
            v = rng.standard_normal(8).astype(np.float32)
            out[i] = v / max(float((v ** 2).sum() ** 0.5), 1e-9)
        return out

    def close(self):
        pass


class TestExtractionDryRun:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:9", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                with patch.object(_me.MemoryEngine, "_make_embedder", lambda self: _StubEmbedder()):
                    importlib.reload(app_module)
                    with TestClient(app_module.app) as tc:
                        mock_engine = MagicMock()
                        mock_engine.metadata = []
                        app_module.memory = mock_engine
                        yield tc, mock_engine

    def test_dry_run_field_accepted(self, client):
        """dry_run=True should be accepted (not a 422 validation error)."""
        tc, mock = client
        resp = tc.post(
            "/memory/extract",
            json={"messages": "Test message.", "source": "test/", "dry_run": True},
        )
        # 422 = validation error (field rejected); any other code means field was accepted
        assert resp.status_code != 422

    def test_dry_run_false_accepted(self, client):
        """dry_run=False (default) should be accepted (not a 422 validation error)."""
        tc, mock = client
        resp = tc.post(
            "/memory/extract",
            json={"messages": "Test message.", "source": "test/", "dry_run": False},
        )
        assert resp.status_code != 422

    def test_dry_run_propagates_in_profile(self, client):
        """When dry_run=True, the profile passed to run_extraction has dry_run=True."""
        tc, mock = client
        captured = {}

        def fake_run_extraction(provider, engine, messages, source, context,
                                allowed_prefixes=None, debug=False, profile=None, document_at=None):
            captured["profile"] = profile
            return {
                "dry_run": True,
                "actions": [],
                "extracted_count": 0,
                "tokens": {"extract": {"input": 0, "output": 0}, "audn": {"input": 0, "output": 0}},
            }

        with patch("app.run_extraction", fake_run_extraction), \
             patch("app.extract_provider", MagicMock()):
            resp = tc.post(
                "/memory/extract",
                json={"messages": "Test message.", "source": "test/", "dry_run": True},
            )
        assert resp.status_code in (200, 202)
        assert captured.get("profile", {}).get("dry_run") is True

    def test_dry_run_false_not_in_profile(self, client):
        """When dry_run=False, the profile should NOT have dry_run set to True."""
        tc, mock = client
        captured = {}

        def fake_run_extraction(provider, engine, messages, source, context,
                                allowed_prefixes=None, debug=False, profile=None, document_at=None):
            captured["profile"] = profile
            return {
                "actions": [],
                "extracted_count": 0,
                "stored_count": 0,
                "updated_count": 0,
                "deleted_count": 0,
                "conflict_count": 0,
            }

        with patch("app.run_extraction", fake_run_extraction), \
             patch("app.extract_provider", MagicMock()):
            resp = tc.post(
                "/memory/extract",
                json={"messages": "Test message.", "source": "test/"},
            )
        assert resp.status_code in (200, 202)
        profile = captured.get("profile") or {}
        assert not profile.get("dry_run")


class TestRunExtractionDryRun:
    """Unit tests for dry_run intercept in run_extraction()."""

    def test_dry_run_returns_before_execute_actions(self):
        """dry_run in profile causes run_extraction to return actions without executing."""
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_provider.supports_audn = True

        # extract_facts returns 2 facts
        mock_provider.complete.side_effect = [
            MagicMock(
                text='[{"category":"decision","text":"Use Redis for caching"},{"category":"detail","text":"Port is 6379"}]',
                input_tokens=10,
                output_tokens=5,
            ),
            # AUDN call
            MagicMock(
                text='[{"action":"ADD","fact_index":0},{"action":"ADD","fact_index":1}]',
                input_tokens=20,
                output_tokens=10,
            ),
        ]

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []

        result = run_extraction(
            provider=mock_provider,
            engine=mock_engine,
            messages="Test conversation",
            source="test/proj",
            profile={"dry_run": True},
        )

        assert result.get("dry_run") is True
        assert "actions" in result
        assert "extracted_count" in result
        assert "tokens" in result
        # execute_actions should NOT have been called → no add_memories calls
        mock_engine.add_memories.assert_not_called()

    def test_dry_run_false_does_execute(self):
        """When dry_run is not set, run_extraction proceeds to execute_actions."""
        from llm_extract import run_extraction

        mock_provider = MagicMock()
        mock_provider.supports_audn = True

        mock_provider.complete.side_effect = [
            MagicMock(
                text='[{"category":"decision","text":"Use Redis for caching"}]',
                input_tokens=10,
                output_tokens=5,
            ),
            MagicMock(
                text='[{"action":"ADD","fact_index":0}]',
                input_tokens=20,
                output_tokens=10,
            ),
        ]

        mock_engine = MagicMock()
        mock_engine.hybrid_search.return_value = []
        mock_engine.add_memories.return_value = [42]

        result = run_extraction(
            provider=mock_provider,
            engine=mock_engine,
            messages="Test conversation",
            source="test/proj",
            profile=None,
        )

        assert result.get("dry_run") is not True
        assert result.get("stored_count") == 1
        mock_engine.add_memories.assert_called_once()


class TestExtractCommit:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "ollama", "OLLAMA_URL": "http://127.0.0.1:9", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                with patch.object(_me.MemoryEngine, "_make_embedder", lambda self: _StubEmbedder()):
                    importlib.reload(app_module)
                    with TestClient(app_module.app) as tc:
                        mock_engine = MagicMock()
                        mock_engine.metadata = []
                        app_module.memory = mock_engine
                        yield tc, mock_engine

    def test_commit_approved_actions(self, client):
        """Only approved=True actions are stored."""
        tc, mock = client
        mock.add_memories.return_value = [1]
        resp = tc.post(
            "/memory/extract/commit",
            json={
                "actions": [
                    {
                        "action": "ADD",
                        "fact_index": 0,
                        "fact": {"text": "fact one", "category": "decision"},
                        "approved": True,
                    },
                    {
                        "action": "ADD",
                        "fact_index": 1,
                        "fact": {"text": "rejected fact", "category": "detail"},
                        "approved": False,
                    },
                ],
                "source": "commit-test/",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored_count"] == 1
        mock.add_memories.assert_called_once()

    def test_commit_empty_approved(self, client):
        """No approved actions → zero counts, no writes."""
        tc, mock = client
        resp = tc.post(
            "/memory/extract/commit",
            json={
                "actions": [
                    {
                        "action": "ADD",
                        "fact_index": 0,
                        "fact": {"text": "all rejected"},
                        "approved": False,
                    }
                ],
                "source": "noop/",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored_count"] == 0
        assert data["updated_count"] == 0
        assert data["deleted_count"] == 0
        mock.add_memories.assert_not_called()

    def test_commit_no_actions_at_all(self, client):
        """Empty actions list → zero counts."""
        tc, mock = client
        resp = tc.post(
            "/memory/extract/commit",
            json={"actions": [], "source": "empty/"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored_count"] == 0

    def test_commit_requires_source(self, client):
        """source field is required."""
        tc, mock = client
        resp = tc.post(
            "/memory/extract/commit",
            json={
                "actions": [
                    {"action": "ADD", "fact_index": 0, "fact": {"text": "x"}, "approved": True}
                ],
            },
        )
        assert resp.status_code == 422

    def test_commit_multiple_approved(self, client):
        """Multiple approved ADD actions result in correct stored_count."""
        tc, mock = client
        mock.add_memories.return_value = [10]
        resp = tc.post(
            "/memory/extract/commit",
            json={
                "actions": [
                    {
                        "action": "ADD",
                        "fact_index": i,
                        "fact": {"text": f"fact {i}", "category": "detail"},
                        "approved": True,
                    }
                    for i in range(3)
                ],
                "source": "multi-test/",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["stored_count"] == 3
        assert mock.add_memories.call_count == 3
