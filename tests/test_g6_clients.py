"""Self-test of the gate G6 tools: parity/clients.py (compare) and parity/sort_tests.py."""

import ast

from parity import clients, sort_tests


def rec(step, out, rc=0, suite="cli", seconds=0.1, limit=None, expect_rc=0):
    return {"suite": suite, "step": step, "rc": rc, "out": out, "seconds": seconds,
            "limit": limit, "expect_rc": expect_rc}


def verdicts(ref, new):
    rows, failures = clients.compare(ref, new)
    return {r[1]: r[2] for r in rows}, failures


def test_same_output_after_masks_passes():
    ref = [rec("get", {"ok": True, "data": {"id": 1, "created_at": "2026-01-01T00:00:00Z"}})]
    new = [rec("get", {"ok": True, "data": {"id": 1, "created_at": "2026-11-16T15:00:00Z"}})]
    assert verdicts(ref, new) == ({"get": "same"}, 0)


def test_different_output_fails_and_known_difference_does_not():
    ref = [rec("get", {"id": 1}), rec("health", {"version": "5.19.0"})]
    new = [rec("get", {"id": 2}), rec("health", {"version": "6.0.0"})]
    v, failures = verdicts(ref, new)
    assert v == {"get": "FAIL", "health": "known"} and failures == 1


def test_exit_code_time_limit_and_missing_step_fail():
    ref = [rec("a", "x"), rec("h", "", suite="hooks", limit=1), rec("gone", "x")]
    new = [rec("a", "x", rc=1), rec("h", "", suite="hooks", limit=1, seconds=1.5)]
    v, failures = verdicts(ref, new)
    assert v == {"a": "FAIL", "h": "FAIL", "gone": "FAIL"} and failures == 3


def test_must_text_and_probe_status():
    ref = [rec("claude-code:SessionStart:memory-recall.sh", "nothing", suite="hooks"),
           rec("audit-403", {"detail": "x"}, rc=200, suite="probes", expect_rc=None)]
    new = [dict(r) for r in ref]
    v, failures = verdicts(ref, new)
    assert set(v.values()) == {"FAIL"} and failures == 2


def test_sort_statuses_reads_eq_and_in_only():
    fn = ast.parse(
        "def t(r):\n"
        "    assert r.status_code == 403\n"
        "    assert r.status_code in (200, 202)\n"
        "    assert r.status_code != 500\n"
    ).body[0]
    assert sort_tests._statuses(fn) == {(403,), (200, 202)}


def test_sort_finds_paths_in_variables_and_loops():
    fn = ast.parse(
        "def t(client):\n"
        "    url = f'/memory/{1}'\n"
        "    client.get(url)\n"
        "    for path, body in [('/search', {}), ('/search/evidence', {})]:\n"
        "        client.post(path, json=body)\n"
        "    client.post(make())\n"
    ).body[0]
    reqs, _ = sort_tests._requests(fn)
    assert sorted(reqs) == [("GET", "/memory/{x}"), ("POST", "/search"), ("POST", "/search/evidence"),
                            ("POST", "<dynamic>")]


def test_sort_matches_path_routes():
    table = sort_tests.routes()
    assert sort_tests._match("PUT", "/extraction/profiles/claude-code/memories/", table) == \
        "PUT /extraction/profiles/{prefix:path}"
    assert sort_tests._match("GET", "/memory/{x}", table) == "GET /memory/{memory_id}"


def test_each_probe_name_holds_its_status():
    for name, *_ in clients.PROBES:
        assert sort_tests.re.search(r"-\d{3}(-|$)", name), name


def test_known_difference_accepts_only_its_own_keys():
    ref = [rec("health", {"data": {"status": "ok", "version": "5.19.0"}})]
    new = [rec("health", {"data": {"status": "down", "version": "6.0.0"}})]
    assert verdicts(ref, new) == ({"health": "FAIL"}, 1)
    new = [rec("health", {"data": {"status": "ok", "version": "6.0.0"}}, rc=1)]
    assert verdicts(ref, new)[1] == 1


def test_sort_keeps_methods_of_two_classes_apart(tmp_path):
    f = tmp_path / "test_x.py"
    f.write_text(
        "class TestA:\n"
        "    def test_same(self, tc):\n"
        "        assert tc.patch('/memory/1').status_code == 200\n"
        "class TestB:\n"
        "    def test_same(self, engine):\n"
        "        engine.merge()\n"
    )
    rows = sort_tests.sort_file(f, sort_tests.routes())
    assert [(r["test"], r["group"]) for r in rows] == [
        ("test_x.py::TestA::test_same", "http"), ("test_x.py::TestB::test_same", "internals")]
