"""Run in WSL from project root. Standard library only; inference server must already run."""
import argparse, csv, datetime, json, random, subprocess, threading, time, urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROFILES = {'normal': (.04,0,0), 'flaky': (.12,.35,0), 'overload': (.65,.45,0), 'timeout': (.45,.05,.50)}
MODES = ['native_none','native_retry','aegis_fixed','aegis_learned']

def server_for(scenario, seed):
    counts = Counter(); events = []; lock = threading.Lock()
    base, errors, stalls = PROFILES[scenario]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                prefix, op = self.path.rsplit('/',1); op = int(op)
                if prefix != '/op' or op < 1: raise ValueError()
            except ValueError:
                self.send_error(404); return
            with lock:
                counts[op] += 1; attempt = counts[op]
                rng = random.Random('%s:%s:%s' % (seed,op,attempt))
                delay = rng.uniform(.65*base,1.35*base)
                if rng.random() < stalls: delay = rng.uniform(2.5,4)
                status = 503 if rng.random() < errors else 200
                events.append(dict(operation=op,attempt=attempt,planned_status=status,delay_ms=delay*1000))
            time.sleep(delay)
            try:
                self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'{}')
            except (BrokenPipeError,ConnectionResetError): pass
        def log_message(self,*args): pass
    server = ThreadingHTTPServer(('127.0.0.1',8080),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    return server, thread, counts, events

def write_csv(path, rows):
    if not rows: return
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def quantile(xs,p):
    xs=sorted(xs); z=(len(xs)-1)*p; i=int(z); return xs[i]+(xs[min(i+1,len(xs)-1)]-xs[i])*(z-i)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--scenarios',nargs='+',choices=PROFILES,default=list(PROFILES)); p.add_argument('--modes',nargs='+',choices=MODES,default=MODES); p.add_argument('--repeats',type=int,default=3); p.add_argument('--operations',type=int,default=30); p.add_argument('--warmup',type=int,default=15); p.add_argument('--seed',type=int,default=98173); p.add_argument('--inference-url',default='http://127.0.0.1:8091'); a=p.parse_args()
    if min(a.repeats,a.operations)<1 or a.warmup<10: p.error('positive repeats/operations and warmup >=10 required')
    if 'aegis_learned' in a.modes:
        with urllib.request.urlopen(a.inference_url+'/health',timeout=5) as response:
            if not json.load(response).get('ready'): raise RuntimeError('Inference not ready')
    root=Path('benchmarks_native')/datetime.datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f'); root.mkdir(parents=True)
    rng=random.Random(a.seed); plan=[]
    for s in a.scenarios:
        for r in range(1,a.repeats+1):
            seed=rng.randrange(2**32); modes=list(a.modes); rng.shuffle(modes); plan.extend((s,r,seed,m) for m in modes)
    (root/'metadata.json').write_text(json.dumps(dict(parameters=vars(a),plan=plan,profiles=PROFILES,scope='matched logical-operation and HTTP-arrival fault draws; application emulation; server counts are received HTTP requests, not client transport attempts'),indent=2))
    rows=[]
    for j,(s,r,seed,m) in enumerate(plan,1):
        name=f'{s}_{r:02d}_{m}'; print(f'[{j}/{len(plan)}] {name}',flush=True)
        server,thread,counts,events=server_for(s,seed)
        try:
            native=m.startswith('native_'); directory='native_baseline' if native else 'native_demo'
            args=['bal','run','--offline','--',f'-Coperations={a.operations}',f'-Cwarmup={a.warmup}']
            args += [f'-CretryCount={int(m=="native_retry")}'] if native else [f'-Cmode={m.split("_")[1]}',f'-CinferenceUrl={a.inference_url}']
            with (root/f'{name}.log').open('w') as f:
                subprocess.run(args,cwd=directory,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=900)
            data=[json.loads(line.split('AEGIS_RESULT,',1)[1]) for line in (root/f'{name}.log').read_text().splitlines() if line.startswith('AEGIS_RESULT,')]
            if [x['operation'] for x in data]!=list(range(1,a.operations+1)): raise RuntimeError(f'Unexpected output: {name}')
        finally:
            server.shutdown(); server.server_close(); thread.join()
        write_csv(root/f'{name}_server.csv',events)
        for x in data:
            rows.append(dict(scenario=s,repeat=r,seed=seed,mode=m,operation=x['operation'],status=x['status'],outcome=x['outcome'],elapsed_ms=x['elapsedMs'],deadline_success=int(x['deadlineSuccess']),deadline_violation=int(x['deadlineViolation']),server_requests=counts[x['operation']+a.warmup],client_attempts=x.get('attempts','')))
        write_csv(root/'operations.csv',rows)
    summary=[]
    for s in ['ALL']+a.scenarios:
        for m in a.modes:
            d=[x for x in rows if x['mode']==m and (s=='ALL' or x['scenario']==s)]
            summary.append(dict(scenario=s,mode=m,operations=len(d),deadline_success_rate=sum(x['deadline_success'] for x in d)/len(d),deadline_violations=sum(x['deadline_violation'] for x in d),server_request_amplification=sum(x['server_requests'] for x in d)/len(d),p95_ms=quantile([x['elapsed_ms'] for x in d],.95)))
    write_csv(root/'summary.csv',summary); print('Complete:',root)
if __name__=='__main__': main()
