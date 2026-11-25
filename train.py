import argparse, json, re
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.metrics import classification_report, confusion_matrix, f1_score, accuracy_score

# Exploratory full-telemetry classifier, not a client-only model.
def features(frame):
    out = {}
    for col in frame.columns:
        if col == 'time': continue
        a = pd.to_numeric(frame[col], errors='coerce').replace([np.inf,-np.inf],np.nan).dropna()
        if len(a):
            out[col+'__mean'] = a.mean()
            out[col+'__std'] = a.std(ddof=0)
            out[col+'__delta'] = a.iloc[-1]-a.iloc[0]
    return out

def collect(root, seconds):
    rows=[]; labels=[]; groups=[]; repeats=[]
    for case in sorted(root.glob('re1ob_*')):
        m=re.fullmatch(r're1ob_(.+)_(cpu|mem|disk|delay|loss)_(\d+)',case.name)
        if not m: continue
        p=case/'metrics.parquet'; t=case/'inject_time.txt'
        if not p.exists() or not t.exists(): continue
        df=pd.read_parquet(p).sort_values('time')
        inject=int(t.read_text().strip())
        for start,end,label in [(inject-seconds,inject,'normal'),(inject,inject+seconds,m[2])]:
            window=df.loc[(df.time>=start)&(df.time<end)]
            if len(window)<3: continue
            rows.append(features(window)); labels.append(label); groups.append(case.name); repeats.append(int(m[3]))
    return pd.DataFrame(rows), np.array(labels), np.array(groups), np.array(repeats)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--data',type=Path,default=Path('data')); p.add_argument('--out',type=Path,default=Path('outputs')); p.add_argument('--seconds',type=int,default=120); p.add_argument('--fixture',action='store_true'); a=p.parse_args()
    if a.fixture:
        rng=np.random.default_rng(42); labels=['normal','cpu','mem','disk','delay','loss']; rows=[]; y=[]; g=[]; r=[]
        for rep in range(1,6):
            for k,label in enumerate(labels[1:],1):
                for i in range(5):
                    for c in [0,k]:
                        rows.append(rng.normal(size=6)+np.eye(6)[c]*3); y.append(labels[c]); g.append(f'{rep}-{k}-{i}'); r.append(rep)
        X=pd.DataFrame(rows,columns=[f'fixture_{i}' for i in range(6)]); y=np.array(y); g=np.array(g); r=np.array(r)
    else: X,y,g,r=collect(a.data,a.seconds)
    train=r<=3; val=r==4; test=r==5
    expected={'normal','cpu','mem','disk','delay','loss'}
    for mask in [train,val,test]:
        if set(y[mask])!=expected: raise SystemExit('Missing classes or data in split; audit download and window size.')
    assert not (set(g[train])&set(g[test]) or set(g[val])&set(g[test]) or set(g[train])&set(g[val]))
    cols=X.loc[train].dropna(axis=1,how='all').columns.tolist(); X=X[cols]
    a.out.mkdir(parents=True,exist_ok=True)
    report={'fixture_only':a.fixture,'window_seconds':a.seconds,'counts':{k:int(m.sum()) for k,m in [('train',train),('validation',val),('test',test)]},'models':{}}
    for name,est in [('majority',DummyClassifier(strategy='most_frequent')),('random_forest',RandomForestClassifier(n_estimators=200,class_weight='balanced',random_state=42,n_jobs=-1))]:
        model=make_pipeline(SimpleImputer(strategy='median'),est); model.fit(X.loc[train],y[train]); pred=model.predict(X.loc[test]); classes=model.classes_.tolist()
        report['models'][name]={'accuracy':accuracy_score(y[test],pred),'macro_f1':f1_score(y[test],pred,average='macro'),'classification':classification_report(y[test],pred,output_dict=True,zero_division=0)}
        pd.DataFrame(confusion_matrix(y[test],pred,labels=classes),index=classes,columns=classes).to_csv(a.out/f'{name}_confusion.csv')
        if name=='random_forest':
            prob=model.predict_proba(X.loc[val]); correct=model.predict(X.loc[val])==y[val]; threshold=.7 # illustrative; not calibrated
            keep=prob.max(axis=1)>=threshold
            report['validation_abstention']={'threshold':threshold,'coverage':float(keep.mean()),'accepted_accuracy':float(correct[keep].mean()) if keep.any() else None}
            joblib.dump({'model':model,'features':cols,'fixture_only':a.fixture},a.out/'classifier.joblib')
            pd.DataFrame({'case':g[test],'true':y[test],'predicted':pred,'confidence':model.predict_proba(X.loc[test]).max(axis=1)}).to_csv(a.out/'test_predictions.csv',index=False)
    (a.out/'report.json').write_text(json.dumps(report,indent=2)); print(json.dumps({'fixture_only':a.fixture,'counts':report['counts'],'macro_f1':{k:v['macro_f1'] for k,v in report['models'].items()}},indent=2))
if __name__=='__main__': main()
