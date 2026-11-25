"""Aegis V3 pilot: run-disjoint communication scenario estimation."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, classification_report

LABELS = ['normal', 'slow', 'flaky', 'overload', 'timeout']
FEATURES = ['mean_ms','median_ms','p90_ms','std_ms','max_ms','success_rate','http_error_rate','transport_error_rate']

def extract(batch, size):
    rows = []
    for scenario in LABELS:
        for rep in range(1, 6):
            path = batch / f'{scenario}_{rep:02d}.csv'
            d = pd.read_csv(path).sort_values('request_id')
            if len(d) != 60 or list(d.request_id) != list(range(1,61)) or set(d.scenario) != {scenario} or set(d['repeat']) != {rep} or list(d.warmup) != [1]*15+[0]*45:
                raise ValueError(f'Unexpected run structure: {path}')
            d = d[d.warmup == 0]
            if not np.isfinite(d.latency_ms).all() or (d.latency_ms < 0).any() or not set(d.outcome).issubset({'success','http_error','transport_error'}):
                raise ValueError(f'Invalid measurements: {path}')
            for start in range(0, len(d)-size+1, size):
                w = d.iloc[start:start+size]
                x = w.latency_ms.to_numpy()
                rows.append(dict(run_id=f'{scenario}_{rep:02d}', scenario=scenario, repeat=rep, first_request=int(w.request_id.iloc[0]), last_request=int(w.request_id.iloc[-1]), mean_ms=x.mean(), median_ms=np.median(x), p90_ms=np.percentile(x,90), std_ms=x.std(), max_ms=x.max(), success_rate=(w.outcome=='success').mean(), http_error_rate=(w.outcome=='http_error').mean(), transport_error_rate=(w.outcome=='transport_error').mean()))
    return pd.DataFrame(rows)

def rules(x):
    # Fixed pilot heuristic; thresholds informed by already observed aggregate results.
    return np.select([(x.transport_error_rate>=.5)&(x.median_ms>=1500), (x.median_ms>=250)&(x.http_error_rate>=.2), x.median_ms>=250, x.http_error_rate>0], ['timeout','overload','slow','flaky'], default='normal')

def scores(y,p):
    return {'accuracy':float(accuracy_score(y,p)), 'macro_f1':float(f1_score(y,p,labels=LABELS,average='macro',zero_division=0))}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--batch',required=True,type=Path); p.add_argument('--out',type=Path,default=Path('outputs_v3')); a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    comparison=[]; candidates=[]
    for size in [5,10,15]:
        d=extract(a.batch,size); d.to_csv(a.out/f'windows_{size}.csv',index=False)
        tr=d[d.repeat<=3]; va=d[d.repeat==4]
        models={'logistic':make_pipeline(StandardScaler(),LogisticRegression(max_iter=2000,random_state=42)), 'random_forest':RandomForestClassifier(n_estimators=200,random_state=42,n_jobs=-1), 'extra_trees':ExtraTreesClassifier(n_estimators=200,random_state=42,n_jobs=-1)}
        for name, model in models.items():
            model.fit(tr[FEATURES],tr.scenario); pred=model.predict(va[FEATURES]); s=scores(va.scenario,pred)
            comparison.append(dict(window=size,model=name,**s)); candidates.append((s['macro_f1'], -size,name,model,d))
        for name,pred in [('rules',rules(va)),('majority',np.repeat(tr.scenario.mode().iloc[0],len(va)))]:
            comparison.append(dict(window=size,model=name,**scores(va.scenario,pred)))
    pd.DataFrame(comparison).to_csv(a.out/'validation_comparison.csv',index=False)
    _,negsize,name,model,d=max(candidates,key=lambda x:(x[0],x[1],x[2])); size=-negsize
    va=d[d.repeat==4]; te=d[d.repeat==5].copy(); vp=model.predict_proba(va[FEATURES]); vc=vp.max(axis=1); vy=model.classes_[vp.argmax(axis=1)]
    valid=[]
    for threshold in np.arange(.5,1.001,.05):
        mask=vc>=threshold
        if mask.sum()>=max(5,len(va)*.5) and (vy[mask]==va.scenario.to_numpy()[mask]).mean()>=.95: valid.append((float(mask.mean()),float(threshold)))
    threshold=max(valid,key=lambda x:(x[0],-x[1]))[1] if valid else 1.01
    prob=model.predict_proba(te[FEATURES]); pred=model.classes_[prob.argmax(axis=1)]; conf=prob.max(axis=1); accept=conf>=threshold
    te['predicted']=pred; te['confidence']=conf; te['accepted']=accept; te['rule_prediction']=rules(te)
    for i,label in enumerate(model.classes_): te['prob_'+label]=prob[:,i]
    te.to_csv(a.out/'test_predictions.csv',index=False)
    pd.DataFrame(confusion_matrix(te.scenario,pred,labels=LABELS),index=LABELS,columns=LABELS).to_csv(a.out/'confusion.csv')
    report={'scope':'exploratory controlled application-level scenarios; aggregate results previously inspected','model':name,'window_requests':size,'runs':{'train':15,'validation':5,'test':5},'windows':{'train':int((d.repeat<=3).sum()),'validation':len(va),'test':len(te)},'test':scores(te.scenario,pred),'rules_test':scores(te.scenario,rules(te)),'majority_test':scores(te.scenario,np.repeat(d[d.repeat<=3].scenario.mode().iloc[0],len(te))),'threshold':threshold if valid else None,'coverage':float(accept.mean()),'accepted_accuracy':float((pred[accept]==te.scenario.to_numpy()[accept]).mean()) if accept.any() else None,'classification':classification_report(te.scenario,pred,labels=LABELS,output_dict=True,zero_division=0)}
    (a.out/'report.json').write_text(json.dumps(report,indent=2)); joblib.dump({'model':model,'features':FEATURES,'window_requests':size,'threshold':threshold},a.out/'estimator.joblib'); print(json.dumps(report,indent=2))

if __name__=='__main__': main()
