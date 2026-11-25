"""AEGIS V8 single-retry randomized action pilot. Windows Python, existing WSL worker."""
import argparse, csv, hashlib, json, random, subprocess, time
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from math import floor

ARMS = [(b,t) for b in (0,100,300,500) for t in (200,400,700,1000,1500)]
STATES = ('normal','slow','flaky','overload','timeout')
EXPECTED = 'c5dac5c150f177821d0e1ef66eb6743cfa1a42d1c50de27551ca324131054f21'
WSL=['wsl','-d','Ubuntu-20.04']

def feasible(remaining,reserve):
    return [(0,0)]+[(b,t) for b,t in ARMS if b+t+reserve<=remaining]

@contextmanager
def server_for(scenario,seed,root,run):
    from benchmark_v7_2 import port_open
    if port_open(): raise RuntimeError('Stop existing server on 8080')
    pidfile=f'.aegis_v8_{root.name}_{run}.pid'
    with (root/f'{run}_server.log').open('w') as log:
        cmd=f'echo $$ > {pidfile}; exec python3 -u challenge_v4.py serve --scenario {scenario} --seed {seed}'
        p=subprocess.Popen(WSL+['bash','-c',cmd],stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                if p.poll() is not None: raise RuntimeError('Server exited')
                if port_open(): break
                time.sleep(.2)
            else: raise RuntimeError('Server not ready')
            yield
        finally:
            subprocess.run(WSL+['bash','-c',f'if test -f {pidfile}; then kill "$(cat {pidfile})" 2>/dev/null || true; rm -f {pidfile}; fi'],check=False)
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.terminate(); p.wait(timeout=10)

def trial(worker,est,rng,deadline,reserve):
    start=time.perf_counter()
    def elapsed(): return (time.perf_counter()-start)*1000
    def remaining(): return deadline-elapsed()
    initial=min(2000,floor(remaining()-reserve))
    if initial<200: raise RuntimeError('Insufficient initial budget')
    lat,status,outcome=worker.attempt(initial)
    first_roundtrip=elapsed(); est.observe(lat,outcome)
    row=dict(deadline_ms=deadline,reserve_ms=reserve,first_timeout_ms=initial,first_latency_ms=lat,first_status=status,first_outcome=outcome,first_roundtrip_ms=first_roundtrip,history_json=json.dumps(list(est.history)),decision_ms=0,remaining_ms=None,rule_state=None,eligible=0,feasible_json='[]',propensity=None,assigned_backoff_ms=None,assigned_timeout_ms=None,dispatched=0,actual_backoff_ms=0,retry_latency_ms=None,retry_status=None,retry_outcome=None,retry_roundtrip_ms=None,execution_status='first_attempt_complete')
    row.update({f'p_{s}':None for s in STATES})
    success=outcome=='success'
    if not success and (outcome=='transport_error' or (outcome=='http_error' and status in (408,429,502,503,504))):
        t=time.perf_counter(); probs,rule=est.estimate(); row['decision_ms']=(time.perf_counter()-t)*1000
        row.update({f'p_{s}':probs[s] if probs else None for s in STATES}); row['rule_state']=rule
        rem=remaining(); arms=feasible(rem,reserve); row['remaining_ms']=rem; row['eligible']=1; row['feasible_json']=json.dumps(arms)
        b,a=rng.choice(arms); row.update(propensity=1/len(arms),assigned_backoff_ms=b,assigned_timeout_ms=a,execution_status='randomized_stop' if a==0 else 'cancelled_before_sleep')
        if a and remaining()>=b+a+reserve:
            t=time.perf_counter(); time.sleep(b/1000); row['actual_backoff_ms']=(time.perf_counter()-t)*1000
            row['execution_status']='cancelled_after_sleep'
            if remaining()>=a+reserve:
                row['dispatched']=1; t=time.perf_counter(); l,s,o=worker.attempt(a)
                row.update(retry_latency_ms=l,retry_status=s,retry_outcome=o,retry_roundtrip_ms=(time.perf_counter()-t)*1000,execution_status='dispatched'); success=o=='success'
    elif not success: row['execution_status']='nonretryable'
    e=elapsed(); row.update(elapsed_ms=e,success=int(success),deadline_success=int(success and e<=deadline),deadline_violation=int(e>deadline),attempts=1+row['dispatched'])
    return row

def main():
    p=argparse.ArgumentParser(); p.add_argument('--scenarios',nargs='+',choices=STATES,default=['flaky','overload','timeout']); p.add_argument('--repeats',type=int,default=2); p.add_argument('--operations',type=int,default=30); p.add_argument('--seed',type=int,default=81821); p.add_argument('--deadlines',nargs='+',type=int,default=[2500,3000,4500]); p.add_argument('--reserve-ms',type=int,default=250); p.add_argument('--model',default='frozen_v5/estimator.joblib'); a=p.parse_args()
    if a.repeats<1 or a.operations<1 or a.reserve_ms<0 or min(a.deadlines)<a.reserve_ms+200: p.error('invalid budgets/counts')
    from run_v7_2 import Worker
    from aegis_v6 import Estimator
    if hashlib.sha256(Path(a.model).read_bytes()).hexdigest()!=EXPECTED: raise RuntimeError('Frozen model hash mismatch')
    rng=random.Random(a.seed); plan=[(s,r,rng.randrange(1,2**31),rng.randrange(1,2**31)) for s in a.scenarios for r in range(1,a.repeats+1)]; rng.shuffle(plan)
    root=Path('data_v8_actions')/datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f'); root.mkdir(parents=True)
    (root/'metadata.json').write_text(json.dumps(dict(parameters=vars(a),plan=plan,arms=ARMS,model_sha256=EXPECTED,scope='GET only; one randomized retry or stop; application-level faults'),indent=2))
    for i,(s,r,seed,action_seed) in enumerate(plan,1):
        run=f'{s}_{r:02d}'; print(f'[{i}/{len(plan)}] {run}',flush=True); arng=random.Random(action_seed); est=Estimator(a.model)
        with server_for(s,seed,root,run), Worker() as worker, (root/f'{run}.csv').open('w',newline='',encoding='utf-8') as f:
            for _ in range(15):
                l,_,o=worker.attempt(2000); est.observe(l,o)
            writer=None; count=0; dispatched=0; successes=0
            for op in range(1,a.operations+1):
                row=dict(run_id=run,scenario=s,repeat=r,server_seed=seed,operation=op,**trial(worker,est,arng,arng.choice(a.deadlines),a.reserve_ms))
                if writer is None: writer=csv.DictWriter(f,fieldnames=list(row)); writer.writeheader()
                writer.writerow(row); f.flush(); count+=row['eligible']; dispatched+=row['dispatched']; successes+=row['deadline_success']; time.sleep(.2)
            print(json.dumps(dict(run_id=run,operations=a.operations,eligible=count,dispatched=dispatched,deadline_successes=successes)),flush=True)
    print('Collection complete:',root.resolve())

if __name__=='__main__': main()
