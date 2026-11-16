"""Golden cases for the Rust rank core (task P2-1).

Calls the Python rank functions of v5.19.0 with fixed inputs and writes the
results to parity/golden/rank/*.json. The Rust tests in
server/crates/memories-engine read these files.

  python -m parity.rank_golden            write the files
  python -m parity.rank_golden --check    exit 1 if a file differs

Run it with Python 3.11, the version of the production image. The float sum
and datetime.fromisoformat of other versions give different results.
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import memory_engine
import query_intent
from auth_context import source_matches_prefixes
from memory_engine import MemoryEngine, annotate_relative_scores

OUT_DIR = Path(__file__).resolve().parent / "golden" / "rank"
NOW = datetime(2026, 6, 15, 12, 34, 56, 789012, tzinfo=timezone.utc)
SEED = 20261116


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


memory_engine.datetime = FrozenDatetime


def iso_result(value):
    """fromisoformat of the C module, as plain data."""
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    off = dt.utcoffset()
    return {
        "fields": [dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond],
        "offset_us": None if off is None else off // timedelta(microseconds=1),
    }


# ---------------------------------------------------------------- inputs

DATE_STRINGS = [
    "2026-06-15T12:34:56.789012+00:00", "2026-06-15", "2026-06-14T23:00:00Z",
    "2026-06-01T00:00:00", "2026-05-01T08:00:00-07:00", "2026-01-31T10:00:00+05:30",
    "2025-12-31T23:59:59Z", "2025-06-15", "2024-02-29T12:00:00", "2026-07-01T00:00:00Z",
    "2020-01-01T00:00:00+00:00", "0001-01-01", "9999-12-31T23:59:59.999999",
    "20260610T101010", "2026-W23-2T09:00", "2026-06-10 10:10:10,5", "2026-06-10T10:10:10.1234567Z",
    "2026-06-10T10Z", "2026-06-10T10:10+0130", "2026-06-10T10:10:10-00:00:00.5",
    "not a date", "", "2026-13-01", "2026-06-31", "2026-06-10T24:00:00", "2026-06-10T10:10:10Zjunk",
    "2026-06-10Z", "2026-06-10TZ", "2026-06-10T10:10:10+24:00", "2026-06-10T10:10:10+23:59:59.999999",
    "2026-06-10é10:10", "2026-06-10T10:10.", "2026-06-10T10:1", "2026-06-10T1010:10",
    "2026-0610", "202606-10", "2026W24", "2026W245", "2026-W53", "2020-W53-7", "2026-W00-1",
    "2026-W01-8", "2026-W1", "2026-W01-", "2026-06-10T10:10:10.", "2026-06-10T10,5",
    "2026-06-10T10:10:10+05", "2026-06-10T10:10:10+5", "2026-06-10T10:10:10+050", "2026-06-10T10:10:10+05:30:15",
    "2026-06-10T10:10:10+053015.25", "2026-06-10T10:10:10-12:00", "2026-06-10T10:10:10+99:99",
    "2026-06-10T10:10:10.000001+00:00:00.000001", "2026-06-10T10:10:10Z+01:00", "２026-06-10",
    "2026-06-10T10:10:10\x00", "2026-06-10\x0010:10", "2026-06-10T10:10:10.12a", "2026-06-10T10:10:10.1234567a",
    "2026-06-10T10:10:10.1234567", "2026-06-10T-10", "2026-06-10T+01", "2026-06-10T1",
    "2026-06-10T10:", "2026-06-10T10:10:", "2026-06-10T10:10:10:10", "2026-06-10T101010.5+0100",
    "2026-06-10T10:1010", "2026-06-10T1010:1", "1999-06-10T00:00:00Z", "2026-06-15T12:34:56.789013+00:00",
    "2026-06-15T12:34:56.789011+00:00", "2026-06-16T00:00:00+14:00", "2026-06-15T00:00:00-11:00",
    "1-01-01", "0000-01-01", "2026-02-29", "2024-02-30", "2026-06-10T10:60", "2026-06-10T10:10:60",
    "2026-06-10TT10", "2026-06-10T10:10:10+", "2026-06-10T10:10:10-", "2026-06-10T10:10:10+0",
    "2026-06-10T10:10:10+00", "2026-06-10T10:10:10+0000", "2026-06-10T10:10:10+00:00:00",
    "2026-06-10 ", "2026-06-10T", "2026-06", "2026-6-1", "26-06-10", "2026-06-10T10:10:10.5Z",
    "2026-06-10T10:10:10,123Z", "2026-06-10T10:10:10.123+01:00", "2026-06-10T10:10:10Z ",
]

QUERIES = [
    "", " ", "the", "What is the plan?", "what   did we decide about QDRANT??", "Who? why? when?",
    "a an the", "Rust rewrite plan", "deploy\tthe\nserver", "naïve CAFÉ ΣΊΣΥΦΟΣ", "İstanbul DŽ",
    "trailing?", "?", "??", "what?is", "hello\x1cworld\x1fend", "x y z　w", "end line",
    "tab\x0bvt\x0cff\x85nel", "ß straße STRASSE", "émoji 🚀 rocket", "WHAT WHEN", "who's there?",
    "cats? dogs?? birds", "Ǆ ǅ ǆ", "ΑΣ", "Σ", "mixed Case Words?", "᠎​ zero",
]

INTENT_QUERIES = [
    "what happened yesterday", "Yesterday's notes", "what did we do last week", "last month summary",
    "LAST YEAR goals", "last monday standup", "last Sunday", "notes from last friday",
    "3 days ago", "two weeks ago", "a month ago", "an year ago", "five years ago", "few days ago",
    "couple weeks ago", "several months ago", "12 days ago", "0 days ago", "past 3 days",
    "past two weeks", "last 3 months", "last 10 years", "past few days", "this week", "this month",
    "this year", "recently changed", "the latest decision", "most  recent plan", "newest", "nothing here",
    "in march", "In May we decided", "from jan to mar", "between november and february",
    "from september to december", "during june", "month of december", "from feb", "in sept",
    "in mayday", "from mar to junk", "between jan and jan", "in july and last week", "yesterdays",
    "the last weekend", "lastweek", "last  week", "past 1 day", "999999999 days ago",
    "1000000000 days ago", "99999999999999999999 days ago", "800000 days ago", "800000 weeks ago",
    "3000 years ago", "past 3000 years", "past 800000 months", "٣ days ago", "\U0001d7d5 days ago",
    "३ weeks ago", "last 3 weeks", "In December", "this week", "this\x1cweek", "theyesterday",
    "yesterday_", "_yesterday", "yesterdaý", "café yesterday", "last weeK", "ſeptember",
    "in ſeptember", "recently from june to may", "april showers", "in apr", "ago 3 days",
    "seven days ago and in may", "\u0130n june", "\u0131n june", "in apr\u0130l", "in apr\u0131l",
    "last fr\u0131day", "f\u0131ve days ago", "th\u0131s week", "this\u017f week", "la\u017ft week",
    "yesterday\u0130", "newest\u02b0", "\u0130N JUNE", "past \u0131 days", "LAST MONDAY",
]

REFERENCE_DATES = [
    NOW, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, 5, 0),
    datetime(2024, 3, 1, tzinfo=timezone.utc), datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc),
    datetime(1, 1, 3, tzinfo=timezone.utc), datetime(2, 2, 2, tzinfo=timezone.utc),
    datetime(9999, 12, 30, tzinfo=timezone.utc), datetime(999, 6, 15, tzinfo=timezone.utc),
    datetime(2026, 6, 14, tzinfo=timezone(timedelta(hours=-5))),
]


def gen_tokens():
    out = []
    for q in QUERIES:
        out.append({
            "input": q,
            "query_tokens": MemoryEngine._bm25_query_tokens(q),
            "doc_tokens": q.lower().split(),
        })
    return out


def random_iso(rng):
    """A string in one of the formats that fromisoformat takes, with random values."""
    y = rng.choice(["2026", "0001", "9999", "2024", "0000", "2020"])
    m, d, w = (f"{rng.randint(0, 13):02d}" for _ in range(3))
    wd = str(rng.randint(0, 8))
    date = rng.choice([f"{y}-{m}-{d}", f"{y}{m}{d}", f"{y}-W{w}-{wd}", f"{y}W{w}{wd}", f"{y}-W{w}", f"{y}W{w}"])
    if rng.random() < 0.2:
        return date
    hh, mm, ss = (f"{rng.randint(0, 25 if i == 0 else 61):02d}" for i in range(3))
    time = rng.choice([hh, f"{hh}:{mm}", f"{hh}{mm}", f"{hh}:{mm}:{ss}", f"{hh}{mm}{ss}"])
    if rng.random() < 0.4:
        time += rng.choice(".,") + "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 9)))
    th, tm, ts = (f"{rng.randint(0, 25):02d}", f"{rng.randint(0, 99):02d}", f"{rng.randint(0, 59):02d}")
    tz = rng.choice(["", "", "Z", f"{th}", f"{th}{tm}", f"{th}:{tm}", f"{th}:{tm}:{ts}",
                     f"{th}{tm}{ts}.{rng.randint(0, 999999)}", f"{th}:{tm}:{ts}.{rng.randint(0, 999999):06d}"])
    if tz and tz != "Z":
        tz = rng.choice("+-") + tz
    return date + rng.choice(["T", "T", " ", "\u00e9", "x", "\u3000"]) + time + tz


def gen_isoformat(rng):
    strings = list(DATE_STRINGS)
    alphabet = "0123456789-:T+Z.,W WZé\x00"
    for _ in range(3000):
        base = rng.choice(DATE_STRINGS[:20])
        chars = list(base)
        for _ in range(rng.randint(1, 3)):
            op = rng.random()
            pos = rng.randint(0, len(chars))
            if op < 0.4 and chars:
                del chars[min(pos, len(chars) - 1)]
            elif op < 0.8:
                chars.insert(pos, rng.choice(alphabet))
            elif chars:
                chars[min(pos, len(chars) - 1)] = rng.choice(alphabet)
        strings.append("".join(chars))
    for _ in range(1500):
        strings.append(random_iso(rng))
    seen, out = set(), []
    for s in strings:
        if s in seen:
            continue
        seen.add(s)
        out.append({"input": s, "result": iso_result(s)})
    return out


def gen_round(rng):
    values = [0.125, 0.375, 2.5, 0.5, 1.5, 0.0000005, 1.0000005, 0.0166666666, 1 / 60, 1 / 61,
              0.30000049, 123456.7890125, -0.0000004, -0.125, 0.0, -0.0, 1e-7, 9.9999995e-5,
              0.99995, 0.00005, 0.00015, 1e300, 5e-324]
    for _ in range(2000):
        values.append(rng.choice([rng.random() / rng.randint(1, 200), rng.uniform(-1, 1),
                                  round(rng.random(), rng.randint(4, 8)) + rng.choice([0, 5e-7, 5e-5, -5e-7])]))
    return [{"x": v, "n": n, "result": round(v, n)} for v in values for n in (4, 6)]


def gen_scores(rng):
    """recency_score, compute_confidence, passes_temporal_filter."""
    rec, conf, temp = [], [], []
    halves = [30.0, 90.0, 1.0, 0.0, -5.0, 0.5, 365.0, 1e-9, 1e9]
    for s in DATE_STRINGS + [None]:
        for h in halves:
            rec.append({"ts": s, "half_life": h, "result": MemoryEngine._recency_score(s, half_life_days=h)})
            conf.append({"ts": s, "half_life": h, "result": MemoryEngine.compute_confidence(s, half_life_days=h)})
    bounds = [None, "", "2026-06-01", "2026-06-10T10:10:10Z", "2026-06-10T10:10:10", "2026-06-11T00:00:00+05:00",
              "garbage", "2026-06-10TZ", "2026-06-10T10:10:10.000001Z", "2026-W24-3"]
    docs = DATE_STRINGS[:40] + [None]
    for _ in range(3000):
        meta = {}
        for key in ("document_at", "created_at", "timestamp"):
            if rng.random() < 0.6:
                meta[key] = rng.choice(docs)
        since, until = rng.choice(bounds), rng.choice(bounds)
        temp.append({"meta": meta, "since": since, "until": until,
                     "result": MemoryEngine._passes_temporal_filter(meta, since, until)})
    return rec, conf, temp


def gen_relative(rng):
    cases = [[], [{"rrf_score": 0.0}], [{"rrf_score": -1.0}, {"rrf_score": 0.5}],
             [{"similarity": 0.8}, {"rrf_score": 0.9}], [{}], [{}, {"rrf_score": 0.2}], [{"similarity": 1e-300}, {"similarity": 3e-300}]]
    for _ in range(300):
        key = rng.choice(["rrf_score", "similarity"])
        cases.append([{key: round(rng.random() / rng.choice([1, 60]), 6)} for _ in range(rng.randint(1, 12))])
    out = []
    for c in cases:
        inp = [dict(r) for r in c]
        res = annotate_relative_scores([dict(r) for r in c])
        out.append({"input": inp, "result": [r["relative_score"] for r in res]})
    return out


def gen_intent():
    out = []
    callers = [(None, None, None), ("2020-01-01T00:00:00Z", None, None), (None, "2020-02-01", 0.4),
               ("s", "u", 0.0), (None, None, 0.7)]
    for q in INTENT_QUERIES:
        for i, ref in enumerate(REFERENCE_DATES):
            caller = callers[i % len(callers)]
            case = {"query": q, "reference": ref.isoformat(),
                    "caller_since": caller[0], "caller_until": caller[1], "caller_graph_weight": caller[2]}
            try:
                adj = query_intent.classify_query(q, reference_date=ref, caller_since=caller[0],
                                                  caller_until=caller[1], caller_graph_weight=caller[2])
                case["result"] = {"since": adj.since, "until": adj.until, "graph_weight": adj.graph_weight,
                                  "recency_weight": adj.recency_weight, "auto_detected": adj.auto_detected}
            except (ValueError, OverflowError, KeyError) as exc:
                case["result"] = {"error": type(exc).__name__}
            out.append(case)
    return out


# ---------------------------------------------------------------- set order

def gen_pyset(rng):
    """CPython iteration order of int sets, built by add() and by &."""
    out = []
    for _ in range(400):
        pool = rng.choice([20, 200, 5000, 200000, 2 ** 40])
        adds = [rng.randrange(pool) for _ in range(rng.randint(0, 70))]
        other = [rng.randrange(pool) for _ in range(rng.randint(0, 70))]
        a, b = set(), set()
        for x in adds:
            a.add(x)
        for x in other:
            b.add(x)
        out.append({"adds": adds, "other": other, "order": list(a), "and_order": list(a & b)})
    return out


# ---------------------------------------------------------------- hybrid

SOURCES = ["proj/a", "proj/a/x", "proj/ab", "proj/b", "user/c", "proj"]


class FakeProfiles:
    def __init__(self, table):
        self.table = table

    def resolve(self, source):
        for prefix, half in self.table:
            if source.startswith(prefix):
                return {"confidence_half_life_days": half}
        return {}


def make_engine(metas, keyword_hits, vector_hits, profiles):
    eng = MemoryEngine.__new__(MemoryEngine)
    eng.metadata = metas
    by_id = {m["id"]: m for m in metas}
    eng._id_exists = lambda i: i in by_id
    eng._get_meta_by_id = lambda i: by_id[i]
    eng._links_generation = 0
    eng._related_adjacency_cache = None
    eng.bm25_index = object() if keyword_hits is not None else None
    eng._bm25_hits = lambda tokens: list(keyword_hits or [])
    eng._profiles = FakeProfiles(profiles) if profiles else None

    def fake_search(**kw):
        out = []
        for doc_id, sim in vector_hits:
            if doc_id not in by_id:
                # A record deleted while the search runs.
                out.append({"id": doc_id, "similarity": sim})
                continue
            out.append({**by_id[doc_id], "similarity": sim})
            eng.reinforce(doc_id)
        return out

    eng.search = fake_search
    return eng


def run_hybrid(case):
    metas = [json.loads(json.dumps(m)) for m in case["metas"]]
    eng = make_engine(metas, case["keyword_hits"], case["vector_hits"], case["profiles"])
    info_box = {}
    real_expand = eng._graph_expand

    def expand(**kw):
        cands, info = real_expand(**kw)
        info_box["info"] = info
        return cands, info

    eng._graph_expand = expand
    p = case["params"]
    scope = case["scope"]
    results = eng.hybrid_search(
        query="q", k=p["k"], threshold=p["threshold"], vector_weight=p["vector_weight"],
        recency_weight=p["recency_weight"], recency_half_life_days=p["recency_half_life_days"],
        feedback_weight=p["feedback_weight"],
        feedback_scores=None if case["feedback"] is None else {int(k): v for k, v in case["feedback"]},
        confidence_weight=p["confidence_weight"], graph_weight=p["graph_weight"],
        since=p["since"], until=p["until"], source_prefix=scope["source_prefix"],
        source_exact=scope["source_exact"], source_boundary=scope["source_boundary"],
        allowed_prefixes=scope["allowed_prefixes"], include_archived=scope["include_archived"],
    )
    keep = ("id", "rrf_score", "confidence", "base_rrf_score", "match_type", "graph_support", "graph_via")
    out = {"results": [{k: r[k] for k in keep if k in r} for r in results]}
    if "info" in info_box:
        out["graph_info"] = info_box["info"]
    return out


def visible_ids(metas, scope):
    """The scope test of hybrid_search._visible and _filter_adjacency."""
    ids = []
    for m in metas:
        source = str(m.get("source", ""))
        if (
            (scope["source_exact"] is None or source == scope["source_exact"])
            and (not scope["source_prefix"]
                 or MemoryEngine._source_matches_scope(source, scope["source_prefix"], scope["source_boundary"]))
            and (scope["allowed_prefixes"] is None or source_matches_prefixes(source, scope["allowed_prefixes"]))
            and (scope["include_archived"] or not m.get("archived"))
        ):
            ids.append(m["id"])
    return ids


def random_hybrid(rng):
    pool = rng.choice([30, 1000, 200000])
    n = rng.randint(1, min(30, pool))
    ids = rng.sample(range(pool), n)
    stamps = DATE_STRINGS[:25] + ["", None]
    metas = []
    for i in ids:
        m = {"id": i, "source": rng.choice(SOURCES), "text": "t"}
        if rng.random() < 0.15:
            m["archived"] = True
        for key in ("document_at", "created_at", "timestamp", "updated_at", "last_reinforced_at"):
            if rng.random() < 0.5:
                m[key] = rng.choice(stamps)
        links = []
        for _ in range(rng.choice([0, 0, 1, 2, 3, 6])):
            target = rng.choice(ids + [pool + 7, i])
            links.append({"to_id": target, "type": rng.choice(["related_to"] * 4 + ["supersedes", "blocked_by"])})
        if links:
            m["links"] = links
        metas.append(m)
    scope = {
        "source_prefix": rng.choice([None, None, "", "proj/a", "proj"]),
        "source_exact": rng.choice([None, None, None, "proj/b"]),
        "source_boundary": rng.random() < 0.3,
        "allowed_prefixes": rng.choice([None, None, ["proj/a"], ["proj/*", "user"]]),
        "include_archived": rng.random() < 0.5,
    }
    vis = visible_ids(metas, scope)
    vector = []
    if vis:
        sims = sorted((round(rng.random(), 6) for _ in range(rng.randint(0, len(vis)))), reverse=True)
        if rng.random() < 0.2 and sims:
            sims = [sims[0]] * len(sims)
        vector = list(zip(rng.sample(vis, len(sims)), sims))
    keyword = None
    if rng.random() < 0.85:
        kpool = ids if rng.random() < 0.3 else vis
        keyword = [(d, rng.choice([1.0, 2.0, round(rng.uniform(0.01, 9), 4)]))
                   for d in rng.sample(kpool, rng.randint(0, len(kpool)))]
    feedback = None
    if rng.random() < 0.6:
        feedback = [(d, rng.randint(-3, 5)) for d in rng.sample(ids + [pool + 9], rng.randint(0, min(10, n)))]

    def weight(lo=0.0, hi=1.0):
        return rng.choice([0.0, 0.0, round(rng.uniform(lo, hi), 3), 1.0, 0.1, 0.2])

    params = {
        "k": rng.choice([1, 2, 3, 5, 5, 8, 12, 40, 150]),
        "threshold": rng.choice([None, None, None, 0.3, 0.6, round(rng.random(), 6)]),
        "vector_weight": rng.choice([0.7, 0.0, 1.0, round(rng.random(), 3)]),
        "recency_weight": weight(-0.3, 1.3), "recency_half_life_days": rng.choice([30.0, 1.0, 0.0, -2.0, 365.0]),
        "feedback_weight": weight(-0.2, 1.2), "confidence_weight": weight(-0.2, 1.2),
        "graph_weight": rng.choice([0.0, 0.1, 0.1, 0.5, 1.0, 1.7, -0.5]),
        "since": rng.choice([None, None, None, "2026-06-01", "2026-01-01T00:00:00Z", "bad"]),
        "until": rng.choice([None, None, None, "2026-06-12", "2026-06-15T12:00:00+00:00"]),
    }
    profiles = rng.choice([None, [["proj/a", 1.0], ["user", 0.0]], [["proj", 7.0]]])
    return {"metas": metas, "scope": scope, "vector_hits": vector, "keyword_hits": keyword,
            "feedback": feedback, "params": params, "profiles": profiles}


def fixed_hybrid():
    """Hand-made cases for branches that random cases rarely reach."""
    base_params = {"k": 5, "threshold": None, "vector_weight": 0.7, "recency_weight": 0.0,
                   "recency_half_life_days": 30.0, "feedback_weight": 0.0, "confidence_weight": 0.0,
                   "graph_weight": 0.1, "since": None, "until": None}
    no_scope = {"source_prefix": None, "source_exact": None, "source_boundary": False,
                "allowed_prefixes": None, "include_archived": True}

    def case(metas, vector, keyword, params=None, scope=None, feedback=None):
        return {"metas": metas, "scope": scope or no_scope, "vector_hits": vector, "keyword_hits": keyword,
                "feedback": feedback, "params": {**base_params, **(params or {})}, "profiles": None}

    star = [{"id": 1, "source": "proj/a", "links": [{"to_id": 2, "type": "related_to"},
                                                     {"to_id": 3, "type": "related_to"},
                                                     {"to_id": 4, "type": "related_to"}]},
            {"id": 2, "source": "proj/a"}, {"id": 3, "source": "proj/a"}, {"id": 4, "source": "proj/b"},
            {"id": 9, "source": "proj/a"}]
    return [
        case([], [], [], {}),
        case(star, [], None, {}),
        case(star, [(1, 0.9)], [], {}),
        case(star, [(1, 0.9)], [], {"threshold": 0.5}),
        case(star, [(1, 0.9), (9, 0.4)], [(9, 3.0)], {"threshold": 0.5}),
        case(star, [(1, 0.9)], [], {"k": 1}),
        case(star, [(1, 0.9)], [], {"k": 2}),
        case(star, [(1, 0.9)], [], scope={**no_scope, "source_prefix": "proj/a", "include_archived": False}),
        case(star, [(1, 0.9)], [(2, 1.0)], {"vector_weight": 1.0, "graph_weight": 1.0}),
        case(star, [(2, 0.5)], [], {"vector_weight": 0.0}),
        case(star, [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)], [], {"graph_weight": 1.0}),
        case(star, [(1, 0.9)], [], {"feedback_weight": 0.6, "confidence_weight": 0.9},
             feedback=[(1, 2), (2, 5), (3, -1)]),
        case(star, [(1, 0.9)], [], {"since": "2030-01-01"}),
        case(star, [(77, 0.95), (1, 0.9)], [], {"recency_weight": 0.5, "confidence_weight": 0.5,
                                                 "graph_weight": 0.0}),
        case(star, [(77, 0.95), (1, 0.9)], [], {"recency_weight": 0.5, "confidence_weight": 0.5}),
        case([{"id": 1, "source": "s", "links": [{"to_id": 1, "type": "related_to"}]},
              {"id": 2, "source": "s", "links": [{"to_id": 3, "type": "related_to"}]},
              {"id": 3, "source": "s", "links": [{"to_id": 4, "type": "related_to"}]},
              {"id": 4, "source": "s"}],
             [(1, 0.9), (2, 0.8)], [], {"graph_weight": 0.5}),
    ]


def gen_hybrid(rng):
    cases = fixed_hybrid() + [random_hybrid(rng) for _ in range(300)]
    out = []
    for c in cases:
        expected = run_hybrid(c)
        scope = c["scope"]
        filtered = not (scope["source_exact"] is None and not scope["source_prefix"]
                        and scope["allowed_prefixes"] is None and scope["include_archived"])
        out.append({**c, "visible": visible_ids(c["metas"], scope), "filtered": filtered,
                    "now": NOW.isoformat(), "expected": expected})
    return out


# ---------------------------------------------------------------- main

def build():
    rng = random.Random(SEED)
    rec, conf, temp = gen_scores(rng)
    return {
        "tokens": gen_tokens(),
        "isoformat": gen_isoformat(rng),
        "round": gen_round(rng),
        "recency": rec,
        "confidence": conf,
        "temporal": temp,
        "relative": gen_relative(rng),
        "intent": gen_intent(),
        "pyset": gen_pyset(rng),
        "hybrid": gen_hybrid(rng),
    }


def dump(now, cases):
    """One case per line, so a diff shows which cases changed."""
    def one(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    lines = ",\n".join(one(c) for c in cases)
    return f'{{"now":{one(now)},"cases":[\n{lines}\n]}}\n'



def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m parity.rank_golden")
    p.add_argument("--check", action="store_true", help="exit 1 if a golden file differs")
    args = p.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        sys.exit("Use Python 3.11, the version of the production image.")
    files = {name: dump(NOW.isoformat(), cases) for name, cases in build().items()}
    if args.check:
        bad = [n for n, text in files.items() if (OUT_DIR / f"{n}.json").read_text(encoding="utf-8") != text]
        if bad:
            sys.exit(f"Golden rank files differ: {', '.join(bad)}. Run python -m parity.rank_golden.")
        print(f"{len(files)} golden rank files match.")
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (OUT_DIR / f"{name}.json").write_text(text, encoding="utf-8")
    print(f"Wrote {len(files)} files to {OUT_DIR}.")


if __name__ == "__main__":
    main()
