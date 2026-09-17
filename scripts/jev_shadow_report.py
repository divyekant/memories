#!/usr/bin/env python3
"""Summarize Jev shadow agreement. Agreement is not accuracy or product quality."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import time


def summarize(records):
    unique={r.get('call_id',str(i)):r for i,r in enumerate(records)}
    rows=list(unique.values())
    paired=[r for r in rows if r.get('status')=='ok' and isinstance(r.get('primary_decisions'),list) and len(r['primary_decisions'])==r.get('fact_count') and not r.get('primary_parse_error')]
    dropped={}
    for r in rows:
        pid=r.get('process_id','unknown');dropped[pid]=max(dropped.get(pid,0),r.get('dropped_total',0))
    def latency(key):
        values=sorted(r[key] for r in paired if isinstance(r.get(key),(int,float)))
        return {'n':len(values),'median_ms':statistics.median(values),'p95_ms':values[max(0,math.ceil(len(values)*0.95)-1)]} if values else None
    return {
        'calls':len(rows),'status':dict(Counter(r.get('status','unknown') for r in rows)),
        'paired_calls':len(paired),'paired_facts':sum(r['fact_count'] for r in paired),
        'action_matches':sum(r.get('action_matches') or 0 for r in paired),
        'joint_matches':sum(r.get('joint_matches') or 0 for r in paired),
        'invalid_targets':sum(not d.get('target_valid',False) for r in rows for d in r.get('shadow_decisions') or []),
        'primary_models':dict(Counter(str(r.get('primary_model')) for r in rows)),
        'served_models':dict(Counter(str(r.get('served_model')) for r in rows if r.get('status')=='ok')),
        'dropped_observed':sum(dropped.values()),
        'jev_latency':latency('latency_ms'),'primary_latency':latency('primary_latency_ms'),
        'interpretation':'Agreement only. Review original evidence before judging quality. Drop counts are observed lifetime totals per process, not window-specific. Rotated logs may exclude older calls.',
    }


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log-dir',type=Path,default=Path('data/shadow-logs'))
    p.add_argument('--days',type=float,default=7)
    p.add_argument('--review-out',type=Path,help='private JSONL review packet: up to 20 disagreements plus 10 agreements')
    args=p.parse_args();records=[];malformed=0
    cutoff=time.time()-args.days*86400
    for path in sorted(args.log_dir.glob('memories-shadow-jev*.log*')):
        for line in path.open():
            try:
                r=json.loads(line)
                if r.get('ts',0)>=cutoff: records.append(r)
            except (ValueError,TypeError): malformed+=1
    result=summarize(records);result['malformed_lines']=malformed
    result['since']=datetime.fromtimestamp(cutoff,timezone.utc).isoformat()
    print(json.dumps(result,indent=2))
    if args.review_out:
        good={r['call_id']:r for r in records if r.get('status')=='ok' and r.get('call_id')}
        ordered=sorted(good.values(),key=lambda r:r.get('prompt_hash',''))
        disagree=[r for r in ordered if r.get('joint_matches')!=r.get('fact_count')][:20]
        agree=[r for r in ordered if r.get('joint_matches')==r.get('fact_count')][:10]
        import os
        fd=os.open(args.review_out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:
            for r in disagree+agree: f.write(json.dumps(r)+'\n')
