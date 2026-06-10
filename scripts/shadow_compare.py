#!/usr/bin/env python3
"""Compare shadow extraction outputs against primary (Haiku) using semantic scoring.

Uses MiniLM-L6-v2 semantic similarity — same judge as memex eval — instead of
Jaccard-on-exact-strings. Reports per-call-type agreement metrics.

Requires: pip install sentence-transformers
Or run with memex venv: /Users/dk/projects/memex/.venv/bin/python scripts/shadow_compare.py

Usage:
    python scripts/shadow_compare.py [--log-dir data/shadow-logs]
    python scripts/shadow_compare.py --detail          # show worst disagreements
    python scripts/shadow_compare.py --export out.csv  # export per-record CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _embedder


def _semantic_similarity(a: str, b: str) -> float:
    """Cosine similarity via MiniLM-L6-v2 sentence embeddings."""
    try:
        model = _get_embedder()
        embs = model.encode([a.lower(), b.lower()], normalize_embeddings=True)
        return float(embs[0] @ embs[1])
    except Exception:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()


def _parse_facts(raw: str | None) -> list[dict]:
    """Parse raw extraction output into a list of {category, text} dicts."""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and "text" in item]


def _parse_audn(raw: str | None) -> list[dict]:
    """Parse raw AUDN output into a list of {action, fact_index, ...} dicts."""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and "action" in item]


def _best_match_score(fact_text: str, candidates: list[dict]) -> float:
    """Find the highest semantic similarity between fact_text and any candidate's text."""
    if not candidates:
        return 0.0
    best = 0.0
    for c in candidates:
        ct = c.get("text", "")
        if not ct:
            continue
        sim = _semantic_similarity(fact_text, ct)
        if sim > best:
            best = sim
    return best


def score_extract_record(primary_text: str, shadow_text: str) -> dict[str, float]:
    """Score a single extract record: shadow vs primary (Haiku as ground truth)."""
    expected = _parse_facts(primary_text)
    actual = _parse_facts(shadow_text)

    # Completeness: what fraction of primary facts did shadow capture?
    if not expected and not actual:
        completeness = 1.0
    elif not expected:
        completeness = 0.0  # hallucinated facts
    elif not actual:
        completeness = 0.0
    else:
        captured = sum(1 for e in expected if _best_match_score(e["text"], actual) > 0.5)
        completeness = captured / len(expected)

    # Accuracy: how well do shadow's facts match primary?
    if not actual:
        accuracy = 1.0 if not expected else 0.0
    else:
        scores = []
        for act in actual:
            act_text = act.get("text", "")
            if not act_text:
                scores.append(0.0)
                continue
            sim = _best_match_score(act_text, expected)
            act_cat = (act.get("category") or "").lower()
            # Check if category matches any expected fact's category
            cat_bonus = 1.0
            for e in expected:
                e_sim = _semantic_similarity(act_text, e.get("text", ""))
                if e_sim > 0.5:
                    e_cat = (e.get("category") or "").lower()
                    if act_cat and e_cat and act_cat != e_cat:
                        cat_bonus = 0.75
                    break
            scores.append(sim * cat_bonus)
        accuracy = sum(scores) / len(scores)

    # JSON compliance
    try:
        data = json.loads(_strip_fences(shadow_text))
        if isinstance(data, list):
            valid = sum(1 for i in data if isinstance(i, dict) and "category" in i and "text" in i)
            json_compliance = valid / len(data) if data else 1.0
        else:
            json_compliance = 0.0
    except (json.JSONDecodeError, TypeError):
        json_compliance = 0.0

    composite = (completeness + accuracy + json_compliance) / 3
    return {
        "completeness": round(completeness, 4),
        "accuracy": round(accuracy, 4),
        "json_compliance": round(json_compliance, 4),
        "composite": round(composite, 4),
        "primary_count": len(expected),
        "shadow_count": len(actual),
    }


def score_audn_record(primary_text: str, shadow_text: str) -> dict[str, float]:
    """Score a single AUDN record: compare action decisions."""
    expected = _parse_audn(primary_text)
    actual = _parse_audn(shadow_text)

    if not expected and not actual:
        return {"action_accuracy": 1.0, "composite": 1.0, "primary_count": 0, "shadow_count": 0}
    if not expected or not actual:
        return {"action_accuracy": 0.0, "composite": 0.0,
                "primary_count": len(expected), "shadow_count": len(actual)}

    # Match by fact_index, compare action type
    matched = 0
    total = max(len(expected), len(actual))
    remaining = list(expected)
    for act in actual:
        for i, exp in enumerate(remaining):
            if act.get("fact_index") == exp.get("fact_index"):
                if act.get("action") == exp.get("action"):
                    matched += 1
                remaining.pop(i)
                break

    action_accuracy = matched / total if total else 1.0
    return {
        "action_accuracy": round(action_accuracy, 4),
        "composite": round(action_accuracy, 4),
        "primary_count": len(expected),
        "shadow_count": len(actual),
    }


def load_shadow_logs(log_dir: str) -> dict[str, list[dict]]:
    models: dict[str, list[dict]] = {}
    for path in sorted(Path(log_dir).glob("memories-shadow-*.log")):
        model = path.stem.replace("memories-shadow-", "")
        records = []
        for line_num, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  WARN: {path.name}:{line_num} — bad JSON, skipped",
                      file=sys.stderr)
        models[model] = records
    return models


def analyze_model(records: list[dict], call_type: str | None = None) -> dict:
    """Run semantic scoring over all records for a model."""
    filtered = records
    if call_type:
        filtered = [r for r in records if r.get("call_type") == call_type]

    total = len(filtered)
    errors = sum(1 for r in filtered if r.get("error"))

    extract_scores = []
    audn_scores = []
    latencies = []

    for r in filtered:
        if r.get("error"):
            continue
        pt = r.get("primary_text") or ""
        st = r.get("shadow_text") or ""
        latencies.append(r.get("latency_ms", 0))

        ct = r.get("call_type", "")
        if ct == "extract":
            if pt.strip() and st.strip():
                extract_scores.append(score_extract_record(pt, st))
        elif ct == "audn":
            if pt.strip() and st.strip():
                audn_scores.append(score_audn_record(pt, st))

    def _avg(lst, key):
        vals = [s[key] for s in lst if key in s]
        return sum(vals) / len(vals) if vals else 0.0

    result = {
        "total": total,
        "errors": errors,
        "error_rate": f"{errors / total * 100:.1f}%" if total else "N/A",
    }

    if extract_scores or not call_type or call_type == "extract":
        result["extract"] = {
            "scored": len(extract_scores),
            "avg_completeness": round(_avg(extract_scores, "completeness"), 3),
            "avg_accuracy": round(_avg(extract_scores, "accuracy"), 3),
            "avg_json_compliance": round(_avg(extract_scores, "json_compliance"), 3),
            "avg_composite": round(_avg(extract_scores, "composite"), 3),
        }

    if audn_scores or not call_type or call_type == "audn":
        result["audn"] = {
            "scored": len(audn_scores),
            "avg_action_accuracy": round(_avg(audn_scores, "action_accuracy"), 3),
            "avg_composite": round(_avg(audn_scores, "composite"), 3),
        }

    if latencies:
        latencies.sort()
        result["latency"] = {
            "avg_ms": int(sum(latencies) / len(latencies)),
            "p50_ms": int(latencies[len(latencies) // 2]),
            "p95_ms": int(latencies[int(len(latencies) * 0.95)]),
        }

    return result


def print_detail(records: list[dict], call_type: str | None = None, limit: int = 10):
    """Show the worst disagreements."""
    scored = []
    for r in records:
        if r.get("error"):
            continue
        pt = r.get("primary_text") or ""
        st = r.get("shadow_text") or ""
        ct = r.get("call_type", "")
        if call_type and ct != call_type:
            continue
        if not pt.strip() or not st.strip():
            continue

        if ct == "extract":
            s = score_extract_record(pt, st)
            scored.append((r, s, ct))
        elif ct == "audn":
            s = score_audn_record(pt, st)
            scored.append((r, s, ct))

    scored.sort(key=lambda x: x[1]["composite"])

    print(f"\n{'='*70}")
    print(f"Top {min(limit, len(scored))} worst disagreements "
          f"(of {len(scored)} scored)")
    print(f"{'='*70}")

    for r, s, ct in scored[:limit]:
        print(f"\n--- prompt_hash: {r.get('prompt_hash', '?')} | "
              f"call_type: {ct} | composite: {s['composite']:.3f} | "
              f"latency: {r.get('latency_ms', 0)}ms")
        if ct == "extract":
            print(f"    completeness={s['completeness']:.3f}  "
                  f"accuracy={s['accuracy']:.3f}  "
                  f"json={s['json_compliance']:.3f}  "
                  f"primary_facts={s['primary_count']}  "
                  f"shadow_facts={s['shadow_count']}")
            pf = _parse_facts(r.get("primary_text"))
            sf = _parse_facts(r.get("shadow_text"))
            if pf:
                print(f"    PRIMARY ({len(pf)}):")
                for f in pf[:5]:
                    print(f"      [{f.get('category','')}] {f.get('text','')[:100]}")
            if sf:
                print(f"    SHADOW ({len(sf)}):")
                for f in sf[:5]:
                    print(f"      [{f.get('category','')}] {f.get('text','')[:100]}")
        elif ct == "audn":
            print(f"    action_accuracy={s['action_accuracy']:.3f}  "
                  f"primary_actions={s['primary_count']}  "
                  f"shadow_actions={s['shadow_count']}")
            pa = _parse_audn(r.get("primary_text"))
            sa = _parse_audn(r.get("shadow_text"))
            print(f"    PRIMARY: {[{k: a[k] for k in ['action','fact_index'] if k in a} for a in pa[:6]]}")
            print(f"    SHADOW:  {[{k: a[k] for k in ['action','fact_index'] if k in a} for a in sa[:6]]}")


def export_csv(all_models: dict[str, list[dict]], path: str,
               call_type: str | None = None):
    """Export per-record scores to CSV."""
    rows = []
    for model, records in all_models.items():
        for r in records:
            if r.get("error"):
                continue
            ct = r.get("call_type", "")
            if call_type and ct != call_type:
                continue
            pt = r.get("primary_text") or ""
            st = r.get("shadow_text") or ""
            if not pt.strip() or not st.strip():
                continue

            if ct == "extract":
                s = score_extract_record(pt, st)
            elif ct == "audn":
                s = score_audn_record(pt, st)
            else:
                continue

            rows.append({
                "model": model,
                "prompt_hash": r.get("prompt_hash", ""),
                "call_type": ct,
                "source": r.get("source", ""),
                "composite": s["composite"],
                **{k: v for k, v in s.items() if k != "composite"},
                "latency_ms": r.get("latency_ms", 0),
                "ts": r.get("ts", ""),
            })

    if not rows:
        print("No scorable records to export.")
        return

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} rows to {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Compare shadow extraction outputs using semantic scoring")
    parser.add_argument("--log-dir", default="data/shadow-logs",
                        help="Directory containing shadow JSONL logs")
    parser.add_argument("--call-type", choices=["extract", "audn"],
                        help="Filter to a specific call type")
    parser.add_argument("--detail", action="store_true",
                        help="Show worst disagreements")
    parser.add_argument("--detail-limit", type=int, default=10)
    parser.add_argument("--export", metavar="PATH",
                        help="Export per-record scores to CSV")
    args = parser.parse_args()

    models = load_shadow_logs(args.log_dir)
    if not models:
        print(f"No shadow logs found in {args.log_dir}/memories-shadow-*.log")
        sys.exit(1)

    print(f"Shadow log directory: {args.log_dir}")
    print(f"Judge: sentence-transformers/all-MiniLM-L6-v2 (semantic similarity)")
    if args.call_type:
        print(f"Filtering: {args.call_type}")
    print()

    for model, records in sorted(models.items()):
        metrics = analyze_model(records, args.call_type)
        print(f"Model: {model}")
        print(f"  Records: {metrics['total']} total, "
              f"{metrics['errors']} errors ({metrics.get('error_rate', 'N/A')})")

        if "extract" in metrics:
            e = metrics["extract"]
            print(f"  Extraction ({e['scored']} scored):")
            print(f"    completeness={e['avg_completeness']:.3f}  "
                  f"accuracy={e['avg_accuracy']:.3f}  "
                  f"json={e['avg_json_compliance']:.3f}  "
                  f"composite={e['avg_composite']:.3f}")

        if "audn" in metrics:
            a = metrics["audn"]
            print(f"  AUDN ({a['scored']} scored):")
            print(f"    action_accuracy={a['avg_action_accuracy']:.3f}  "
                  f"composite={a['avg_composite']:.3f}")

        if "latency" in metrics:
            l = metrics["latency"]
            print(f"  Latency: avg={l['avg_ms']}ms  "
                  f"p50={l['p50_ms']}ms  p95={l['p95_ms']}ms")
        print()

        if args.detail:
            print_detail(records, args.call_type, args.detail_limit)

    if args.export:
        export_csv(models, args.export, args.call_type)


if __name__ == "__main__":
    main()
