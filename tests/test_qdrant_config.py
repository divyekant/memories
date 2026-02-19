"""Tests for Qdrant configuration and compose wiring."""

import importlib
import os
from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_dependencies_use_qdrant_client_not_faiss():
    deps = _read("pyproject.toml")
    assert "qdrant-client" in deps
    assert "faiss-cpu" not in deps


def test_compose_defines_qdrant_service_and_env():
    compose = _read("docker-compose.yml")
    assert "qdrant:" in compose
    assert "QDRANT_URL=" in compose and "http://qdrant:6333" in compose
    assert "QDRANT_COLLECTION=" in compose and "memories" in compose
    assert "depends_on:" in compose


def test_compose_snippet_defines_qdrant_service_and_env():
    compose = _read("docker-compose.snippet.yml")
    assert "qdrant:" in compose
    assert "QDRANT_URL=" in compose and "http://qdrant:6333" in compose
    assert "QDRANT_COLLECTION=" in compose and "memories" in compose
    assert "depends_on:" in compose


def test_qdrant_settings_from_env_parses_defaults():
    env = {
        "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_COLLECTION": "memories",
    }
    from unittest.mock import patch

    with patch.dict(os.environ, env, clear=False):
        import qdrant_config

        importlib.reload(qdrant_config)
        settings = qdrant_config.QdrantSettings.from_env()
        assert settings.url == "http://qdrant:6333"
        assert settings.collection == "memories"
        assert settings.wait is True
        assert settings.write_ordering == "strong"
        assert settings.read_consistency == "majority"
