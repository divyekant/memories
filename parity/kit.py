"""Core of the parity kit: run cases against a server, normalize replies, diff them."""

import json
import re
import time
from pathlib import Path

import httpx

from parity_capture import REDACTED, decode_body

GOLDEN_PATH = Path(__file__).parent / "golden" / "golden.ndjson"
ADMIN_KEY_DEFAULT = "parity-admin-key"
TOLERANCE = 1e-4

# Keys whose value changes on each run, by exact name. Each key that ends in `_at` is a time
# and is also masked. The diff keeps the key and the value type. Add a key here only with a reason.
VOLATILE_KEYS = frozenset({
    "timestamp", "ts", "created",                      # times
    "job_id", "key_prefix",                            # random IDs
    "backup_path", "backup_location",                  # paths in the data directory
    "uptime_sec", "index_size_bytes",                  # process and file state
    "avg_latency_ms", "max_latency_ms", "p95_latency_ms", "last_reload_duration_ms",
    "rss_kb", "last_rss_kb", "rss_anon_kb", "rss_file_kb", "rss_high_water_kb", "vmsize_kb",
    "trim", "gc_collected", "last_gc_collected", "last_trim_reason",  # Python memory workarounds
})


def _volatile_key(key):
    return key in VOLATILE_KEYS or key.endswith("_at")


# Values inside strings that change on each run.
# Keep the ID rules first. A hex ID can hold 14 digits in a row, which the compact timestamp rule also matches.
VOLATILE_TEXT = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<uuid>"),
    (re.compile(r"\b(mem_)?[0-9a-f]{32}\b"), "<hex>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\d{8}[_T-]?\d{6}(_\d+)?"), "<ts>"),
]
_VAR = re.compile(r"\{\{([A-Za-z0-9_:.-]+)\}\}")


# -- files --------------------------------------------------------------------

def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def dump(records, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


# -- normalize and diff -------------------------------------------------------

def _type_name(v):
    return "null" if v is None else type(v).__name__


# (list key, item key) pairs whose value changes on each run, for example the event IDs come from the clock.
VOLATILE_ITEM_KEYS = {("events", "id")}


def _text(value):
    for pattern, repl in VOLATILE_TEXT:
        value = pattern.sub(repl, value)
    return value


def normalize(value, key=None, parent=None, extra=()):
    """Mask the values that change on each run. The result keeps each key and the type of each value.

    `extra` holds more key names to mask, from the `volatile` field of a case.
    """
    if key is not None and (_volatile_key(key) or (parent, key) in VOLATILE_ITEM_KEYS or key in extra):
        if value is None or (isinstance(value, str) and value.startswith("<volatile:")):
            return value
        return f"<volatile:{_type_name(value)}>"
    if isinstance(value, dict):
        return {_text(k): normalize(v, k, parent, extra) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, parent=key, extra=extra) for v in value]
    if isinstance(value, str):
        return _text(value)
    return value


def diff_values(a, b, path="$"):
    """Return a list of differences. Two floats compare with TOLERANCE. An int and a float differ."""
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a == b and type(a) is type(b) else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, float) and isinstance(b, float):
        # 1e-12: 0.8493 - 0.8492 is 1.0000000000000009e-04 in floating point.
        return [] if abs(a - b) <= TOLERANCE + 1e-12 else [f"{path}: {a!r} != {b!r}"]
    if type(a) is not type(b):
        return [f"{path}: type {_type_name(a)} != {_type_name(b)}"]
    if isinstance(a, dict):
        out = [f"{path}.{k}: missing on the right" for k in a.keys() - b.keys()]
        out += [f"{path}.{k}: missing on the left" for k in b.keys() - a.keys()]
        for k in sorted(a.keys() & b.keys()):
            out += diff_values(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        out = [] if len(a) == len(b) else [f"{path}: length {len(a)} != {len(b)}"]
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff_values(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _media_type(ct):
    return (ct or "").split(";")[0].strip().lower()


def diff_captures(left, right):
    """Compare two capture lists case by case. The right side is the server under test."""
    problems = []
    if len(left) != len(right):
        problems.append({"name": "<file>", "problems": [f"case count {len(left)} != {len(right)}"]})
    for a, b in zip(left, right):
        name = a.get("name") or f"{a['request']['method']} {a['request']['path']}"
        found = []
        if a.get("name") != b.get("name"):
            found.append(f"case name {a.get('name')!r} != {b.get('name')!r}")
        ra, rb = a["response"], b["response"]
        if ra["status"] != rb["status"]:
            found.append(f"status {ra['status']} != {rb['status']}")
        if _media_type(ra.get("content_type")) != _media_type(rb.get("content_type")):
            found.append(f"content type {ra.get('content_type')!r} != {rb.get('content_type')!r}")
        if ra.get("body_type") != rb.get("body_type"):
            found.append(f"body type {ra.get('body_type')} != {rb.get('body_type')}")
        extra = a.get("volatile", ())
        found += diff_values(normalize(ra.get("body"), extra=extra), normalize(rb.get("body"), extra=extra), "body")
        max_ms = a.get("max_ms")
        if max_ms and b.get("elapsed_ms", 0) > max_ms:
            found.append(f"elapsed {b['elapsed_ms']} ms > {max_ms} ms")
        if found:
            problems.append({"name": name, "problems": found})
    return problems


# -- run cases ----------------------------------------------------------------

def get_path(obj, dotted):
    """Read `a.0.b` from a JSON value. A `:basename` suffix keeps the last part of a file path."""
    dotted, _, op = dotted.partition(":")
    for part in dotted.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return str(obj).rstrip("/").rsplit("/", 1)[-1] if op == "basename" else obj


def _var(vars_, name):
    """`ids.3` reads item 3 of the variable `ids`."""
    head, _, rest = name.partition(".")
    return get_path(vars_[head], rest) if rest else vars_[head]


def substitute(obj, vars_):
    if isinstance(obj, dict):
        return {k: substitute(v, vars_) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, vars_) for v in obj]
    if isinstance(obj, str):
        whole = _VAR.fullmatch(obj)
        if whole:
            return _var(vars_, whole.group(1))
        return _VAR.sub(lambda m: str(_var(vars_, m.group(1))), obj)
    return obj


def _send(client, req):
    try:
        return _send_once(client, req)
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
        # Uvicorn closes the connection after an error that no handler catches. The next
        # request on that kept-alive connection was not read (the server answers it with a
        # close or a reset), so send it again once on a new connection.
        return _send_once(client, req)


def _send_once(client, req):
    kwargs = {"headers": req.get("headers") or {}}
    if "json" in req:
        kwargs["json"] = req["json"]
    elif "body" in req:
        kwargs["content"] = req["body"].encode("utf-8")
    url = req["path"] + (f"?{req['query']}" if req.get("query") else "")
    start = time.perf_counter()
    with client.stream(req["method"], url, **kwargs) as r:
        ct = r.headers.get("content-type", "")
        if ct.startswith("text/event-stream"):
            raw = next(r.iter_raw(), b"")  # an SSE reply never ends: keep the first chunk
        else:
            raw = r.read()
        elapsed = (time.perf_counter() - start) * 1000.0
    body_type, body = decode_body(raw, ct)
    return {"status": r.status_code, "content_type": ct, "body_type": body_type, "body": body}, elapsed


def _run_case(client, case, vars_, admin_key):
    req = substitute(case["request"], vars_)
    headers = req.get("headers") or {}
    for name in list(headers):
        if headers[name] == REDACTED:
            headers[name] = admin_key  # shortcut: a middleware capture keeps no key, so the replay is admin
    until = case.get("until")
    deadline = time.monotonic() + (until or {}).get("timeout_s", 0)
    while True:
        resp, elapsed = _send(client, req)
        if not until or time.monotonic() > deadline:
            return resp, elapsed
        try:
            if get_path(resp["body"], until["path"]) in until["in"]:
                return resp, elapsed
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        time.sleep(0.2)


class NotEmptyError(RuntimeError):
    pass


def select(cases, names):
    """The cases with these names, in file order. `None` keeps all. An unknown name is an error."""
    if names is None:
        return cases
    unknown = set(names) - {c.get("name") for c in cases}
    if unknown:
        raise ValueError(f"no case with the name(s): {', '.join(sorted(unknown))}")
    return [c for c in cases if c.get("name") in names]


def run(cases, base_url, admin_key=ADMIN_KEY_DEFAULT, timeout=120.0, log=None, allow_nonempty=False,
        extra_vars=None, vars_out=None):
    """Send each case in order. Return capture records with normalized replies.

    The scenario deletes records, so a server that holds records is refused unless allow_nonempty is set.
    `extra_vars` sets variables that a case before the selected cases would save.
    `vars_out` (a dict) gets the variables at the end, so a later run can use them.
    """
    vars_ = {"key:admin": admin_key, **(extra_vars or {})}
    out = []
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        if not allow_nonempty:
            r = client.get("/memories/count", headers={"X-API-Key": admin_key})
            count = r.json().get("count") if r.status_code == 200 else None
            if count != 0:
                detail = f"holds {count} record(s)" if r.status_code == 200 else f"replied {r.status_code} to the count"
                raise NotEmptyError(f"the server {detail}; use a new, empty server and the right admin key")
        for case in cases:
            if case.get("delay_s"):
                time.sleep(case["delay_s"])
            try:
                resp, elapsed = _run_case(client, case, vars_, admin_key)
            except Exception as e:  # a server under test that fails must give a difference, not stop the run
                resp, elapsed = {"status": 0, "content_type": "", "body_type": "error", "body": repr(e)}, 0.0
            for name, dotted in (case.get("save") or {}).items():
                try:
                    vars_[name] = get_path(resp["body"], dotted)
                except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                    vars_[name] = None  # later cases that use it fail and show in the diff
                    if log:
                        log(f"WARN {case.get('name')}: no value at {dotted!r} for {name!r}")
            rec = {k: v for k, v in case.items() if k not in ("response", "elapsed_ms", "captured_at")}
            rec["response"] = {**resp, "body": normalize(resp["body"], extra=case.get("volatile", ()))}
            rec["elapsed_ms"] = round(elapsed, 1)
            out.append(rec)
            if log:
                log(f"{resp['status']} {case.get('name', case['request']['path'])}")
    if vars_out is not None:
        vars_out.update(vars_)
    return out
