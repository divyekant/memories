"""POST /memory/{id}/supersede and GET /memory/conflicts pagination."""

from __future__ import annotations

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {"API_KEY": "", "EXTRACT_PROVIDER": "", "DATA_DIR": tmpdir}
        with patch.dict(os.environ, env):
            import app as app_module
            importlib.reload(app_module)

            mock_engine = MagicMock()
            mock_engine.metadata = []
            app_module.memory = mock_engine
            yield TestClient(app_module.app), mock_engine


class TestSupersedeEndpoint:
    def test_supersede_calls_engine_and_returns_chain(self, client):
        tc, mock = client
        mock.get_memory.return_value = {"id": 5, "text": "old", "source": "learning/x"}
        mock.supersede.return_value = {
            "old_id": 5, "new_id": 9, "previous_text": "old", "archived_old": True,
        }

        resp = tc.post("/memory/5/supersede", json={"text": "new fact"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["new_id"] == 9 and body["old_id"] == 5 and body["archived_old"] is True
        args, kwargs = mock.supersede.call_args
        assert args[0] == 5 and args[1] == "new fact"

    def test_supersede_pinned_returns_409(self, client):
        tc, mock = client
        mock.get_memory.return_value = {"id": 5, "text": "old", "source": "learning/x", "pinned": True}

        resp = tc.post("/memory/5/supersede", json={"text": "new fact"})

        assert resp.status_code == 409
        mock.supersede.assert_not_called()

    def test_supersede_missing_returns_404(self, client):
        tc, mock = client
        mock.get_memory.side_effect = ValueError("Memory ID 5 not found")

        resp = tc.post("/memory/5/supersede", json={"text": "new fact"})

        assert resp.status_code == 404


class TestConflictsPagination:
    def _seed(self, mock, n):
        mock.metadata = [
            {"id": i, "text": f"t{i}", "source": "learning/x", "conflicts_with": 1000 + i}
            for i in range(n)
        ]
        mock.get_memory.side_effect = lambda cid: {"id": cid, "text": "other", "source": "learning/x"}

    def test_default_page_and_total(self, client):
        tc, mock = client
        self._seed(mock, 60)

        resp = tc.get("/memory/conflicts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 60
        assert body["count"] == 50  # default limit
        assert body["has_more"] is True

    def test_offset_walks_pages(self, client):
        tc, mock = client
        self._seed(mock, 60)

        resp = tc.get("/memory/conflicts?limit=50&offset=50")

        body = resp.json()
        assert body["count"] == 10
        assert body["offset"] == 50
        assert body["has_more"] is False

    def test_limit_is_capped(self, client):
        tc, mock = client
        self._seed(mock, 5)

        resp = tc.get("/memory/conflicts?limit=99999")

        assert resp.status_code == 200
        assert resp.json()["count"] == 5
