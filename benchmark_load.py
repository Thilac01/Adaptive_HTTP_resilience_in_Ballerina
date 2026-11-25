"""Consolidated single-retry live benchmark. Run in Windows project root."""
import argparse, csv, hashlib, json, random, time
from pathlib import Path
from datetime import datetime
from math import floor
from collections import defaultdict
import numpy as np
import pandas as pd
import joblib
from collect_actions_v8 import feasible, EXPECTED
from load_server import server_for
SCENARIOS = ("flaky", "overload", "timeout")
from aegis_v6 import Estimator
from run_v7_2 import Worker
from train_v8 import FEATURES, STATES

MODES=('none','fixed','longest','learned')

def choose_action(mode, row, arms, artifact, cost):
    retry=[a for a in arms if a[1]>0]
    if mode=='none' or not retry: return (0,0)
    if mode=='fixed':
        options=[a for a in retry if a[0]==100 and a[1]<=1000]
        return max(options,key=lambda a:a[1]) if options else (0,0)
    if mode=='longest': return max(retry,key=lambda a:(a[1],-a[0]))
    hist=json.loads(row['history_json']); x=np.array([v[0] for v in hist],float); y=np.array([v[1] for v in hist])
    features=dict(row)
    features.update(zip(FEATURES[8:16],[x.mean(),np.median(x),np.percentile(x,90),x.std(),x.max(),(y=='success').mean(),(y=='http_error').mean(),(y=='transport_error').mean()]))
    frame=pd.DataFrame([dict(features,assigned_backoff_ms=b,assigned_timeout_ms=t) for b,t in retry])[FEATURES]
    if not np.isfinite(frame.to_numpy(float)).all(): return (0,0)
    p=artifact['model'].predict_proba(frame)[:,1]
    utility=p-cost-.05*np.array([(b+t)/max(row['remaining_ms'],1) for b,t in retry]); i=int(np.argmax(utility))
    return retry[i] if utility[i]>0 else (0,0)

def trial(worker,est,choose,deadline,reserve):
    start=time.perf_counter()
    def elapsed(): return (time.perf_counter()-start)*1000
    def remaining(): return deadline-elapsed()
    initial=min(2000,floor(remaining()-reserve))
    if initial<200: raise RuntimeError('Insufficient initial budget')
    lat,status,outcome=worker.attempt(initial)
    first_roundtrip=elapsed(); est.observe(lat,outcome)
    row=dict(deadline_ms=deadline,reserve_ms=reserve,first_timeout_ms=initial,first_latency_ms=lat,first_status=status,first_outcome=outcome,first_roundtrip_ms=first_roundtrip,history_json=json.dumps(list(est.history)),decision_ms=0,selection_ms=0,remaining_ms=None,rule_state=None,eligible=0,feasible_json='[]',propensity=None,assigned_backoff_ms=None,assigned_timeout_ms=None,dispatched=0,actual_backoff_ms=0,retry_latency_ms=None,retry_status=None,retry_outcome=None,retry_roundtrip_ms=None,execution_status='first_attempt_complete')
    row.update({f'p_{s}':None for s in STATES})
    success=outcome=='success'
    if not success and (outcome=='transport_error' or (outcome=='http_error' and status in (408,429,502,503,504))):
        t=time.perf_counter(); probs,rule=est.estimate(); row['decision_ms']=(time.perf_counter()-t)*1000
        row.update({f'p_{s}':probs[s] if probs else None for s in STATES}); row['rule_state']=rule
        rem=remaining(); arms=feasible(rem,reserve); row['remaining_ms']=rem; row['eligible']=1; row['feasible_json']=json.dumps(arms)
        t=time.perf_counter(); b,a=choose(row,arms); row['selection_ms']=(time.perf_counter()-t)*1000; assert (b,a) in arms; row.update(propensity=None,assigned_backoff_ms=b,assigned_timeout_ms=a,execution_status='policy_stop' if a==0 else 'cancelled_before_sleep')
        if a and remaining()>=b+a+reserve:
            t=time.perf_counter(); time.sleep(b/1000); row['actual_backoff_ms']=(time.perf_counter()-t)*1000
            row['execution_status']='cancelled_after_sleep'
            if remaining()>=a+reserve:
                row['dispatched']=1; t=time.perf_counter(); l,s,o=worker.attempt(a)
                row.update(retry_latency_ms=l,retry_status=s,retry_outcome=o,retry_roundtrip_ms=(time.perf_counter()-t)*1000,execution_status='dispatched'); success=o=='success'
    elif not success: row['execution_status']='nonretryable'
    e=elapsed(); row.update(elapsed_ms=e,success=int(success),deadline_success=int(success and e<=deadline),deadline_violation=int(e>deadline),attempts=1+row['dispatched'])
    return row

def summary(rows):
    groups=defaultdict(list)
    for r in rows:
        groups[(r['scenario'],r['mode'])].append(r)
        groups[('ALL',r['mode'])].append(r)
    result=[]
    for (s,m),g in sorted(groups.items()):
        n=len(g); eligible=sum(r['eligible'] for r in g); retries=sum(r['dispatched'] for r in g)
        result.append(dict(scenario=s,mode=m,runs=len({(r['scenario'],r['repeat']) for r in g}),operations=n,deadline_success_rate=sum(r['deadline_success'] for r in g)/n,success_rate=sum(r['success'] for r in g)/n,deadline_violations=sum(r['deadline_violation'] for r in g),attempt_amplification=sum(r['attempts'] for r in g)/n,p95_ms=float(np.percentile([r['elapsed_ms'] for r in g],95)),eligible=eligible,retries=retries,recovered=sum(r['deadline_success'] for r in g if r['eligible']),cancelled=sum(r['eligible'] and (r['assigned_timeout_ms'] or 0)>0 and not r['dispatched'] for r in g)))
    return result

def write(path,rows):
    if not rows:return
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--scenarios',nargs='+',choices=SCENARIOS,default=['flaky','overload','timeout']); p.add_argument('--modes',nargs='+',choices=MODES,default=list(MODES)); p.add_argument('--repeats',type=int,default=2); p.add_argument('--operations',type=int,default=20); p.add_argument('--seed',type=int,default=96317); p.add_argument('--deadlines',nargs='+',type=int,default=[2500,3000,4500]); p.add_argument('--reserve-ms',type=int,default=250); p.add_argument('--v5',type=Path,default=Path('frozen_v5/estimator.joblib')); p.add_argument('--v8',type=Path,default=Path('outputs_v8/outcome_model.joblib')); a=p.parse_args()
    if min(a.repeats,a.operations)<1 or a.reserve_ms!=250 or any(d not in (2500,3000,4500) for d in a.deadlines): p.error('use training budgets and positive counts')
    h=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    if h(a.v5)!=EXPECTED: raise RuntimeError('V5 hash mismatch')
    artifact=joblib.load(a.v8)
    if artifact['source_model_sha256']!=EXPECTED or list(artifact['features'])!=FEATURES: raise RuntimeError('incompatible outcome model')
    cost=float(artifact['retry_cost']); rng=random.Random(a.seed)
    blocks=[(s,r,rng.randrange(1,2**31),[rng.choice(a.deadlines) for _ in range(a.operations)]) for s in a.scenarios for r in range(1,a.repeats+1)]; rng.shuffle(blocks)
    plan=[]
    for s,r,seed,deadlines in blocks:
        modes=list(a.modes); rng.shuffle(modes)
        plan.extend(dict(scenario=s,repeat=r,seed=seed,deadlines=deadlines,mode=m) for m in modes)
    root=Path('benchmarks_load')/datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f'); root.mkdir(parents=True)
    (root/'plan.json').write_text(json.dumps(dict(plan=plan,parameters=vars(a),v5_sha256=h(a.v5),v8_sha256=h(a.v8),retry_cost=cost,scope='single retry GET; shared bounded service with open-loop background traffic; no hard deadline cancellation'),indent=2,default=str))
    rows=[]
    for i,b in enumerate(plan,1):
        s,r,seed,mode=(b[k] for k in ('scenario','repeat','seed','mode')); name=f'{s}_{r:02d}_{mode}'; print(f'[{i}/{len(plan)}] {name}',flush=True); est=Estimator(a.v5)
        with server_for(s,seed,root,name), Worker() as worker, (root/f'{name}.csv').open('w',newline='',encoding='utf-8') as f:
            for _ in range(15):
                l,_,o=worker.attempt(2000); est.observe(l,o)
            writer=None
            for op,deadline in enumerate(b['deadlines'],1):
                row=dict(scenario=s,repeat=r,seed=seed,mode=mode,operation=op,**trial(worker,est,lambda row,arms:choose_action(mode,row,arms,artifact,cost),deadline,a.reserve_ms)); rows.append(row)
                if writer is None: writer=csv.DictWriter(f,fieldnames=list(row)); writer.writeheader()
                writer.writerow(row); f.flush(); time.sleep(.2)
        write(root/'summary.csv',summary(rows)); print(json.dumps(summary([x for x in rows if x['scenario']==s and x['mode']==mode])[0]),flush=True)
    print('Benchmark complete:',root.resolve())
if __name__=='__main__':main()
