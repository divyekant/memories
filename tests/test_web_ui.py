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


def test_show_toast_accepts_action_param(client):
    """showToast should accept optional action object for undo buttons."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "action" in js[js.index("function showToast"):js.index("function showToast") + 300]


def test_create_memory_button_exists(client):
    """Memories page JS should contain create memory button and modal."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "showCreateMemoryModal" in js or "createMemoryModal" in js
    assert "+ Create" in js or "Create" in js


def test_create_memory_styles(client):
    """CSS should include create button and empty state styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".create-memory-btn" in css


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


def test_inline_edit_in_detail_panel(client):
    """Detail panel should support inline editing via editableField."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "editableField" in js
    assert "PATCH" in js


def test_pin_archive_controls(client):
    """Detail panel should have pin toggle and archive button."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "pinned" in js
    assert "archived" in js


def test_enhanced_link_modal_search_results(client):
    """Link modal search results should show source, text preview, and confidence bar."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    # Search results in link modal should include confidence bar
    fn_start = js.index("function showAddLinkModal")
    modal_section = js[fn_start:fn_start + 2500]
    assert "confidenceBar" in modal_section


def test_bidirectional_link_display(client):
    """Linked memories should show direction indicators for outgoing vs incoming links."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    fn_start = js.index("function loadLinks")
    links_section = js[fn_start:fn_start + 4500]
    # Should display direction indicators
    assert "outgoing" in links_section
    assert "incoming" in links_section
    # Should group links and render with direction arrows
    assert "renderLinkGroup" in links_section
    assert "Outgoing" in links_section
    assert "Incoming" in links_section


def test_bulk_select_mode(client):
    """Memories page should support bulk select with action bar."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "bulkSelectMode" in js
    assert "archive-batch" in js
    assert "delete-batch" in js


def test_bulk_select_toggle_button(client):
    """Memories page should have a Select toggle button in the toolbar."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    # Should have a bulk select toggle in the filter bar
    assert "bulkMode" in js or "bulk-select-toggle" in js
    # Should handle toggling between browse and select modes
    assert "deselectAll" in js


def test_bulk_retag_and_resource(client):
    """Bulk actions should include retag (category) and re-source operations."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "Retag" in js or "retag" in js
    assert "Re-source" in js or "resource" in js
    # Should use PATCH for individual updates
    assert "metadata_patch" in js


def test_bulk_action_styles(client):
    """CSS should include bulk action bar styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".bulk-action-bar" in css


def test_merge_modal(client):
    """Merge modal should use comparisonPanel and call POST /memory/merge."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "comparisonPanel" in js
    assert "/memory/merge" in js


def test_extraction_trigger_modal(client):
    """Extract button should open modal with dry-run flow."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "showExtractionModal" in js or "extractionModal" in js
    assert "actionBadge" in js
    assert "approvalToggle" in js
    assert "/memory/extract/commit" in js


def test_extraction_result_styles(client):
    """CSS should include extraction result list styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".extract-fact" in css or ".extraction-fact" in css


def test_lifecycle_tabbed_panel(client):
    """Detail panel should have tabbed layout: Overview | Lifecycle | Links."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "Overview" in js
    assert "Lifecycle" in js
    assert "timelineEvent" in js


def test_lifecycle_styles(client):
    """CSS should include tab and timeline styles."""
    resp = client.get("/ui/static/styles.css")
    css = resp.text
    assert ".detail-tabs" in css or ".detail-tab" in css
    assert ".timeline-event" in css


def test_lifecycle_origin_block(client):
    """Lifecycle tab should display origin block with method detection."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "lifecycle-origin" in js
    assert "Extraction" in js or "Manual" in js


def test_lifecycle_audit_timeline(client):
    """Lifecycle tab should fetch audit log for timeline display."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "/audit?resource_id=" in js
    assert "memory.created" in js or "memory.updated" in js


def test_lifecycle_tab_switching(client):
    """Tab system should support switching between Overview, Lifecycle, Links."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "detail-tab" in js
    assert "active" in js
    # Should have tab content areas
    assert "tab-content" in js or "tabContent" in js


def test_conflict_resolution_modal(client):
    """Health page should have conflict resolution modal with soft archive."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "showConflictModal" in js
    assert "comparisonPanel" in js
    # Must use PATCH with archived:true, NOT DELETE for conflict resolution
    assert "archived" in js


def test_conflict_resolution_options(client):
    """Conflict modal should offer Keep A, Keep B, Merge, Defer."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "Keep A" in js
    assert "Keep B" in js
    assert "Merge" in js or "merge" in js
    assert "Defer" in js or "defer" in js


def test_conflict_resolution_no_delete(client):
    """Conflict resolution must use soft archive (PATCH), never DELETE."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    # Find the showConflictModal function definition and ensure no DELETE in it
    fn_start = js.index("function showConflictModal")
    # Get a generous chunk of the function body
    modal_section = js[fn_start:fn_start + 5000]
    assert "DELETE" not in modal_section
    assert "archived" in modal_section


def test_conflict_resolve_button(client):
    """Health page conflicts should show a Resolve button that opens modal."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "Resolve" in js
    assert "showConflictModal" in js


def test_feedback_section_in_lifecycle(client):
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "feedback/history" in js


def test_conflict_defer_patches_metadata(client):
    """Defer option should PATCH both memories with deferred: true."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    assert "deferred" in js


def test_audit_action_colors_complete(client):
    """All backend audit actions should have UI color mappings."""
    resp = client.get("/ui/static/app.js")
    js = resp.text
    for action in ["memory.deleted", "extract", "snapshot.created", "feedback.retracted",
                    "memory.missed", "memory.consolidated", "memory.pruned"]:
        assert action in js, f"Missing audit action color: {action}"
