"""Tests for LongMemEval benchmark adapter and MemoriesClient extensions."""

import pytest
from unittest.mock import patch, MagicMock
import json


def test_memories_client_has_search_method():
    """MemoriesClient should have a search() method."""
    from eval.memories_client import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "search")
    assert callable(client.search)


def test_memories_client_has_extract_method():
    """MemoriesClient should have an extract() method."""
    from eval.memories_client import MemoriesClient
    client = MemoriesClient(url="http://localhost:8900", api_key="test")
    assert hasattr(client, "extract")
    assert callable(client.extract)
