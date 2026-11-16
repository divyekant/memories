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

# Keys whose value changes on each run. The diff keeps the key and the value type.
VOLATILE_KEY = re.compile(
    r"(_at|_ms|_sec|_seconds|_bytes|_kb|_mb|_path|_location)$"
    r"|^(timestamp|created|ts|trim|gc_collected|last_gc_collected|last_trim_reason|job_id|key_prefix|uptime.*|elapsed.*|duration.*|latency.*|rss.*)$"
)
# Values inside strings that change on each run.
VOLATILE_TEXT = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\d{8}[_T-]?\d{6}(_\d+)?"), "<ts>"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<uuid>"),
    (re.compile(r"\b(mem_)?[0-9a-f]{32}\b"), "<hex>"),
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
    if key is not None and (VOLATILE_KEY.search(key) or (parent, key) in VOLATILE_ITEM_KEYS or key in extra):
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
    """Return a list of differences. Floats compare with TOLERANCE."""
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a == b and type(a) is type(b) else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            return [] if abs(a - b) <= TOLERANCE else [f"{path}: {a!r} != {b!r}"]
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
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
    return obj.rstrip("/").rsplit("/", 1)[-1] if op == "basename" else obj


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


class NotEmptyError(RuntimeError):
    pass


def run(cases, base_url, admin_key=ADMIN_KEY_DEFAULT, timeout=120.0, log=None, allow_nonempty=False):
    """Send each case in order. Return capture records with normalized replies.

    The scenario deletes records, so a server that holds records is refused unless allow_nonempty is set.
    """
    vars_ = {"key:admin": admin_key}
    out = []
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        if not allow_nonempty:
            count = client.get("/memories/count", headers={"X-API-Key": admin_key}).json().get("count")
            if count != 0:
                raise NotEmptyError(f"the server holds {count} record(s); use a new, empty server")
        for case in cases:
            req = substitute(case["request"], vars_)
            headers = req.get("headers") or {}
            for name in list(headers):
                if headers[name] == REDACTED:
                    headers[name] = admin_key  # a capture from the middleware has no key
            until = case.get("until")
            deadline = time.monotonic() + (until or {}).get("timeout_s", 0)
            while True:
                resp, elapsed = _send(client, req)
                if not until or time.monotonic() > deadline:
                    break
                try:
                    if get_path(resp["body"], until["path"]) in until["in"]:
                        break
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
                time.sleep(0.2)
            for name, dotted in (case.get("save") or {}).items():
                try:
                    vars_[name] = get_path(resp["body"], dotted)
                except (KeyError, IndexError, TypeError, ValueError):
                    vars_[name] = None  # later cases that use it fail and show in the diff
                    if log:
                        log(f"WARN {case.get('name')}: no value at {dotted!r} for {name!r}")
            rec = {k: v for k, v in case.items() if k not in ("response", "elapsed_ms", "captured_at")}
            rec["response"] = {**resp, "body": normalize(resp["body"], extra=case.get("volatile", ()))}
            rec["elapsed_ms"] = round(elapsed, 1)
            out.append(rec)
            if log:
                log(f"{resp['status']} {case.get('name', req['path'])}")
    return out
