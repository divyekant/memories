import importlib, os, tempfile
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient


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
