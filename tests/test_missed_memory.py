import importlib, os, tempfile
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from auth_context import AuthContext
from project_memory import ProjectMemoryPolicyError


class TestMissedMemory:
    @pytest.fixture
    def client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
            with patch.dict(os.environ, env):
                import app as app_module
                importlib.reload(app_module)
                mock_engine = MagicMock()
                mock_engine.metadata = []
                mock_engine.add_memories.return_value = [42]
                app_module.memory = mock_engine
                app_module._missed_counts = {}  # reset
                yield TestClient(app_module.app), mock_engine

    def test_missed_memory_creates_entry(self, client):
        tc, mock = client
        resp = tc.post("/memory/missed", json={"text": "API rate limit is 100 req/s", "source": "test/"})
        assert resp.status_code == 200
        assert resp.json()["id"] == 42
        mock.add_memories.assert_called_once()
        # Verify origin metadata
        call_kwargs = mock.add_memories.call_args
        metadata_list = call_kwargs[1].get("metadata_list") or call_kwargs[0][2]
        assert metadata_list[0]["origin"] == "missed_capture"

    def test_missed_memory_with_context(self, client):
        tc, mock = client
        resp = tc.post("/memory/missed", json={
            "text": "Port 5432", "source": "test/", "context": "debugging session"
        })
        assert resp.status_code == 200

    def test_missed_count_increments(self, client):
        tc, mock = client
        r1 = tc.post("/memory/missed", json={"text": "first", "source": "count/"})
        assert r1.json()["missed_count"] == 1
        r2 = tc.post("/memory/missed", json={"text": "second", "source": "count/"})
        assert r2.json()["missed_count"] == 2

    def test_managed_missed_capture_passes_trusted_authorship(self, client, monkeypatch):
        tc, mock = client
        import app as app_module

        monkeypatch.setattr(
            app_module,
            "_get_auth",
            lambda request: AuthContext(
                role="read-write",
                prefixes=["project/demo/"],
                key_type="managed",
                principal_id="alice",
            ),
        )
        resp = tc.post(
            "/memory/missed",
            json={"text": "project decision", "source": "project/demo/decisions"},
            headers={"X-Memories-Client": "codex"},
        )

        assert resp.status_code == 200
        trusted = mock.add_memories.call_args.kwargs["trusted_authorship"]
        assert trusted.author == "alice"

    def test_missed_project_policy_error_is_422(self, client):
        tc, mock = client
        mock.add_memories.side_effect = ProjectMemoryPolicyError("project policy")
        response = tc.post(
            "/memory/missed",
            json={"text": "project decision", "source": "project/demo/decisions"},
        )

        assert response.status_code == 422
