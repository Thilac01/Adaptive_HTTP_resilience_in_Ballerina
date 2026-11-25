"""Aegis V6 policy prototype. No HTTP execution; budgets are milliseconds."""
from dataclasses import dataclass
from math import isfinite
from collections import deque
import argparse
import json
import hashlib

STATES = ('normal','slow','flaky','overload','timeout')
# Hypothesized policy settings, not experimentally optimized.
BACKOFF = {'normal':100,'slow':150,'flaky':100,'overload':500,'timeout':300,'uncertain':300}
ATTEMPT = {'normal':500,'slow':1200,'flaky':700,'overload':1000,'timeout':1500,'uncertain':700}

@dataclass(frozen=True)
class Context:
    safe_to_repeat: bool
    remaining_ms: float
    retries_used: int
    max_retries: int
    outcome: str
    status_code: int = 0
    retry_after_ms: float = 0

@dataclass(frozen=True)
class Decision:
    action: str
    state: str
    reason: str
    backoff_ms: int = 0
    attempt_ms: int = 0


def decide(ctx, probabilities=None, rule_state=None, threshold=.55, mode='hybrid'):
    if mode not in ('hybrid','ai','rules'):
        raise ValueError('invalid mode')
    if not all(isfinite(v) and v >= 0 for v in (ctx.remaining_ms,ctx.retry_after_ms,ctx.retries_used,ctx.max_retries)):
        raise ValueError('invalid budget')
    if not 0 <= threshold <= 1: raise ValueError('invalid threshold')
    state='uncertain'
    if mode=='rules':
        state=rule_state if rule_state in STATES else 'uncertain'
    elif probabilities is not None:
        if set(probabilities)!=set(STATES) or any(not isfinite(p) or not 0<=p<=1 for p in probabilities.values()) or abs(sum(probabilities.values())-1)>1e-5:
            raise ValueError('invalid probabilities')
        best=max(STATES,key=lambda s: probabilities[s])
        if probabilities[best]>=threshold and (mode=='ai' or best==rule_state): state=best
    def stop(reason): return Decision('stop',state,reason)
    if ctx.outcome=='success': return Decision('complete',state,'successful response')
    if ctx.safe_to_repeat is not True: return stop('operation not established safe to repeat')
    if ctx.retries_used>=ctx.max_retries: return stop('retry budget exhausted')
    if ctx.outcome not in ('transport_error','http_error'): return stop('unknown outcome')
    if ctx.outcome=='http_error' and ctx.status_code not in (408,429,502,503,504): return stop('status not on retry allowlist')
    # Conservative fallback and overload suppression; test against alternatives later.
    if state in ('overload','uncertain') and ctx.retries_used>=1: return stop('conservative retry limit')
    import math
    delay=max(BACKOFF[state]*(2**min(ctx.retries_used,4)),math.ceil(ctx.retry_after_ms))
    # Reserve 50 ms for local scheduling/decision overhead; not a hard runtime guarantee.
    budget=math.floor(ctx.remaining_ms-delay-50)
    if budget<200: return stop('insufficient deadline budget')
    attempt=min(ATTEMPT[state],budget)
    return Decision('retry',state,'bounded retry',delay,attempt)


class Estimator:
    """Adapter for the exact V5 artifact schema. Use only trusted local joblib files."""
    def __init__(self,path):
        import joblib
        from pathlib import Path
        self.sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.art=joblib.load(path)
        self.history=deque(maxlen=self.art['window_requests'])
    def observe(self,latency_ms,outcome):
        if not isfinite(latency_ms) or latency_ms<0 or outcome not in ('success','http_error','transport_error'):
            raise ValueError('invalid observation')
        self.history.append((latency_ms,outcome))
    def estimate(self):
        import numpy as np
        import pandas as pd
        from train_v5 import tuned_rules
        if len(self.history)<self.history.maxlen: return None,None
        x=np.array([r[0] for r in self.history]); y=np.array([r[1] for r in self.history])
        d=pd.DataFrame([dict(mean_ms=x.mean(),median_ms=np.median(x),p90_ms=np.percentile(x,90),std_ms=x.std(),max_ms=x.max(),success_rate=(y=='success').mean(),http_error_rate=(y=='http_error').mean(),transport_error_rate=(y=='transport_error').mean())])
        m=self.art['model']; p=m.predict_proba(d[self.art['features']])[0]
        return dict(zip(m.classes_,map(float,p))),str(tuned_rules(d,self.art['rule_params'])[0])
    def decision(self,ctx,mode='hybrid'):
        p,r=self.estimate(); t=self.art['threshold']
        return decide(ctx,p if t is not None or mode=='rules' else None,r,t if t is not None else 1,mode)


def main():
    from dataclasses import asdict
    parser=argparse.ArgumentParser(); parser.add_argument('--model'); a=parser.parse_args()
    if a.model:
        e=Estimator(a.model); print('Model SHA256:',e.sha256)
        # Synthetic smoke input, not experimental performance evidence.
        for i in range(e.history.maxlen): e.observe(80,'http_error' if i%3==0 else 'success')
        p,r=e.estimate(); print(json.dumps({'probabilities':p,'rule_state':r},indent=2))
        print(json.dumps(asdict(e.decision(Context(True,2000,0,2,'http_error',503))),indent=2))
    else:
        p={s:(.8 if s=='flaky' else .05) for s in STATES}
        for c in (Context(True,2000,0,2,'http_error',503),Context(False,2000,0,2,'http_error',503),Context(True,250,0,2,'transport_error')):
            print(json.dumps(asdict(decide(c,p,'flaky'))))
if __name__=='__main__': main()
