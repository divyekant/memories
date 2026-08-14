"""Owner/admin API coverage for Phase 2 promotion review."""

import importlib
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def promotion_api():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            "API_KEY": "admin-key",
            "EXTRACT_PROVIDER": "",
            "DATA_DIR": tmpdir,
            "AUDIT_LOG": "true",
        }
        with patch.dict(os.environ, env):
            import app as app_module

            importlib.reload(app_module)
            from audit_log import AuditLog
            from key_store import KeyStore

            store = KeyStore(os.path.join(tmpdir, "keys.db"))
            alice = store.create_key(
                "Alice",
                "read-write",
                ["person/alice/fplguru", "project/fplguru"],
                principal_id="alice",
            )
            bob = store.create_key(
                "Bob",
                "read-write",
                ["person/bob/fplguru", "project/fplguru"],
                principal_id="bob",
            )
            app_module.key_store = store
            app_module.audit_log = AuditLog(os.path.join(tmpdir, "audit.db"))
            service = MagicMock()

            def list_candidates(actor, **kwargs):
                rows = [
                    {"id": 1, "owner": "alice", "project_id": "fplguru", "status": "candidate"},
                    {"id": 2, "owner": "bob", "project_id": "fplguru", "status": "candidate"},
                ]
                return rows if actor.role == "admin" else [row for row in rows if row["owner"] == actor.principal_id]

            def get_candidate(candidate_id, actor):
                owner = "alice" if candidate_id == 1 else "bob"
                if actor.role != "admin" and actor.principal_id != owner:
                    raise LookupError("not found")
                return {"id": candidate_id, "owner": owner, "text": f"{owner} private text"}

            def approve(candidate_id, *, actor, reason, shared_text=None):
                if candidate_id == 2 and actor.role != "admin":
                    raise LookupError("not found")
                if actor.role == "read-only":
                    raise PermissionError("write access required")
                return {"status": "promoted", "candidate_id": candidate_id, "target_memory_id": 99}

            def reject(candidate_id, *, actor, reason):
                if candidate_id == 2 and actor.role != "admin":
                    raise LookupError("not found")
                return {"id": candidate_id, "owner": actor.principal_id, "status": "rejected"}

            service.list_candidates.side_effect = list_candidates
            service.get_candidate.side_effect = get_candidate
            service.approve_candidate.side_effect = approve
            service.reject_candidate.side_effect = reject
            app_module.promotion_service = service
            yield (
                TestClient(app_module.app),
                app_module,
                store,
                alice,
                bob,
                service,
            )


def _headers(key):
    return {"X-API-Key": key}


def test_collaborator_lists_only_own_candidates_and_admin_lists_all(promotion_api):
    client, _, _, alice, _, _ = promotion_api
    own = client.get("/promotions?project_id=fplguru", headers=_headers(alice["key"]))
    assert own.status_code == 200
    assert {item["owner"] for item in own.json()["promotions"]} == {"alice"}

    admin = client.get("/promotions?project_id=fplguru", headers=_headers("admin-key"))
    assert {item["owner"] for item in admin.json()["promotions"]} == {"alice", "bob"}


def test_collaborator_cannot_get_or_decide_another_owner_candidate(promotion_api):
    client, _, _, alice, _, _ = promotion_api
    assert client.get("/promotions/2", headers=_headers(alice["key"])).status_code == 404
    response = client.post(
        "/promotions/2/approve",
        headers=_headers(alice["key"]),
        json={"reason": "not mine", "shared_text": "safe project text"},
    )
    assert response.status_code == 404


def test_revocation_between_get_and_decision_is_rejected(promotion_api):
    client, _, store, alice, _, service = promotion_api
    assert client.get("/promotions/1", headers=_headers(alice["key"])).status_code == 200
    store.revoke(alice["id"])
    response = client.post(
        "/promotions/1/reject",
        headers=_headers(alice["key"]),
        json={"reason": "dismiss"},
    )
    assert response.status_code == 401
    service.reject_candidate.assert_not_called()


def test_manual_decision_audit_contains_reason_but_not_private_text(promotion_api):
    client, app_module, _, alice, _, _ = promotion_api
    response = client.post(
        "/promotions/1/approve",
        headers=_headers(alice["key"]),
        json={"reason": "confirmed by owner", "shared_text": "safe project text"},
    )
    assert response.status_code == 200
    entry = app_module.audit_log.query(action="promotion.approved")[0]
    assert entry["metadata"]["reason"] == "confirmed by owner"
    assert "alice private text" not in str(entry)
    assert "safe project text" not in str(entry)


def test_spoofed_fields_and_oversized_reasons_are_rejected(promotion_api):
    client, _, _, alice, _, service = promotion_api
    spoofed = client.post(
        "/promotions/1/reject",
        headers=_headers(alice["key"]),
        json={"reason": "dismiss", "owner": "bob"},
    )
    assert spoofed.status_code == 422
    too_long = client.post(
        "/promotions/1/reject",
        headers=_headers(alice["key"]),
        json={"reason": "x" * 2001},
    )
    assert too_long.status_code == 422
    service.reject_candidate.assert_not_called()
