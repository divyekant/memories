"""Tests for browser UI routes and static assets."""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": ""}):
        import importlib
        import app as app_module

        importlib.reload(app_module)

        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 5}
        mock_engine.stats.return_value = {"total_memories": 5}
        app_module.memory = mock_engine

        yield TestClient(app_module.app)


def test_ui_page_is_served_without_api_key_header(client):
    response = client.get("/ui")
    assert response.status_code == 200
    assert "Memory Observatory" in response.text


def test_ui_static_assets_are_served(client):
    css_response = client.get("/ui/static/styles.css")
    js_response = client.get("/ui/static/app.js")

    assert css_response.status_code == 200
    assert "--accent" in css_response.text

    assert js_response.status_code == 200
    assert "loadMemories" in js_response.text
