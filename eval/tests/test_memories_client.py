"""Tests for the Memories API client."""

from unittest.mock import patch, MagicMock

import httpx
import pytest

from eval.memories_client import MemoriesClient


@pytest.fixture
def client():
    return MemoriesClient(url="http://localhost:8900", api_key="test-key")


class TestSeedMemories:
    def test_seed_memories_uses_batch_add_for_multiple_items(self, client):
        mock_post = MagicMock()
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"success": True, "ids": [1, 2]}),
        )
        with patch.object(client._client, "post", mock_post):
            ids = client.seed_memories([
                {"text": "SQLite chosen over Postgres", "source": "eval/test-001"},
                {"text": "Auth uses JWT tokens", "source": "eval/test-001"},
            ])

        assert ids == [1, 2]
        mock_post.assert_called_once_with(
            "/memory/add-batch",
            json={
                "memories": [
                    {"text": "SQLite chosen over Postgres", "source": "eval/test-001"},
                    {"text": "Auth uses JWT tokens", "source": "eval/test-001"},
                ],
                "deduplicate": False,
            },
        )

    def test_seed_memories_uses_single_add_for_one_item(self, client):
        mock_post = MagicMock()
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"success": True, "id": 1}),
        )
        with patch.object(client._client, "post", mock_post):
            ids = client.seed_memories([
                {"text": "SQLite chosen over Postgres", "source": "eval/test-001"},
            ])

        assert ids == [1]
        mock_post.assert_called_once_with(
            "/memory/add",
            json={
                "text": "SQLite chosen over Postgres",
                "source": "eval/test-001",
                "metadata": None,
                "deduplicate": False,
            },
        )


class TestAddBatch:
    def test_add_batch_splits_large_payloads(self, client):
        mock_post = MagicMock()
        mock_post.side_effect = [
            MagicMock(status_code=200, json=MagicMock(return_value={"ids": list(range(1000))})),
            MagicMock(status_code=200, json=MagicMock(return_value={"ids": [1000, 1001]})),
        ]
        memories = [
            {"text": f"memory {i}", "source": "eval/test-001"}
            for i in range(1002)
        ]

        with patch.object(client._client, "post", mock_post):
            ids = client.add_batch(memories, deduplicate=False)

        assert len(ids) == 1002
        assert mock_post.call_count == 2
        first_batch = mock_post.call_args_list[0].kwargs["json"]["memories"]
        second_batch = mock_post.call_args_list[1].kwargs["json"]["memories"]
        assert len(first_batch) == 1000
        assert len(second_batch) == 2


class TestClearByPrefix:
    def test_clear_by_prefix(self, client):
        mock_post = MagicMock()
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"success": True, "deleted": 5}),
        )
        with patch.object(client._client, "post", mock_post):
            deleted = client.clear_by_prefix("eval/test-001")

        assert deleted == 5
        mock_post.assert_called_once_with(
            "/memory/delete-by-prefix",
            json={"source_prefix": "eval/test-001"},
        )


class TestHealthCheck:
    def test_health_check(self, client):
        mock_get = MagicMock()
        mock_get.return_value = MagicMock(status_code=200)
        with patch.object(client._client, "get", mock_get):
            assert client.health_check() is True
        mock_get.assert_called_once_with("/health/ready")

    def test_health_check_fails(self, client):
        mock_get = MagicMock(side_effect=httpx.ConnectError("connection refused"))
        with patch.object(client._client, "get", mock_get):
            assert client.health_check() is False


class TestGetStats:
    def test_get_stats(self, client):
        expected = {"total_memories": 42, "index_type": "hnsw"}
        mock_get = MagicMock()
        mock_get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=expected),
        )
        with patch.object(client._client, "get", mock_get):
            result = client.get_stats()

        assert result == expected
        mock_get.assert_called_once_with("/stats")
