"""V8 randomized single-retry outcome modeling. Fit on whole runs, evaluate on held-out runs.
Only trusted local joblib artifacts should be loaded. No server scenario is a model feature.
"""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import log_loss,brier_score_loss,roc_auc_score
STATES=('normal','slow','flaky','overload','timeout')
FEATURES=['deadline_ms','reserve_ms','first_timeout_ms','first_latency_ms','first_roundtrip_ms','first_status','remaining_ms','decision_ms','mean_ms','median_ms','p90_ms','std_ms','max_ms','success_rate','http_error_rate','transport_error_rate']+['p_'+s for s in STATES]+['assigned_backoff_ms','assigned_timeout_ms']

def load(path):
    meta=json.loads((path/'metadata.json').read_text()); frames=[]
    for s,r,seed,_ in meta['plan']:
        run=f'{s}_{r:02d}'; d=pd.read_csv(path/(run+'.csv'))
        if len(d)!=meta['parameters']['operations'] or set(d.run_id)!={run} or set(d.server_seed)!={seed} or d.operation.duplicated().any(): raise ValueError('Invalid run '+run)
        frames.append(d)
    all_rows=pd.concat(frames,ignore_index=True)
    if all_rows.duplicated(['run_id','operation']).any(): raise ValueError('Duplicate operation')
    d=all_rows[all_rows.eligible==1].copy().reset_index(drop=True)
    for i,row in d.iterrows():
        hist=json.loads(row.history_json); x=np.array([v[0] for v in hist],float); y=np.array([v[1] for v in hist]); arms=json.loads(row.feasible_json)
        if [row.assigned_backoff_ms,row.assigned_timeout_ms] not in arms or not np.isclose(row.propensity,1/len(arms)): raise ValueError('Invalid randomization')
        if row.assigned_timeout_ms==0 and (row.deadline_success or row.dispatched): raise ValueError('Invalid stop outcome')
        vals=[x.mean(),np.median(x),np.percentile(x,90),x.std(),x.max(),(y=='success').mean(),(y=='http_error').mean(),(y=='transport_error').mean()]
        for k,v in zip(FEATURES[8:16],vals): d.loc[i,k]=v
    if not np.isfinite(d[FEATURES].to_numpy(float)).all(): raise ValueError('Missing/nonfinite features; inspect history and probabilities')
    return d,all_rows,meta

def metrics(y,p):
    return dict(n=len(y),positive=int(sum(y)),brier=float(brier_score_loss(y,p)),log_loss=float(log_loss(y,p,labels=[0,1])),auc=float(roc_auc_score(y,p)) if len(set(y))==2 else None)

def candidates(row):
    return json.loads(row["feasible_json"])

def select(model,row,cost):
    arms=[a for a in candidates(row) if a[1]>0]
    if not arms:return (0,0)
    x=pd.DataFrame([dict(row,assigned_backoff_ms=b,assigned_timeout_ms=t) for b,t in arms])[FEATURES]
    p=model.predict_proba(x)[:,1]; u=p-cost-.05*np.array([(b+t)/max(row['remaining_ms'],1) for b,t in arms]); i=int(np.argmax(u))
    return tuple(arms[i]) if u[i]>0 else (0,0)

def fixed(row):
    arms=[a for a in candidates(row) if a[0]==100 and a[1]<=1000 and a[1]>0]
    return tuple(max(arms,key=lambda a:a[1])) if arms else (0,0)

def ope(d,actions):
    assigned=d[['assigned_backoff_ms','assigned_timeout_ms']].to_numpy(); match=np.all(assigned==np.asarray(actions),axis=1); w=match/d.propensity.to_numpy(); y=d.deadline_success.to_numpy(); disp=d.dispatched.to_numpy()
    # Stop after observed failure has known zero recovery and zero additional attempts.
    retry=np.array([a[1]>0 for a in actions]); w=w*retry
    return dict(n=len(d),recommended_retries=int(retry.sum()),matched_retries=int((match&retry).sum()),effective_sample_size=float(w.sum()**2/(w@w)) if w@w else 0.,ips_recovery=float(np.mean(w*y)),ips_extra_attempts=float(np.mean(w*disp)))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--batch',type=Path,required=True); p.add_argument('--out',type=Path,default=Path('outputs_v8')); p.add_argument('--retry-cost',type=float,default=.15); a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    d,all_rows,meta=load(a.batch); tr=d[d.repeat<=6]; va=d[d.repeat.between(7,8)]; te=d[d.repeat>=9];
    if any(x.empty for x in (tr,va,te)): raise ValueError('Need 10 repetitions per scenario')
    rr=lambda x:x[x.assigned_timeout_ms>0]
    train,val,test=map(rr,(tr,va,te)); y=train.deadline_success.astype(int)
    if len(set(y))<2: raise ValueError('Training needs both outcomes')
    models={'logistic':make_pipeline(StandardScaler(),LogisticRegression(max_iter=2000,random_state=42)), 'rf':RandomForestClassifier(n_estimators=200,min_samples_leaf=8,random_state=42,n_jobs=1)}
    scores=[]
    for name,m in models.items():
        m.fit(train[FEATURES],y); scores.append(dict(model=name,**metrics(val.deadline_success,m.predict_proba(val[FEATURES])[:,1])))
    name=min(scores,key=lambda r:r['brier'])['model']; m=models[name]; prob=m.predict_proba(test[FEATURES])[:,1]
    baseline=np.full(len(test),y.mean()); actions=[select(m,r,a.retry_cost) for r in te.to_dict('records')]; fixed_actions=[fixed(r) for r in te.to_dict('records')]
    report=dict(scope='pilot held-out runs from same collection batch; not prospective deployment validation',model=name,eligible=len(d),assigned_retry=len(rr(d)),assigned_stop=int((d.assigned_timeout_ms==0).sum()),dispatched=int(d.dispatched.sum()),splits={k:dict(runs=int(x.run_id.nunique()),eligible=len(x),assigned_retry=len(rr(x))) for k,x in [('train',tr),('validation',va),('test',te)]},validation=scores,test=metrics(test.deadline_success,prob),constant_baseline=metrics(test.deadline_success,baseline),retry_cost=a.retry_cost,off_policy={'learned':ope(te,actions),'fixed':ope(te,fixed_actions)},warning='IPS estimates may be noisy; per-decision only, not closed-loop policy value; no causal claim about load or backoff beyond tested environment')
    joblib.dump(dict(model=m,features=FEATURES,retry_cost=a.retry_cost,source_model_sha256=meta['model_sha256']),a.out/'outcome_model.joblib')
    d.groupby(['scenario','assigned_backoff_ms','assigned_timeout_ms']).agg(assigned=('eligible','size'),dispatched=('dispatched','sum'),success=('deadline_success','sum')).reset_index().to_csv(a.out/'action_coverage.csv',index=False)
    test[['run_id','operation','scenario','assigned_backoff_ms','assigned_timeout_ms','dispatched','deadline_success']].assign(predicted_probability=prob).to_csv(a.out/'test_predictions.csv',index=False)
    (a.out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))
if __name__=='__main__':main()
