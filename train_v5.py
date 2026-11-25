"""Aegis V5: variable-severity development and prospective frozen evaluation.
Run in Windows Python. Collect independent batches with challenge_v4.py in WSL.
"""
import argparse, json, hashlib
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, log_loss
from train_v3 import LABELS, FEATURES, rules


def load_batch(path, n):
    meta = json.loads((path/'metadata.json').read_text())
    rows=[]; seen=set()
    for scenario, rep, seed in meta['plan']:
        run=f'{scenario}_{rep:02d}'
        if run in seen: raise ValueError('Duplicate run '+run)
        seen.add(run)
        d=pd.read_csv(path/(run+'.csv')).sort_values('request_id')
        if list(d.request_id)!=list(range(1,61)) or list(d.warmup)!=[1]*15+[0]*45 or set(d.scenario)!={scenario} or set(d['repeat'])!={rep}:
            raise ValueError('Invalid recording '+run)
        if not np.isfinite(d.latency_ms).all() or (d.latency_ms<0).any() or not set(d.outcome)<= {'success','http_error','transport_error'}:
            raise ValueError('Invalid telemetry '+run)
        d=d[d.warmup==0]
        for j in range(0,len(d)-n+1,n):
            w=d.iloc[j:j+n]; x=w.latency_ms.to_numpy()
            rows.append(dict(run_id=run,scenario=scenario,repeat=rep,first_request=int(w.request_id.iloc[0]),mean_ms=x.mean(),median_ms=np.median(x),p90_ms=np.percentile(x,90),std_ms=x.std(),max_ms=x.max(),success_rate=(w.outcome=='success').mean(),http_error_rate=(w.outcome=='http_error').mean(),transport_error_rate=(w.outcome=='transport_error').mean()))
    return pd.DataFrame(rows),meta


def score(d,p):
    return dict(accuracy=float(accuracy_score(d.scenario,p)),macro_f1=float(f1_score(d.scenario,p,labels=LABELS,average='macro',zero_division=0)))


def tuned_rules(d, params):
    latency, http, transport, tail=params
    return np.select([(d.transport_error_rate>=transport)&(d.max_ms>=tail),(d.median_ms>=latency)&(d.http_error_rate>=http),d.median_ms>=latency,d.http_error_rate>0],['timeout','overload','slow','flaky'],default='normal')


def fit(a):
    out=a.out; out.mkdir(parents=True,exist_ok=True)
    results=[]; candidates=[]
    # V4 is already inspected: all of it is development data.
    # Repeat IDs 1..6 fit; 7..10 tune rules; both models select on 7..10.
    # No performance on this split is described as independent testing.
    for n in (5,10,15):
        d,meta=load_batch(a.batch,n); tr=d[d.repeat<=6]; va=d[d.repeat>6]
        if set(tr.scenario)!=set(LABELS) or set(va.scenario)!=set(LABELS): raise ValueError('Need at least seven repetitions of each scenario')
        models={'logistic':make_pipeline(StandardScaler(),LogisticRegression(max_iter=3000,random_state=42)), 'rf':RandomForestClassifier(n_estimators=300,min_samples_leaf=3,random_state=42,n_jobs=-1),'extra_trees':ExtraTreesClassifier(n_estimators=300,min_samples_leaf=3,random_state=42,n_jobs=-1)}
        for name,m in models.items():
            m.fit(tr[FEATURES],tr.scenario); s=score(va,m.predict(va[FEATURES])); results.append(dict(window=n,model=name,**s)); candidates.append((s['macro_f1'],-n,name,m,va))
        results.append(dict(window=n,model='fixed_v3_rules',**score(va,rules(va))))
    _,neg,nm,m,va=max(candidates,key=lambda z:(z[0],z[1],z[2])); n=-neg
    # Same observation window for the final model and rule comparator.
    options=[]
    for latency in (100,150,200,250,350,450):
        for http in (.1,.2,.3,.4):
            for transport in (.2,.4,.6):
                for tail in (1000,1500,1800):
                    p=(latency,http,transport,tail); options.append((score(va,tuned_rules(va,p))['macro_f1'],p))
    _,params=max(options,key=lambda z:z[0])
    prob=m.predict_proba(va[FEATURES]); pred=m.classes_[prob.argmax(1)]; conf=prob.max(1)
    thresholds=[]
    for t in np.arange(.5,1.001,.05):
        mask=conf>=t
        if mask.mean()>=.5 and (pred[mask]==va.scenario.to_numpy()[mask]).mean()>=.90: thresholds.append(float(t))
    threshold=min(thresholds) if thresholds else None
    artifact=dict(model=m,model_name=nm,window_requests=n,features=FEATURES,rule_params=params,threshold=threshold,development_seed=meta['seed'],development_run_seeds=[int(x[2]) for x in meta['plan']],development_batch=str(a.batch.resolve()))
    joblib.dump(artifact,out/'estimator.joblib')
    pd.DataFrame(results).to_csv(out/'validation_comparison.csv',index=False)
    report=dict(scope='V4 development only; selection scores optimistic',model=nm,window=n,validation_model=score(va,pred),validation_tuned_rules=score(va,tuned_rules(va,params)),validation_fixed_rules=score(va,rules(va)),threshold=threshold,rule_params=params)
    (out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))


def evaluate(a):
    art=joblib.load(a.model); d,meta=load_batch(a.batch,art['window_requests'])
    if meta['seed']==art['development_seed'] or set(int(x[2]) for x in meta['plan']) & set(art['development_run_seeds']): raise ValueError('Evaluation must use a new independent seed and recordings')
    out=a.out; out.mkdir(parents=True,exist_ok=True)
    p=art['model'].predict_proba(d[art['features']]); pred=art['model'].classes_[p.argmax(1)]; conf=p.max(1)
    d['ai']=pred; d['fixed_rules']=rules(d); d['tuned_rules']=tuned_rules(d,art['rule_params']); d['confidence']=conf
    t=art['threshold']; mask=conf>=t if t is not None else np.zeros(len(d),dtype=bool); d['accepted']=mask
    for i,label in enumerate(art['model'].classes_): d['prob_'+label]=p[:,i]
    report=dict(scope='frozen model on new variable-severity application-level recordings',runs=int(d.run_id.nunique()),windows=len(d),window=art['window_requests'],threshold=t,coverage=float(mask.mean()),accepted_accuracy=float((pred[mask]==d.scenario.to_numpy()[mask]).mean()) if mask.any() else None,log_loss=float(log_loss(d.scenario,p,labels=art['model'].classes_)),model_sha256=hashlib.sha256(a.model.read_bytes()).hexdigest())
    for col in ('ai','fixed_rules','tuned_rules'):
        report[col]=score(d,d[col]); pd.DataFrame(confusion_matrix(d.scenario,d[col],labels=LABELS),index=LABELS,columns=LABELS).to_csv(out/f'confusion_{col}.csv')
    d.to_csv(out/'predictions.csv',index=False)
    (out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    f=sub.add_parser('fit'); f.add_argument('--batch',type=Path,required=True); f.add_argument('--out',type=Path,default=Path('outputs_v5_dev'))
    e=sub.add_parser('evaluate'); e.add_argument('--batch',type=Path,required=True); e.add_argument('--model',type=Path,default=Path('outputs_v5_dev/estimator.joblib')); e.add_argument('--out',type=Path,default=Path('outputs_v5_test'))
    a=p.parse_args(); (fit if a.cmd=='fit' else evaluate)(a)
if __name__=='__main__': main()
