"""Tests for the parity kit (parity/) and the capture middleware."""

import copy
import importlib
import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from parity import kit, scenario, seed

GOLDEN = kit.GOLDEN_PATH


# -- normalizer ---------------------------------------------------------------

def test_normalize_masks_volatile_keys_but_keeps_type():
    body = {"created_at": "2026-11-16T07:00:00+00:00", "job_id": "ab" * 16,
            "latency_ms": 12.5, "text": "stays", "id": 3, "updated_at": None}
    out = kit.normalize(body)
    assert out["text"] == "stays" and out["id"] == 3
    assert out["created_at"] == "<volatile:str>"
    assert out["job_id"] == "<volatile:str>"
    assert out["latency_ms"] == "<volatile:float>"
    assert out["updated_at"] is None


def test_normalize_masks_timestamps_and_ids_inside_strings():
    s = "manual_20261116_070945 at 2026-11-16T07:09:45.123+00:00 key 6f1c2d3e-0a1b-4c2d-9e8f-001122334455"
    out = kit.normalize(s)
    assert "20261116" not in out and "2026-11-16" not in out and "6f1c2d3e" not in out


def test_diff_float_tolerance():
    a = {"score": 0.50001}
    b = {"score": 0.50005}
    assert kit.diff_values(a, b) == []
    assert kit.diff_values({"score": 0.5}, {"score": 0.6})


def test_diff_reports_type_and_key_differences():
    assert kit.diff_values({"a": 1}, {"a": "1"})
    assert kit.diff_values({"a": 1}, {"a": 1, "b": 2})
    assert kit.diff_values([1, 2], [1])


# -- scenario coverage --------------------------------------------------------

def _app_routes():
    with patch.dict(os.environ, {"API_KEY": "k", "EXTRACT_PROVIDER": ""}):
        import app as app_module
        importlib.reload(app_module)
    out = set()
    for r in app_module.app.routes:
        methods = getattr(r, "methods", None)
        if not methods or r.path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            continue
        for m in methods - {"HEAD", "OPTIONS"}:
            out.add(f"{m} {r.path}")
    return out


def test_scenario_covers_every_route():
    routes = _app_routes()
    assert len(routes) == 93
    covered = {c["route"] for c in scenario.build()}
    assert routes - covered == set()


def test_scenario_covers_every_wire_rule():
    rules = {r for c in scenario.build() for r in c.get("rules", [])}
    assert set(scenario.WIRE_RULES) <= rules


def test_seed_is_deterministic_and_has_required_shapes():
    a, b = seed.build(), seed.build()
    assert a == b
    blob = json.dumps(a)
    for needle in ('"pinned": true', '"archived": true', "conflicts_with", "/link"):
        assert needle in blob
    sources = {m["source"].split("/")[0] for c in a if c["request"]["path"] == "/memory/add-batch"
               for m in c["request"]["json"]["memories"]}
    assert len(sources) >= 4


# -- golden file --------------------------------------------------------------

def test_golden_matches_scenario():
    golden = kit.load(GOLDEN)
    cases = scenario.build()
    assert [g["name"] for g in golden] == [c["name"] for c in cases]
    assert all("response" in g for g in golden)


def test_golden_holds_no_secret():
    text = GOLDEN.read_text()
    assert kit.ADMIN_KEY_DEFAULT not in text
    assert not re.search(r"mem_[A-Za-z0-9_-]{20,}", text)


def test_self_test_one_changed_field_fails_the_diff():
    golden = kit.load(GOLDEN)
    assert kit.diff_captures(golden, golden) == []
    changed = copy.deepcopy(golden)
    target = next(c for c in changed if c["name"] == "health-minimal")
    target["response"]["body"]["service"] = "other"
    problems = kit.diff_captures(golden, changed)
    assert len(problems) == 1 and problems[0]["name"] == "health-minimal"


def test_diff_fails_on_slow_reply_when_case_has_max_ms():
    golden = kit.load(GOLDEN)
    slow = copy.deepcopy(golden)
    case = next(c for c in slow if c.get("max_ms"))
    case["elapsed_ms"] = case["max_ms"] + 1
    assert any(p["name"] == case["name"] for p in kit.diff_captures(golden, slow))


# -- template substitution ----------------------------------------------------

def test_substitute_keeps_type_for_whole_value_and_formats_inside_strings():
    vars_ = {"id": 7, "key": "abc"}
    out = kit.substitute({"ids": ["{{id}}"], "path": "/memory/{{id}}", "k": "{{key}}"}, vars_)
    assert out == {"ids": [7], "path": "/memory/7", "k": "abc"}


# -- capture middleware -------------------------------------------------------

def _client(env):
    with patch.dict(os.environ, {"API_KEY": "test-key", "EXTRACT_PROVIDER": "", **env}):
        import app as app_module
        importlib.reload(app_module)
        mock_engine = MagicMock()
        mock_engine.stats_light.return_value = {"total_memories": 0, "dimension": 384, "model": "m"}
        mock_engine.add_memories.return_value = [1]
        app_module.memory = mock_engine
        return TestClient(app_module.app)


def test_middleware_off_by_default():
    os.environ.pop("PARITY_CAPTURE_DIR", None)
    client = _client({})
    names = [m.cls.__name__ for m in client.app.user_middleware]
    assert "ParityCaptureMiddleware" not in names


def test_middleware_records_request_and_reply_and_redacts_key(tmp_path):
    client = _client({"PARITY_CAPTURE_DIR": str(tmp_path)})
    r = client.post("/memory/add", json={"text": "t", "source": "s/x"},
                    headers={"X-API-Key": "test-key", "X-Memories-Client": "codex"})
    assert r.status_code == 200
    files = list(tmp_path.glob("capture-*.ndjson"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text().splitlines()[-1])
    assert rec["request"]["method"] == "POST" and rec["request"]["path"] == "/memory/add"
    assert rec["request"]["json"] == {"text": "t", "source": "s/x"}
    assert rec["request"]["headers"]["x-api-key"] == kit.REDACTED
    assert rec["request"]["headers"]["x-memories-client"] == "codex"
    assert rec["response"]["status"] == 200 and rec["response"]["body"]["id"] == 1
    assert "test-key" not in files[0].read_text()
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"
