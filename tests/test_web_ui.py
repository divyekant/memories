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
    assert "<title>Memories</title>" in response.text
    assert "Dashboard" in response.text


def test_ui_static_assets_are_served(client):
    css_response = client.get("/ui/static/styles.css")
    js_response = client.get("/ui/static/app.js")

    assert css_response.status_code == 200
    assert "--color-primary" in css_response.text

    assert js_response.status_code == 200
    assert "loadMemories" in js_response.text
    assert "syncKeyStatus" in js_response.text
    assert "addEventListener(\"keydown\"" in js_response.text


def test_styles_contain_confidence_and_health_classes(client):
    css_response = client.get("/ui/static/styles.css")
    assert css_response.status_code == 200
    assert ".confidence-bar" in css_response.text
    assert ".feedback-btn" in css_response.text
    assert ".score-tooltip" in css_response.text
    assert ".conflict-card" in css_response.text
    assert ".health-stat-grid" in css_response.text


def test_health_nav_item_exists(client):
    response = client.get("/ui")
    assert response.status_code == 200
    assert 'data-page="health"' in response.text
    assert "Health" in response.text


def test_app_js_has_health_page_title(client):
    js_response = client.get("/ui/static/app.js")
    assert js_response.status_code == 200
    assert "health:" in js_response.text


def test_app_js_has_confidence_and_link_helpers(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "confidenceColor" in text
    assert "confidenceBar" in text
    assert "linkTypeColor" in text
