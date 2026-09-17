#!/usr/bin/env python3
"""Summarize independent Jev observations. Agreement is not product quality."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import time

FLOWS = ('extraction', 'audn', 'relationships', 'retrieval', 'consolidation', 'pruning', 'promotion')


def join_records(records):
    """Join independently completed events without losing either side."""
    grouped = {}
    for i, record in enumerate(records):
        grouped.setdefault(record.get('call_id', str(i)), {})[record.get('event', 'combined')] = record
    rows = []
    for events in grouped.values():
        row = dict(events.get('combined') or events.get('shadow') or events['primary'])
        row.setdefault('flow', row.get('call_type', 'audn'))
        if 'combined' not in events:
            primary = events.get('primary')
            row['missing_shadow'] = 'shadow' not in events
            row['missing_primary'] = primary is None
            if primary:
                for key in ('baseline', 'primary_decisions', 'primary_parse_error', 'primary_latency_ms'):
                    if key in primary:
                        row[key] = primary[key]
                row['primary_status'] = primary.get('baseline', {}).get('status', primary.get('status'))
            if row['flow'] == 'audn' and not row['missing_shadow']:
                p, s = row.get('primary_decisions'), row.get('shadow_decisions')
                if isinstance(p, list) and isinstance(s, list) and len(p) == len(s) == row.get('fact_count'):
                    row['action_matches'] = sum(a.get('action') == b.get('action') for a, b in zip(p, s))
                    row['joint_matches'] = sum(a.get('action') == b.get('action') and a.get('target') == b.get('target') and a.get('target_valid', False) and b.get('target_valid', False) for a, b in zip(p, s))
        rows.append(row)
    return rows


def summarize(records):
    rows = join_records(records)
    shadow_rows = [r for r in rows if not r.get('missing_shadow')]
    paired = [r for r in shadow_rows if r['flow'] == 'audn' and r.get('status') == 'ok'
              and isinstance(r.get('primary_decisions'), list)
              and len(r['primary_decisions']) == r.get('fact_count') and not r.get('primary_parse_error')]
    dropped = {}
    flow_drops = {flow: {} for flow in FLOWS}
    for r in records:
        pid = r.get('process_id', 'unknown')
        dropped[pid] = max(dropped.get(pid, 0), r.get('dropped_total', 0))
        for flow, count in r.get('dropped_by_flow', {}).items():
            if flow in flow_drops:
                flow_drops[flow][pid] = max(flow_drops[flow].get(pid, 0), count)

    def latency(selected, key):
        values = sorted(r[key] for r in selected if isinstance(r.get(key), (int, float)))
        return {'n': len(values), 'median_ms': statistics.median(values),
                'p95_ms': values[max(0, math.ceil(len(values) * 0.95) - 1)]} if values else None

    flows = {}
    for flow in FLOWS:
        selected = [r for r in shadow_rows if r['flow'] == flow]
        good = [r for r in selected if r.get('status') == 'ok']
        answers = Counter()
        for r in good:
            for qid, answer in (r.get('shadow_answers') or {}).items():
                choice = answer.get('choice') if isinstance(answer, dict) else answer
                answers[str(choice)] += 1
        flows[flow] = {
            'coverage': 'observed' if selected else 'not_observed',
            'calls': len(selected), 'status': dict(Counter(r.get('status', 'unknown') for r in selected)),
            'missing_primary': sum(bool(r.get('missing_primary')) for r in selected),
            'missing_shadow': sum(bool(r.get('missing_shadow')) for r in rows if r['flow'] == flow),
            'primary_status': dict(Counter(r.get('primary_status', 'combined') for r in selected)),
            'evaluated_count': sum(r.get('evaluated_count', r.get('fact_count', 0)) or 0 for r in good),
            'total_count': sum(r.get('total_count', r.get('fact_count', 0)) or 0 for r in good),
            'answer_choices': dict(answers), 'latency': latency(good, 'latency_ms'),
            'dropped_observed': sum(flow_drops[flow].values()),
        }
    return {
        'calls': len(rows), 'status': dict(Counter(r.get('status', 'unknown') for r in shadow_rows)),
        'flows': flows, 'paired_calls': len(paired), 'paired_facts': sum(r['fact_count'] for r in paired),
        'action_matches': sum(r.get('action_matches') or 0 for r in paired),
        'joint_matches': sum(r.get('joint_matches') or 0 for r in paired),
        'invalid_targets': sum(not d.get('target_valid', False) for r in shadow_rows for d in r.get('shadow_decisions') or []),
        'primary_models': dict(Counter(str(r.get('primary_model')) for r in rows)),
        'served_models': dict(Counter(str(r.get('served_model')) for r in shadow_rows if r.get('status') == 'ok')),
        'dropped_observed': sum(dropped.values()), 'jev_latency': latency(paired, 'latency_ms'),
        'primary_latency': latency(paired, 'primary_latency_ms'),
        'interpretation': 'Agreement is not accuracy. Review original evidence for each flow. Missing events include pending, dropped, or rotated records. Drop counts are observed lifetime totals per process, not window-specific. Optional flows may remain unobserved. Choice counts pool question types and are not quality scores.',
    }


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log-dir', type=Path, default=Path('data/shadow-logs'))
    p.add_argument('--days', type=float, default=7)
    p.add_argument('--review-out', type=Path, help='private JSONL review packet: up to 20 successful observations per flow')
    args = p.parse_args()
    records = []; malformed = 0
    cutoff = time.time() - args.days * 86400
    for path in sorted(args.log_dir.glob('memories-shadow-jev*.log*')):
        with path.open() as handle:
            for line in handle:
                try:
                    r = json.loads(line)
                    if not isinstance(r, dict):
                        raise ValueError('record must be an object')
                    if r.get('ts', 0) >= cutoff:
                        records.append(r)
                except (ValueError, TypeError):
                    malformed += 1
    result = summarize(records)
    result.update(malformed_lines=malformed, since=datetime.fromtimestamp(cutoff, timezone.utc).isoformat())
    print(json.dumps(result, indent=2))
    if args.review_out:
        ordered = sorted(join_records(records), key=lambda r: r.get('prompt_hash', ''))
        fd = os.open(args.review_out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as handle:
            for flow in FLOWS:
                selected = [r for r in ordered if r['flow'] == flow and r.get('status') == 'ok' and not r.get('missing_shadow')]
                # Keep disagreements first; include other flows for evidence review.
                selected.sort(key=lambda r: r.get('joint_matches') == r.get('fact_count') if flow == 'audn' else False)
                for row in selected[:20]:
                    handle.write(json.dumps(row) + '\n')
