"""Aegis V4: independent variable-severity scenario challenge.
Run collection in WSL; evaluate in the Windows ML environment.
Labels identify configured profiles, not inferred physical root causes.
"""
import argparse, csv, datetime, json, random, subprocess, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LABELS = ['normal','slow','flaky','overload','timeout']
# Delay ranges in seconds, HTTP 503 probability range, long-delay probability range.
PROFILES = {
 'normal': ((.02,.18),(0,0),(0,0)),
 'slow': ((.12,.85),(0,0),(0,0)),
 'flaky': ((.03,.35),(.08,.55),(0,0)),
 'overload': ((.18,1.2),(.15,.75),(0,0)),
 'timeout': ((.08,.8),(0,.15),(.2,.85)),
}

def serve(a):
    rng=random.Random(a.seed)
    delay, errors, stalls=PROFILES[a.scenario]
    base=rng.uniform(*delay); error=rng.uniform(*errors); stall=rng.uniform(*stalls)
    print(json.dumps(dict(base_delay_s=base,error_probability=error,stall_probability=stall)),flush=True)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            d=rng.uniform(.65*base,1.35*base)
            if rng.random()<stall: d=rng.uniform(2.5,4)
            status=503 if rng.random()<error else 200
            time.sleep(d)
            payload=b'{}'
            try:
                self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError): pass
            print(json.dumps(dict(delay_s=d,status=status)),flush=True)
        def log_message(self,*args): pass
    ThreadingHTTPServer(('127.0.0.1',8080),Handler).serve_forever()

def collect(a):
    from collect_dataset import port_open, parse_log, FIELDS
    if a.repeats<1: raise ValueError('repeats must be positive')
    if port_open(): raise RuntimeError('Stop existing server on port 8080')
    root=Path('data_v4')/datetime.datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f'); root.mkdir(parents=True)
    rng=random.Random(a.seed)
    plan=[(s,r,rng.randrange(2**32)) for s in LABELS for r in range(1,a.repeats+1)]; rng.shuffle(plan)
    (root/'metadata.json').write_text(json.dumps(dict(plan=plan,seed=a.seed,profiles=PROFILES,requests=60,warmup=15),indent=2))
    for i,(s,r,seed) in enumerate(plan,1):
        name=f'{s}_{r:02d}'; print(f'[{i}/{len(plan)}] {name}',flush=True)
        with (root/f'{name}_server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'serve','--scenario',s,'--seed',str(seed)],stdout=log,stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if server.poll() is not None: raise RuntimeError('Server failed')
                    if port_open(): break
                    time.sleep(.1)
                else: raise RuntimeError('Server not ready')
                with (root/f'{name}_client.log').open('w') as f:
                    subprocess.run(['bal','run','collector.bal','--','-Crequests=60'],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=720)
                rows=parse_log((root/f'{name}_client.log').read_text(errors='replace'),60)
                with (root/f'{name}.csv').open('w',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=['run_id','scenario','repeat','warmup']+FIELDS); w.writeheader()
                    for row in rows: w.writerow(dict(row,run_id=name,scenario=s,repeat=r,warmup=int(int(row['request_id'])<=15)))
            finally:
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: server.kill(); server.wait()
    print('Collection complete:',root.resolve())

def evaluate(a):
    import joblib, numpy as np, pandas as pd
    from train_v3 import rules, scores, FEATURES
    from sklearn.metrics import confusion_matrix
    artifact=joblib.load(a.model); model=artifact['model']; n=artifact['window_requests']; threshold=artifact['threshold']
    batch=Path(a.batch); meta=json.loads((batch/'metadata.json').read_text()); rows=[]
    for s,r,_ in meta['plan']:
        name=f'{s}_{r:02d}'; d=pd.read_csv(batch/f'{name}.csv').sort_values('request_id')
        if list(d.request_id)!=list(range(1,61)) or list(d.warmup)!=[1]*15+[0]*45 or set(d.scenario)!={s}: raise ValueError(name)
        d=d[d.warmup==0]
        for j in range(0,len(d)-n+1,n):
            w=d.iloc[j:j+n]; x=w.latency_ms.to_numpy()
            rows.append(dict(run_id=name,scenario=s,first_request=int(w.request_id.iloc[0]),mean_ms=x.mean(),median_ms=np.median(x),p90_ms=np.percentile(x,90),std_ms=x.std(),max_ms=x.max(),success_rate=(w.outcome=='success').mean(),http_error_rate=(w.outcome=='http_error').mean(),transport_error_rate=(w.outcome=='transport_error').mean()))
    d=pd.DataFrame(rows); p=model.predict_proba(d[artifact['features']]); d['predicted']=model.classes_[p.argmax(1)]; d['confidence']=p.max(1); d['accepted']=d.confidence>=threshold; d['rule_prediction']=rules(d)
    mask=d.accepted; out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    report=dict(scope='independent variable-severity application-level challenge; frozen V3 models',runs=int(d.run_id.nunique()),windows=len(d),model=scores(d.scenario,d.predicted),rules=scores(d.scenario,d.rule_prediction),threshold=threshold,coverage=float(mask.mean()),accepted_accuracy=float((d.loc[mask,'scenario']==d.loc[mask,'predicted']).mean()) if mask.any() else None)
    d.to_csv(out/'predictions.csv',index=False)
    for col in ['predicted','rule_prediction']: pd.DataFrame(confusion_matrix(d.scenario,d[col],labels=LABELS),index=LABELS,columns=LABELS).to_csv(out/f'confusion_{col}.csv')
    (out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    c=sub.add_parser('collect'); c.add_argument('--repeats',type=int,default=5); c.add_argument('--seed',type=int,default=731)
    s=sub.add_parser('serve'); s.add_argument('--scenario',choices=LABELS,required=True); s.add_argument('--seed',type=int,required=True)
    e=sub.add_parser('evaluate'); e.add_argument('--batch',required=True); e.add_argument('--model',default='outputs_v3/estimator.joblib'); e.add_argument('--out',default='outputs_v4')
    a=p.parse_args(); {'collect':collect,'serve':serve,'evaluate':evaluate}[a.cmd](a)
if __name__=='__main__': main()
