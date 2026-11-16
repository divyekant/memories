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
            "avg_latency_ms": 12.5, "text": "stays", "id": 3, "updated_at": None, "check_sec": 15}
    out = kit.normalize(body)
    assert out["text"] == "stays" and out["id"] == 3
    assert out["created_at"] == "<volatile:str>"
    assert out["job_id"] == "<volatile:str>"
    assert out["avg_latency_ms"] == "<volatile:float>"
    assert out["updated_at"] is None
    assert out["check_sec"] == 15  # a config value is not masked


def test_normalize_masks_timestamps_and_ids_inside_strings():
    s = "manual_20261116_070945 at 2026-11-16T07:09:45.123+00:00 key 6f1c2d3e-0a1b-4c2d-9e8f-001122334455"
    out = kit.normalize(s)
    assert "20261116" not in out and "2026-11-16" not in out and "6f1c2d3e" not in out


def test_normalize_masks_each_hex_id_whole():
    # A hex ID with 14 digits in a row looked like a compact timestamp, and the diff failed at random.
    import random
    rng = random.Random(7)
    ids = ["ab12345678901234cdef0123456789ab", "mem_0f2026111607094500aabbccddeeff00"]
    ids += ["%032x" % rng.getrandbits(128) for _ in range(5000)]
    for value in ids:
        assert kit.normalize("key " + value + " made") == "key <hex> made", value
    assert kit.normalize("6f1c2d3e-0a1b-4c2d-9e8f-202611160709") == "<uuid>"


def test_diff_float_tolerance():
    a = {"score": 0.50001}
    b = {"score": 0.50005}
    assert kit.diff_values(a, b) == []
    assert kit.diff_values({"score": 0.5}, {"score": 0.6})


def test_diff_reports_type_and_key_differences():
    assert kit.diff_values({"a": 1}, {"a": 1.0})
    assert kit.diff_values({"a": True}, {"a": 1})
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


def test_run_records_a_dead_server_as_a_difference():
    case = {"name": "x", "route": "GET /health", "request": {"method": "GET", "path": "/health"}}
    recs = kit.run([case], "http://127.0.0.1:9", allow_nonempty=True, timeout=2)
    assert recs[0]["response"]["status"] == 0 and recs[0]["response"]["body_type"] == "error"


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
    from parity_capture import ParityCaptureMiddleware

    client = _client({})
    mw = ParityCaptureMiddleware(client.app, str(tmp_path))
    r = TestClient(mw).post("/memory/add", json={"text": "t", "source": "s/x"},
                            headers={"X-API-Key": "test-key", "X-Memories-Client": "codex"})
    assert r.status_code == 200
    mw.flush()
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


def test_middleware_redacts_a_new_raw_key_and_keeps_the_daily_cap(tmp_path):
    from datetime import datetime, timezone
    from parity_capture import ParityCaptureMiddleware

    mw = ParityCaptureMiddleware(None, str(tmp_path), max_mb_per_day=0.001)
    now = datetime.now(timezone.utc)
    body = json.dumps({"id": "k1", "key": "mem_" + "a" * 32}).encode()
    mw._write("POST", "/api/keys", b"", [], b"", False, 200, "application/json", body, False, 1.0, now)
    text = next(tmp_path.glob("capture-*.ndjson")).read_text()
    assert "mem_" not in text and kit.REDACTED in text
    big = json.dumps({"text": "x" * 2000}).encode()
    mw._write("POST", "/search", b"", [], b"", False, 200, "application/json", big, False, 1.0, now)
    assert mw.dropped == 1


# -- subset replay (Rust task exit checks) --------------------------------------

def test_select_keeps_file_order_and_refuses_unknown_names():
    cases = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert kit.select(cases, None) is cases
    assert [c["name"] for c in kit.select(cases, ["c", "a"])] == ["a", "c"]
    with pytest.raises(ValueError):
        kit.select(cases, ["a", "x"])


def test_replay_var_sets_a_saved_variable():
    seen = {}

    def fake_run(cases, base_url, **kw):
        seen.update(kw["extra_vars"])
        return []

    with patch.object(kit, "run", fake_run), patch.object(kit, "dump"):
        from parity.__main__ import main
        assert main(["replay", "--base-url", "http://x", "--out", "/dev/null",
                     "--only", "keys-me-ro", "--var", "key:ro=mem_1"]) == 0
    assert seen == {"key:ro": "mem_1"}


def test_run_gives_the_saved_variables_to_vars_out():
    case = {"name": "x", "request": {"method": "GET", "path": "/health"}, "save": {"v": "version"}}
    with patch.object(kit, "_run_case", lambda *a: ({"status": 200, "body": {"version": "9"}}, 1.0)):
        out = {}
        kit.run([case], "http://x", allow_nonempty=True, extra_vars={"a": 1}, vars_out=out)
    assert out == {"key:admin": kit.ADMIN_KEY_DEFAULT, "a": 1, "v": "9"}


def test_replay_saves_and_loads_variables(tmp_path):
    saved = tmp_path / "vars.json"
    seen = {}

    def fake_run(cases, base_url, **kw):
        seen.update(kw["extra_vars"])
        if kw.get("vars_out") is not None:
            kw["vars_out"].update({"seed0": [0, 1], **kw["extra_vars"]})
        return []

    from parity.__main__ import main
    with patch.object(kit, "run", fake_run), patch.object(kit, "dump"):
        assert main(["replay", "--base-url", "http://x", "--out", "/dev/null", "--only", "health-minimal",
                     "--save-vars", str(saved)]) == 0
        assert json.loads(saved.read_text()) == {"seed0": [0, 1]}
        seen.clear()
        assert main(["replay", "--base-url", "http://x", "--out", "/dev/null", "--only", "health-minimal",
                     "--vars-file", str(saved), "--var", "key:ro=mem_1"]) == 0
    assert seen == {"seed0": [0, 1], "key:ro": "mem_1"}


def test_rust_route_table_matches_python_routes():
    """server/crates/memories-server/src/shell.rs ROUTES lists each route of app.py."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "server", "crates", "memories-server",
                            "src", "shell.rs"), encoding="utf-8").read()
    table = src.split("pub const ROUTES")[1].split("];")[0]
    pairs = re.findall(r'\("([A-Z]+)", "([^"]+)"\)', table)
    rust = {m + " " + re.sub(r"{[*](\w+)}", r"{\1:path}", p) for m, p in pairs}
    python = _app_routes() | {"GET /openapi.json", "GET /ui/static/{path:path}"}
    assert rust == python


# -- gate G4 corpus driver (task P4-4) ------------------------------------------

def _audn_prompt(similar):
    from llm_extract import AUDN_PROMPT
    facts = json.dumps([{"index": 0, "text": "A fact.", "category": "detail"}], separators=(",", ":"))
    return AUDN_PROMPT.format(facts_json=facts, similar_json=json.dumps(similar, separators=(",", ":")))


def test_corpus_cases_come_from_the_evals():
    from parity import extract_corpus as ec
    names = [c["name"] for c in ec.cases()]
    assert sum(n.startswith("corpus/") for n in names) == 20
    assert any(n.startswith("scenario/") for n in names)
    assert len(names) == len(set(names))


def test_corpus_decision_uses_the_tie_rule_and_spares_the_highest_id():
    from parity import extract_corpus as ec
    sims = [{"id": 9, "text": "x", "relevance": 0.016}, {"id": 4, "text": "y", "relevance": 0.016},
            {"id": 2, "text": "z", "relevance": 0.015}]
    assert ec.target(sims)["id"] == 4
    assert ec.target([]) is None
    prompt = _audn_prompt({"0": sims})
    assert ec.split_prompt(prompt)[1] == {"0": sims}
    assert "<similar:python>" in ec.split_prompt(prompt)[0]
    loose = prompt.replace(json.dumps({"0": sims}, separators=(",", ":")), json.dumps({"0": sims}, indent=2))
    assert "<similar:other>" in ec.split_prompt(loose)[0]
    ec.STATE["max_id"] = 4
    try:
        status, body = ec.reply("You are a memory manager. Output only valid JSON.", prompt)
        assert status == 200
        decision = json.loads(body)[0]
        assert decision["action"] != "DELETE"
        assert decision.get("old_id", decision.get("existing_id", 4)) == 4
    finally:
        ec.STATE["max_id"] = -1


def test_corpus_check_tolerates_search_order_but_not_other_changes():
    from parity import extract_corpus as ec
    sims = [{"id": i, "text": "t", "relevance": 0.01} for i in range(5)]
    base = {"name": "c", "enqueue": [202, {}], "prompts": [{"system": "s", "user": _audn_prompt({"0": sims})}],
            "job": {"status": "completed", "error": None,
                    "result": {"actions": [{"action": "add", "id": 1}], "tokens": {"audn": {"input": 3}}}}}
    swapped = copy.deepcopy(base)
    swapped["prompts"][0]["user"] = _audn_prompt({"0": sims[::-1]})
    swapped["job"]["result"]["tokens"]["audn"]["input"] = 4
    assert ec.check([swapped], [base], min_equal=0.0) == []
    assert ec.check([swapped], [base])  # 0 of 1 lists equal: below the tolerance
    changed = copy.deepcopy(base)
    changed["job"]["result"]["actions"][0]["id"] = 2
    assert ec.check([changed], [base])
    other_text = copy.deepcopy(base)
    other_text["prompts"][0]["system"] = "t"
    assert ec.check([other_text], [base])


def test_corpus_check_compares_similar_text_and_candidate_sources():
    from parity import extract_corpus as ec
    sims = [{"id": i, "text": f"t{i}", "relevance": 0.01} for i in range(3)]
    base = {"name": "c", "enqueue": [202, {}], "prompts": [{"system": "s", "user": _audn_prompt({"0": sims})}],
            "job": {"status": "completed", "error": None, "result": {
                "actions": [], "tokens": {"audn": {"input": 3}},
                "compaction_candidates": [{"fact_index": 0, "memory_ids": [0, 1], "avg_rrf_score": 0.01,
                                           "sources": ["a"], "cross_source": False}]}}}
    assert ec.check([copy.deepcopy(base)], [base]) == []
    text = copy.deepcopy(base)
    text["prompts"][0]["user"] = _audn_prompt({"0": [dict(sims[0], text="WRONG")] + sims[1:]})
    assert ec.check([text], [base])
    sources = copy.deepcopy(base)
    sources["job"]["result"]["compaction_candidates"][0]["sources"] = ["x"]
    assert ec.check([sources], [base])
    tokens = copy.deepcopy(base)
    tokens["job"]["result"]["tokens"]["audn"]["input"] = 4
    assert ec.check([tokens], [base])  # compared when the similar lists are equal
