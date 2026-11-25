"""Randomized blocked V7 pilot. Run from project root with Windows Python."""
import argparse, csv, datetime, json, random, subprocess, sys, time
from pathlib import Path
from statistics import mean

MODES = ['none','static','rules','ai','hybrid']
SCENARIOS = ['normal','slow','flaky','overload','timeout']
WSL = ['wsl','-d','Ubuntu-20.04']

def port_open():
    result = subprocess.run(WSL+['python3','-c',
        "import socket; s=socket.socket(); s.settimeout(.5); print(s.connect_ex(('127.0.0.1',8080))==0); s.close()"], capture_output=True,text=True,check=True)
    return 'True' in result.stdout.splitlines()

def percentile(xs,p):
    xs=sorted(xs); i=(len(xs)-1)*p; lo=int(i); hi=min(lo+1,len(xs)-1)
    return xs[lo]+(xs[hi]-xs[lo])*(i-lo)

def write(path,rows):
    if not rows: return
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def summarize(root,rows):
    write(root/'runs.csv',rows)
    summary=[]
    for scenario in SCENARIOS:
        for mode in MODES:
            group=[r for r in rows if r['scenario']==scenario and r['mode']==mode]
            if not group: continue
            summary.append(dict(scenario=scenario,mode=mode,runs=len(group),operations=sum(r['operations'] for r in group),**{k:mean(r[k] for r in group) for k in ['success_rate','deadline_success_rate','deadline_violation_rate','attempt_amplification','p95_ms']}))
    write(root/'summary.csv',summary)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--scenarios',nargs='+',choices=SCENARIOS,default=SCENARIOS); p.add_argument('--repeats',type=int,default=3); p.add_argument('--operations',type=int,default=30); p.add_argument('--deadline-ms',type=int,default=3000); p.add_argument('--seed',type=int,default=84621); a=p.parse_args()
    if a.repeats<1 or a.operations<1 or a.deadline_ms<250: p.error('invalid parameters')
    if port_open(): raise RuntimeError('Stop the existing server on port 8080 first')
    root=Path('benchmarks_v7')/datetime.datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f'); root.mkdir(parents=True)
    rng=random.Random(a.seed); blocks=[(s,r,rng.randrange(1,2**31)) for s in a.scenarios for r in range(1,a.repeats+1)]; rng.shuffle(blocks)
    plan=[]
    for s,r,seed in blocks:
        modes=MODES.copy(); rng.shuffle(modes)
        plan.extend(dict(scenario=s,repeat=r,seed=seed,mode=m) for m in modes)
    (root/'plan.json').write_text(json.dumps(dict(parameters=vars(a),plan=plan),indent=2))
    rows=[]
    for i,b in enumerate(plan,1):
        name=f"{b['scenario']}_{b['repeat']:02d}_{b['mode']}"; out=root/name
        print(f'[{i}/{len(plan)}] {name}',flush=True)
        if port_open(): raise RuntimeError('Port occupied before run')
        # Shell trap kills the exact child server on graceful termination; explicit PID file is used for cleanup.
        pidfile=f'.aegis_bench_{root.name}_{i}.pid'
        command=f"echo $$ > {pidfile}; exec python3 -u challenge_v4.py serve --scenario {b['scenario']} --seed {b['seed']}"
        with (root/f'{name}_server.log').open('w') as log:
            server=subprocess.Popen(WSL+['bash','-c',command],stdout=log,stderr=subprocess.STDOUT)
            try:
                for _ in range(60):
                    if server.poll() is not None: raise RuntimeError('Fault server exited; inspect server log')
                    if port_open(): break
                    time.sleep(.2)
                else: raise RuntimeError('Fault server not ready')
                with (root/f'{name}_client.log').open('w') as clientlog:
                    subprocess.run([sys.executable,'run_v7.py','--mode',b['mode'],'--operations',str(a.operations),'--deadline-ms',str(a.deadline_ms),'--max-retries','2','--out',str(out)],stdout=clientlog,stderr=subprocess.STDOUT,check=True)
                report=json.loads((out/'report.json').read_text());
                if not report['complete'] or report['completed']!=a.operations: raise RuntimeError('Incomplete run')
                with (out/'operations.csv').open(newline='') as f: ops=list(csv.DictReader(f))
                if len(ops)!=a.operations: raise RuntimeError('Invalid operations count')
                rows.append(dict(**b,operations=a.operations,success_rate=report['success_rate'],deadline_success_rate=report['deadline_success_rate'],deadline_violation_rate=report['deadline_exceeded']/a.operations,attempt_amplification=report['attempt_amplification'],p95_ms=percentile([float(o['elapsed_ms']) for o in ops],.95)))
                summarize(root,rows); print(json.dumps(rows[-1]),flush=True)
            finally:
                subprocess.run(WSL+['bash','-c',f'if test -f {pidfile}; then kill "$(cat {pidfile})" 2>/dev/null || true; rm -f {pidfile}; fi'],check=False)
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired: server.terminate(); server.wait(timeout=10)
        for _ in range(30):
            if not port_open(): break
            time.sleep(.2)
        else: raise RuntimeError('Server port still occupied after cleanup')
    print('Benchmark complete:',root.resolve())
if __name__=='__main__': main()
