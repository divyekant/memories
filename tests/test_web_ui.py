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


def test_app_js_has_detail_panel_enhancements(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "confidenceBar" in text
    assert "loadLinks" in text
    assert "conflict-badge" in text
    assert "/links" in text


def test_app_js_has_search_feedback_and_explain(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "feedback-btn" in text
    assert "/search/feedback" in text
    assert "/search/explain" in text
    assert "score-tooltip" in text


def test_app_js_has_health_page_renderer(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert 'registerPage("health"' in text
    assert "/memory/conflicts" in text
    assert "/metrics/extraction-quality" in text or "extraction-quality" in text
    assert "/metrics/search-quality" in text or "search-quality" in text
    assert "/metrics/failures" in text


def test_app_js_extractions_page_has_quality_badge(client):
    js_response = client.get("/ui/static/app.js")
    text = js_response.text
    assert "/metrics/extraction-quality" in text
    assert "Quality:" in text


# -- Behavioral coverage for v3 features ------------------------------------


def test_conflict_resolution_clears_metadata(client):
    """Keep A/B must PATCH surviving memory to clear conflicts_with after delete."""
    js = client.get("/ui/static/app.js").text
    # Both Keep A and Keep B paths should patch metadata_patch with conflicts_with: null
    assert js.count("metadata_patch") >= 3  # Keep A, Keep B, and Dismiss all clear it


def test_failures_card_shows_admin_fallback(client):
    """Failures stat card should show 'Admin only' when request is rejected, not '0'."""
    js = client.get("/ui/static/app.js").text
    # failures starts as null (not []), and null triggers the admin-only fallback
    assert "let failures = null" in js
    assert 'failures != null' in js  # conditional check before rendering count


def test_links_include_incoming(client):
    """Linked memories should request include_incoming=true."""
    js = client.get("/ui/static/app.js").text
    assert "include_incoming=true" in js


def test_components_js_exports(client):
    """components.js should export all 7 reusable component functions."""
    resp = client.get("/ui/static/components.js")
    assert resp.status_code == 200
    js = resp.text
    for fn in [
        "editableField", "actionBadge", "approvalToggle",
        "bulkSelectMode", "memoryCard", "timelineEvent", "comparisonPanel",
    ]:
        assert f"export function {fn}" in js, f"Missing export: {fn}"
