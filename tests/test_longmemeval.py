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


def test_longmemeval_runner_init():
    """LongMemEvalRunner should accept client and judge config."""
    from eval.longmemeval import LongMemEvalRunner
    client = MagicMock()
    runner = LongMemEvalRunner(client=client, judge_provider="anthropic")
    assert runner is not None


def test_longmemeval_report_format():
    """Report should include overall score, categories, and delta."""
    from eval.longmemeval import LongMemEvalResult
    result = LongMemEvalResult(
        version="3.2.2",
        overall=0.724,
        categories={"information_extraction": 0.78},
    )
    assert result.overall == 0.724
    assert "information_extraction" in result.categories
