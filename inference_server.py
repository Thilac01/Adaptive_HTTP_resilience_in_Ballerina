"""Local, stateless inference adapter for the frozen Aegis models. Run from project root."""
import argparse, hashlib, json, math
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import joblib
from aegis_v6 import Estimator
from aegis_core import choose_action
from collect_actions_v8 import feasible, EXPECTED
from train_v8 import FEATURES, STATES

V8_HASH='473c6b84ae1c2413e85c4ceb1c5da58a2c750a1e16b615a53e1018445d94d512'

def decide(data, estimator, artifact):
    history=data['history']; estimator.history.clear()
    for latency,outcome in history: estimator.observe(float(latency),outcome)
    probs,_=estimator.estimate()
    if probs is None: return {'backoffMs':0,'timeoutMs':0}
    row={k:float(data[k]) for k in FEATURES[:8] if k!='decision_ms'}
    row['decision_ms']=0.0
    row.update({f'p_{s}':probs[s] for s in STATES})
    row['history_json']=json.dumps(history)
    if not all(math.isfinite(v) for k,v in row.items() if k!='history_json'): raise ValueError('nonfinite input')
    arms=feasible(row['remaining_ms'],row['reserve_ms'])
    b,t=choose_action('learned',row,arms,artifact,float(artifact['retry_cost']))
    return {'backoffMs':int(b),'timeoutMs':int(t)}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8091); a=p.parse_args()
    v5=Path('frozen_v5/estimator.joblib'); v8=Path('frozen_v8/outcome_model.joblib')
    for path,expected in [(v5,EXPECTED),(v8,V8_HASH)]:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise RuntimeError(f'Unexpected model hash: {path}')
    estimator=Estimator(v5); artifact=joblib.load(v8)
    if list(artifact['features'])!=FEATURES or artifact['source_model_sha256']!=EXPECTED: raise RuntimeError('Model schema mismatch')
    import threading
    lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def send_json(self,status,obj):
            body=json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            self.send_json(200 if self.path=='/health' else 404,{'ready':self.path=='/health'})
        def do_POST(self):
            if self.path!='/decide': return self.send_json(404,{'error':'not found'})
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=16384: raise ValueError('invalid size')
                data=json.loads(self.rfile.read(n))
                if not isinstance(data['history'],list) or len(data['history'])>10: raise ValueError('invalid history')
                with lock: result=decide(data,estimator,artifact)
            except (ValueError,KeyError,TypeError,IndexError): return self.send_json(400,{'error':'invalid input'})
            self.send_json(200,result)
    print(f'Aegis inference listening on {a.host}:{a.port}',flush=True)
    ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()
if __name__=='__main__': main()
