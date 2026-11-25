"""Audit completed V7 benchmark logs. Standard library only; does not alter input."""
import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def read(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def truth(s):
    if s.lower() not in ('true', 'false'):
        raise ValueError('Invalid boolean: ' + s)
    return s.lower() == 'true'


def percentile(values, p):
    if not values:
        return None
    x = sorted(values)
    i = (len(x)-1)*p
    lo, hi = math.floor(i), math.ceil(i)
    return x[lo]+(x[hi]-x[lo])*(i-lo)


def write(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def audit(batch, out):
    runs = read(batch/'runs.csv')
    groups = defaultdict(list)
    reasons = Counter()
    retries = defaultdict(list)
    violations = []
    stops = []
    blocks = defaultdict(dict)
    seen = set()
    for r in runs:
        s, mode, rep = r['scenario'], r['mode'], int(r['repeat'])
        key = (s, mode, rep)
        if key in seen:
            raise ValueError('Duplicate run '+str(key))
        seen.add(key)
        folder = batch/f'{s}_{rep:02d}_{mode}'
        report = json.loads((folder/'report.json').read_text())
        ops, attempts, decisions = (read(folder/f'{n}.csv') for n in ('operations','attempts','decisions'))
        if not report['complete'] or len(ops) != int(r['operations']):
            raise ValueError('Incomplete run '+str(folder))
        deadline = float(report['deadline_ms'])
        aa, dd = defaultdict(list), defaultdict(list)
        for a in attempts: aa[int(a['operation'])].append(a)
        for d in decisions: dd[int(d['operation'])].append(d)
        ids = [int(o['operation']) for o in ops]
        if ids != list(range(1,len(ops)+1)) or set(aa)-set(ids) or set(dd)-set(ids):
            raise ValueError('Invalid operation IDs '+str(folder))
        blocks[(s,rep,int(r['seed']))][mode] = mean(truth(o['within_deadline']) for o in ops)
        for o in ops:
            i = int(o['operation']); at = sorted(aa[i],key=lambda a:int(a['attempt'])); ds = sorted(dd[i],key=lambda d:int(d['after_attempt']))
            if len(at)!=int(o['attempts']) or [int(a['attempt']) for a in at]!=list(range(1,len(at)+1)):
                raise ValueError('Attempt count mismatch '+str(folder))
            if any(int(d['after_attempt'])<1 or int(d['after_attempt'])>len(at) for d in ds):
                raise ValueError('Invalid decision index '+str(folder))
            elapsed = float(o['elapsed_ms']); ok = truth(o['within_deadline']); success = truth(o['success']); late = truth(o['deadline_exceeded'])
            if late != (elapsed>deadline) or ok != (success and not late):
                raise ValueError('Deadline inconsistency '+str(folder))
            groups[(s,mode)].append((elapsed,ok,late,len(at)))
            for d in ds:
                j=int(d['after_attempt']); action=d['action']; reason=d['reason']; state=d['state']
                reasons[(s,mode,state,action,reason)]+=1
                base=dict(scenario=s,mode=mode,repeat=rep,operation=i,after_attempt=j,state=state,reason=reason,previous_outcome=at[j-1]['outcome'],previous_latency_ms=float(at[j-1]['latency_ms']))
                if action=='retry':
                    executed=j<len(at)
                    retries[(s,mode,state)].append((executed,executed and at[j]['outcome']=='success',executed and at[j]['outcome']=='success' and ok))
                    if not executed:
                        stops.append(dict(**base,kind='retry_selected_not_executed',elapsed_ms=elapsed,remaining_at_end_ms=deadline-elapsed))
                elif action=='stop' and not success:
                    stops.append(dict(**base,kind='explicit_stop',elapsed_ms=elapsed,remaining_at_end_ms=deadline-elapsed))
            if late:
                actual=sum(float(a['latency_ms']) for a in at)
                planned=sum(int(d['backoff_ms']) for d in ds if d['action']=='retry' and int(d['after_attempt'])<len(at))
                violations.append(dict(scenario=s,mode=mode,repeat=rep,operation=i,elapsed_ms=elapsed,deadline_ms=deadline,overrun_ms=elapsed-deadline,attempts=len(at),worker_latency_sum_ms=actual,executed_planned_backoff_ms=planned,residual_ms=elapsed-actual-planned,last_outcome=at[-1]['outcome'] if at else 'none'))
    out.mkdir(parents=True,exist_ok=False)
    pooled=[]
    for (s,m), values in sorted(groups.items()):
        pooled.append(dict(scenario=s,mode=m,operations=len(values),deadline_success_rate=mean(v[1] for v in values),deadline_violations=sum(v[2] for v in values),attempts=sum(v[3] for v in values),p95_ms=percentile([v[0] for v in values],.95),p99_ms=percentile([v[0] for v in values],.99)))
    write(out/'pooled_metrics.csv',pooled,list(pooled[0]))
    write(out/'decision_counts.csv',[dict(scenario=s,mode=m,state=t,action=a,reason=r,count=n) for (s,m,t,a,r),n in sorted(reasons.items())],['scenario','mode','state','action','reason','count'])
    write(out/'stopped_operations.csv',stops,['scenario','mode','repeat','operation','after_attempt','state','reason','previous_outcome','previous_latency_ms','kind','elapsed_ms','remaining_at_end_ms'])
    write(out/'deadline_violations.csv',violations,['scenario','mode','repeat','operation','elapsed_ms','deadline_ms','overrun_ms','attempts','worker_latency_sum_ms','executed_planned_backoff_ms','residual_ms','last_outcome'])
    write(out/'retry_outcomes.csv',[dict(scenario=s,mode=m,state=t,selected=len(v),executed=sum(x[0] for x in v),next_attempt_success=sum(x[1] for x in v),next_attempt_success_within_operation_deadline=sum(x[2] for x in v)) for (s,m,t),v in sorted(retries.items())],['scenario','mode','state','selected','executed','next_attempt_success','next_attempt_success_within_operation_deadline'])
    paired=[]
    for (s,r,seed), modes in sorted(blocks.items()):
        if 'static' not in modes: continue
        for m,v in sorted(modes.items()):
            if m!='static': paired.append(dict(scenario=s,repeat=r,seed=seed,mode=m,delta_deadline_success_vs_static=v-modes['static']))
    write(out/'paired_run_differences.csv',paired,['scenario','repeat','seed','mode','delta_deadline_success_vs_static'])
    print(json.dumps(dict(runs=len(runs),operations=sum(len(v) for v in groups.values()),violations=len(violations),explicit_stops=sum(x['kind']=='explicit_stop' for x in stops),retry_selected_not_executed=sum(x['kind']=='retry_selected_not_executed' for x in stops),output=str(out.resolve())),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(); audit(a.batch,a.out)
