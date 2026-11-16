"""Gate G6 (task P6-1): sort the Python tests into groups, and find the check on the Rust side.

Each test function of `tests/` goes into one group:

- http: the test sends an HTTP request to the Python app. The Rust server needs
  a check for the same behavior: a golden case of the parity kit, or an entry
  in `parity/g6_checks.json` (a Rust test, a parity script, or a decision record).
- client: the test checks a client (CLI, hooks, installer, MCP server, OpenCode
  plugin) with a fake server. The client does not change. Gate G6 runs each
  client against the Rust server (`parity/clients.py`).
- tool: the test checks a tool that stays after the cutover (parity kit, eval,
  scripts, benchmarks).
- internals: the test calls Python server code with no HTTP request. The reason
  names the Python module.

For an http test, the script finds each route that the test calls (also through
helper functions of the same file), and each status code that the test asserts.
A status has a check if a golden case, or a probe of `parity/clients.py`, has
that status on one of the routes of the test. A status with no such check needs
an entry in `parity/g6_checks.json`.

Usage:
  python -m parity.sort_tests --write   # write docs/rust-rewrite/gates/G6-tests.tsv
  python -m parity.sort_tests --check   # exit 1 if the file is old or a pair has no check
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
OUT = ROOT / "docs/rust-rewrite/gates/G6-tests.tsv"
CHECKS = Path(__file__).parent / "g6_checks.json"
GOLDEN = Path(__file__).parent / "golden/golden.ndjson"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

CLIENT_FILES = re.compile(
    r"test_(cli_.*|claude_memory_hooks|codex_.*|opencode_plugin|installer|mcp_server_stdio|client_sdk|"
    r"worktree_project|project_memory|playbook_gate)\.py$")
TOOL_FILES = re.compile(
    r"test_(parity_kit|g3|benchmarks|longmemeval|run_longmemeval|active_search_eval|run_active_search_eval|"
    r"reembed_script|shadow_runner|signal_filter|transcript_hygiene|qdrant_cluster_compose|container_config)\.py$")
SERVER_MODULES = {p.stem for p in ROOT.glob("*.py")} - {"conftest", "setup"}


def routes():
    """(method, template, regex) for each route of app.py."""
    out = []
    for m in re.finditer(r'^@app\.(get|post|put|patch|delete)\("([^"]+)"', (ROOT / "app.py").read_text(), re.MULTILINE):
        method, tpl = m.group(1).upper(), m.group(2)
        rx = re.sub(r"\\\{[^}]+:path\\\}", ".+", re.escape(tpl))
        rx = re.sub(r"\\\{[^}]+\\\}", "[^/]+", rx)
        out.append((method, tpl, re.compile(rx + "$")))
    return out


def probe_pairs(table):
    """(route, status) -> probe names, from the probes of parity/clients.py."""
    from parity.clients import PROBES
    pairs = {}
    for name, _key, method, path, _body in PROBES:
        status = re.search(r"-(\d{3})(-|$)", name)
        route = _match(method, re.sub(r"\{[a-z]+\}", "{x}", path.split("?")[0]), table)
        if status and route:
            pairs.setdefault((route, int(status.group(1))), []).append(f"probe {name}")
    return pairs


def golden_pairs():
    pairs = {}
    for line in GOLDEN.read_text().splitlines():
        case = json.loads(line)
        pairs.setdefault((case["route"], case["response"]["status"]), []).append(case["name"])
    return pairs


def _path_of(node):
    """The literal path of a request call, with each f-string field as {x}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{x}" for v in node.values)
    return None


def _requests(fn):
    """(method, path) of each client request in a function, and each name it calls.

    A path in a variable is found if the function sets the variable to a string.
    A receiver whose name has "client" or is "tc" with any other path gives the path "<dynamic>".
    """
    consts = {}  # variable name -> the paths that the function gives it
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if _path_of(node.value):
                consts[node.targets[0].id] = [_path_of(node.value)]
        elif isinstance(node, ast.For) and isinstance(node.iter, (ast.List, ast.Tuple)):
            target = node.target.elts[0] if isinstance(node.target, ast.Tuple) else node.target
            items = [e.elts[0] if isinstance(e, ast.Tuple) else e for e in node.iter.elts]
            if isinstance(target, ast.Name) and items and all(_path_of(i) for i in items):
                consts[target.id] = [_path_of(i) for i in items]
    reqs, calls = [], set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            calls.add(f.id)
        if not isinstance(f, ast.Attribute):
            continue
        if isinstance(f.value, ast.Name) and f.value.id == "self":
            calls.add(f.attr)
        method, args = f.attr.lower(), node.args
        if method == "request" and len(args) >= 2 and isinstance(args[0], ast.Constant):
            method, args = str(args[0].value).lower(), args[1:]
        if method in HTTP_METHODS and args:
            paths = [_path_of(args[0])] if _path_of(args[0]) else (
                consts.get(args[0].id, []) if isinstance(args[0], ast.Name) else [])
            receiver = f.value.id if isinstance(f.value, ast.Name) else (
                f.value.attr if isinstance(f.value, ast.Attribute) else "")
            reqs += [(method.upper(), p.split("?")[0]) for p in paths if p.startswith("/")]
            if not paths and ("client" in receiver or receiver == "tc"):
                reqs.append((method.upper(), "<dynamic>"))
    return reqs, calls


def _statuses(fn):
    """Each status assertion as a tuple of the statuses that it accepts.

    `status_code == 403` gives (403,), `status_code in (200, 202)` gives (200, 202).
    `!=` and `not in` assert no status, so they give nothing.
    """
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare) or not all(isinstance(o, (ast.Eq, ast.In)) for o in node.ops):
            continue
        sides = [node.left, *node.comparators]
        if not any(isinstance(s, ast.Attribute) and s.attr == "status_code" for s in sides):
            continue
        codes = tuple(sorted({c.value for s in sides for c in ast.walk(s)
                              if isinstance(c, ast.Constant) and type(c.value) is int and 100 <= c.value < 600}))
        if codes:
            out.add(codes)
    return out


def _match(method, path, table):
    probe = re.sub(r"\{x\}", "X1", path)
    for m, tpl, rx in table:
        if m == method and rx.match(probe):
            return f"{m} {tpl}"
    return None


def _imports(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return sorted(names & SERVER_MODULES)


def sort_file(path, table):
    tree = ast.parse(path.read_text())
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    info = {name: _requests(fn) for name, fn in funcs.items()}
    modules = _imports(tree)
    rows = []
    for cls_name, fn in _tests(tree):
        reqs, seen, todo = list(info[fn.name][0]), {fn.name}, list(info[fn.name][1])
        while todo:  # helpers of the same file, at any depth
            name = todo.pop()
            if name in funcs and name not in seen:
                seen.add(name)
                reqs += info[name][0]
                todo += info[name][1]
        test_id = f"{path.name}::{cls_name + '::' if cls_name else ''}{fn.name}"
        found = sorted({r for r in (_match(m, p, table) for m, p in reqs) if r})
        unknown = sorted({f"{m} {p}" for m, p in reqs if not _match(m, p, table)})
        if CLIENT_FILES.search(path.name):
            group, reason = "client", "tests a client with a fake server; parity/clients.py runs the client live"
        elif TOOL_FILES.search(path.name):
            group, reason = "tool", "tests a tool that stays after the cutover"
        elif reqs:
            group, reason = "http", ""
        elif path.name == "test_web_ui.py":
            group, reason = "client", "checks the web UI files, which the Rust server serves unchanged (UI_DIR)"
        else:
            group = "internals"
            reason = "calls Python code with no HTTP request: " + (", ".join(f"{m}.py" for m in modules) or "test helpers")
        rows.append({"test": test_id, "group": group, "routes": found, "unmatched": unknown,
                     "statuses": sorted(_statuses(fn)) if group == "http" else [], "reason": reason})
    return rows


def _tests(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield "", node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    yield node.name, sub


def build():
    table = routes()
    golden = golden_pairs()
    for pair, names in probe_pairs(table).items():
        golden.setdefault(pair, []).extend(names)
    checks = json.loads(CHECKS.read_text()) if CHECKS.exists() else {}
    rows, missing = [], []
    for path in sorted(TESTS.glob("test_*.py")):
        for row in sort_file(path, table):
            if row["group"] == "http":
                # A test often calls more routes than the one it checks (for example, an add
                # before a delete), so a status needs a golden case on one of its routes.
                notes, cases = [], 0
                for req in row["unmatched"]:
                    key = f"UNMATCHED {req}"
                    # A check key that ends in "/" covers each path under it.
                    done = [k for k in checks if k == key or (k.endswith("/") and key.startswith(k))]
                    if done:
                        notes.append(f"{key}: {checks[done[0]]}")
                    else:
                        notes.append(f"{key}: NO CHECK")
                        missing.append((row["test"], key))
                for accepted in row["statuses"] or [None]:
                    hits = [n for r in row["routes"] for (gr, gs), names in golden.items()
                            if gr == r and (accepted is None or gs in accepted) for n in names]
                    cases += len(hits)
                    if hits:
                        continue
                    keys = [f"{r} {status}" for r in row["routes"] for status in accepted]
                    if not keys:  # no known route: the UNMATCHED entries above cover the test
                        continue
                    done = [f"{k}: {checks[k]}" for k in keys if k in checks]
                    if done:
                        notes += done
                    else:
                        notes.append(f"{' | '.join(keys)}: NO CHECK")
                        missing.append((row["test"], " | ".join(keys)))
                row["reason"] = f"golden cases and probes: {cases}" + ("; " + "; ".join(notes) if notes else "")
            rows.append(row)
    return rows, missing


def render(rows):
    lines = ["test\tgroup\troutes\tstatuses\tcheck or reason"]
    for r in rows:
        lines.append("\t".join([r["test"], r["group"], ", ".join(r["routes"] + r["unmatched"]),
                                ", ".join("|".join(map(str, t)) for t in r["statuses"]), r["reason"]]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    rows, missing = build()
    text = render(rows)
    counts = {}
    for r in rows:
        counts[r["group"]] = counts.get(r["group"], 0) + 1
    print(f"{len(rows)} tests: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for test, key in missing:
        print(f"NO CHECK: {key} ({test})")
    if args.write:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(ROOT)}")
        return 1 if missing else 0
    if OUT.read_text() != text:
        print(f"{OUT.relative_to(ROOT)} is old: run python -m parity.sort_tests --write")
        return 1
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
