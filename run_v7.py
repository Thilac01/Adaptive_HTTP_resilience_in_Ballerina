"""V7 local GET-only integration pilot. Run with Windows Python from project root."""
import argparse
import csv
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from aegis_v6 import Context, Decision, Estimator, decide

EXPECTED = 'c5dac5c150f177821d0e1ef66eb6743cfa1a42d1c50de27551ca324131054f21'

class Worker:
    def __enter__(self):
        self.q = queue.Queue()
        self.p = subprocess.Popen(['wsl','-d','Ubuntu-20.04','bash','-ic','bal run aegis_worker.bal'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        def reader():
            for line in self.p.stdout:
                self.q.put(line.strip())
            self.q.put(None)
        threading.Thread(target=reader, daemon=True).start()
        try:
            self.receive('AEGIS_READY', 180)
        except BaseException:
            self.close(); raise
        return self
    def receive(self, prefix, timeout):
        end = time.monotonic()+timeout
        while True:
            try: line = self.q.get(timeout=max(0,end-time.monotonic()))
            except queue.Empty: raise RuntimeError('Worker watchdog expired; run aborted, no further attempts') from None
            if line is None: raise RuntimeError('Ballerina worker exited')
            if line.startswith(prefix): return line
            print(line, flush=True)
    def attempt(self, budget):
        self.p.stdin.write(f'{int(budget)}\n'); self.p.stdin.flush()
        line = self.receive('AEGIS_RESULT,', max(30,budget/1000+10))
        _,lat,status,outcome = line.split(',')
        return float(lat),int(status),outcome
    def close(self):
        if self.p.poll() is None:
            try:
                self.p.stdin.write('quit\n'); self.p.stdin.flush(); self.p.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):
                self.p.terminate()
                try: self.p.wait(timeout=5)
                except subprocess.TimeoutExpired: self.p.kill(); self.p.wait()
    def __exit__(self,*args): self.close()

def policy(mode, estimator, ctx):
    if mode=='none': return Decision('stop','baseline','no retries')
    if mode=='static':
        # Fixed settings with the same basic safety checks. Not native Ballerina retry.
        if ctx.outcome=='success': return Decision('complete','fixed','success')
        if not ctx.safe_to_repeat or ctx.retries_used>=ctx.max_retries or (ctx.outcome=='http_error' and ctx.status_code not in (408,429,502,503,504)) or ctx.outcome not in ('http_error','transport_error'):
            return Decision('stop','fixed','safety gate')
        delay=max(200,ctx.retry_after_ms)
        budget=min(1000,int(ctx.remaining_ms-delay-50))
        if budget<200: return Decision('stop','fixed','insufficient budget')
        return Decision('retry','fixed','fixed retry',int(delay),budget)
    return estimator.decision(ctx,mode)

def operation(worker, estimator, mode, deadline, max_retries, op_id):
    started=time.perf_counter(); attempts=[]; decisions=[]; budget=min(2000,deadline-50); retries=0
    def remaining(): return deadline-(time.perf_counter()-started)*1000
    while budget>=200:
        lat,status,outcome=worker.attempt(budget)
        attempts.append(dict(operation=op_id,attempt=len(attempts)+1,latency_ms=lat,status_code=status,outcome=outcome,timeout_ms=budget))
        # Preserve V5 observation definition as far as practical: first attempts only.
        if retries==0: estimator.observe(lat,outcome)
        if outcome=='success': break
        ctx=Context(True,max(0,remaining()),retries,max_retries,outcome,status)
        decision=policy(mode,estimator,ctx)
        decisions.append(dict(operation=op_id,after_attempt=len(attempts),**vars(decision)))
        if decision.action!='retry': break
        if remaining()<decision.backoff_ms+decision.attempt_ms+50: break
        time.sleep(decision.backoff_ms/1000)
        budget=min(decision.attempt_ms,int(remaining()-50))
        if budget<200: break
        retries+=1
    elapsed=(time.perf_counter()-started)*1000
    success=bool(attempts and attempts[-1]['outcome']=='success')
    return dict(operation=op_id,mode=mode,elapsed_ms=elapsed,success=success,within_deadline=success and elapsed<=deadline,deadline_exceeded=elapsed>deadline,attempts=len(attempts)),attempts,decisions

def write_csv(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=['none','static','rules','ai','hybrid'],default='hybrid'); p.add_argument('--operations',type=int,default=20); p.add_argument('--deadline-ms',type=int,default=3000); p.add_argument('--max-retries',type=int,default=2); p.add_argument('--warmup',type=int,default=15); p.add_argument('--model',default='frozen_v5/estimator.joblib'); p.add_argument('--out',default='outputs_v7_smoke'); a=p.parse_args()
    if a.operations<1 or a.deadline_ms<250 or a.max_retries<0 or a.warmup<10: p.error('invalid experimental parameters')
    est=Estimator(a.model)
    if est.sha256!=EXPECTED: raise RuntimeError('Frozen model hash mismatch')
    out=Path(a.out); out.mkdir(parents=True,exist_ok=False)
    rows=[]; attempts=[]; decisions=[]; warm=[]
    try:
        with Worker() as worker:
            for i in range(a.warmup):
                lat,status,result=worker.attempt(2000); est.observe(lat,result); warm.append(dict(operation=i+1,attempt=1,latency_ms=lat,status_code=status,outcome=result,timeout_ms=2000))
            for i in range(1,a.operations+1):
                row,aa,dd=operation(worker,est,a.mode,a.deadline_ms,a.max_retries,i); rows.append(row); attempts.extend(aa); decisions.extend(dd); print(json.dumps(row),flush=True); time.sleep(.2)
    finally:
        af=['operation','attempt','latency_ms','status_code','outcome','timeout_ms']
        write_csv(out/'warmup.csv',warm,af); write_csv(out/'attempts.csv',attempts,af); write_csv(out/'operations.csv',rows,['operation','mode','elapsed_ms','success','within_deadline','deadline_exceeded','attempts']); write_csv(out/'decisions.csv',decisions,['operation','after_attempt','action','state','reason','backoff_ms','attempt_ms'])
        report=dict(mode=a.mode,model_sha256=est.sha256,completed=len(rows),requested=a.operations,complete=len(rows)==a.operations,deadline_ms=a.deadline_ms,max_retries=a.max_retries,success_rate=sum(r['success'] for r in rows)/len(rows) if rows else None,deadline_success_rate=sum(r['within_deadline'] for r in rows)/len(rows) if rows else None,deadline_exceeded=sum(r['deadline_exceeded'] for r in rows),attempt_amplification=len(attempts)/len(rows) if rows else None)
        (out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))
if __name__=='__main__': main()
