"""Aegis V2: exploratory RCAEval experiments. Run from aegis_phase1."""
import argparse
import json
import re
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, classification_report,
                             confusion_matrix, log_loss)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

LABELS = ['cpu', 'delay', 'disk', 'loss', 'mem', 'normal']
SEED = 42


def load_cases(root):
    cases = []
    for p in sorted(root.glob('re1ob_*')):
        m = re.fullmatch(r're1ob_(.+)_(cpu|mem|disk|delay|loss)_(\d+)', p.name)
        if not m:
            continue
        df = pd.read_parquet(p / 'metrics.parquet').sort_values('time')
        df = df.apply(pd.to_numeric, errors='coerce').replace([np.inf, -np.inf], np.nan)
        inject = int((p / 'inject_time.txt').read_text().strip())
        cases.append((p.name, m[2], int(m[3]), inject, df))
    if len(cases) != 125:
        raise ValueError(f'Expected 125 cases, found {len(cases)}')
    return cases


def features(window, baseline, mode):
    cols = [c for c in window if c != 'time']
    if mode == 'frontend_proxy':
        cols = [c for c in cols if c in ('frontend_latency', 'frontend_load',
                'frontend-external_load', 'frontend_error', 'frontend-external_error')]
    if not cols:
        raise ValueError('No usable telemetry columns')
    x = window[cols]
    b = baseline[cols]
    mu, sd = b.mean(), b.std(ddof=0)
    # Scale floor avoids enormous ratios for nearly constant baseline metrics.
    scale = np.maximum(sd, np.maximum(mu.abs() * 0.01, 1e-9))
    z = (x - mu) / scale
    out = {}
    for c in cols:
        a = x[c].dropna()
        v = z[c].dropna()
        if len(a) < 3:
            continue
        t = window.loc[a.index, 'time'].to_numpy(dtype=float)
        t -= t[0]
        slope = np.polyfit(t, a.to_numpy(), 1)[0] if np.ptp(t) > 0 else 0.0
        vals = {'mean': a.mean(), 'std': a.std(ddof=0), 'delta': a.iloc[-1]-a.iloc[0],
                'median': a.median(), 'p95': a.quantile(.95), 'max': a.max(),
                'iqr': a.quantile(.75)-a.quantile(.25), 'slope': slope,
                'zmean': v.mean(), 'zstd': v.std(ddof=0), 'zabs95': v.abs().quantile(.95)}
        out.update({f'{c}__{k}': float(val) for k, val in vals.items()})
    # Aggregate across ALL observed services, never use the injected service label.
    for family in ('cpu', 'mem', 'load', 'latency', 'error'):
        names = [c for c in cols if c.endswith('_' + family)]
        if not names:
            continue
        v = z[names]
        for k, s in [('zmean', v.mean()), ('zabs95', v.abs().quantile(.95)),
                     ('zstd', v.std(ddof=0))]:
            out[f'aggregate_{family}_{k}_max'] = float(s.max())
            out[f'aggregate_{family}_{k}_mean'] = float(s.mean())
            out[f'aggregate_{family}_{k}_min'] = float(s.min())
    return out


def collect(cases, seconds, mode):
    rows, labels, groups, reps = [], [], [], []
    for name, fault, rep, inj, df in cases:
        # Separate historical baseline, preceding BOTH classification windows.
        base = df[(df.time >= inj-420) & (df.time < inj-120)]
        for start, end, label in [(inj-seconds, inj, 'normal'), (inj, inj+seconds, fault)]:
            w = df[(df.time >= start) & (df.time < end)]
            if len(base) < 30 or len(w) < max(3, int(seconds*.8)):
                raise ValueError(f'Insufficient samples: {name}, {seconds}s, {label}')
            rows.append(features(w, base, mode)); labels.append(label)
            groups.append(name); reps.append(rep)
    X = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    return X, np.asarray(labels), np.asarray(groups), np.asarray(reps)


def candidates():
    return {
        'majority': DummyClassifier(strategy='most_frequent'),
        'random_forest': RandomForestClassifier(n_estimators=200, class_weight='balanced', random_state=SEED, n_jobs=1),
        'extra_trees': ExtraTreesClassifier(n_estimators=200, class_weight='balanced', random_state=SEED, n_jobs=1),
        'logistic': LogisticRegression(class_weight='balanced', max_iter=3000, random_state=SEED),
        'svm': SVC(C=1, class_weight='balanced', kernel='rbf', random_state=SEED)
    }


def build(name, est, reps):
    pipe = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), est)
    if name == 'majority':
        return pipe
    # Calibration uses only training repetitions; entire runs stay together.
    cv = [(np.flatnonzero(reps != r), np.flatnonzero(reps == r)) for r in (1, 2, 3)]
    return CalibratedClassifierCV(pipe, method='sigmoid', cv=cv, ensemble=True, n_jobs=1)


def evaluate(model, X, y):
    p = model.predict_proba(X)
    pred = model.classes_[p.argmax(axis=1)]
    onehot = (y[:, None] == model.classes_[None, :]).astype(float)
    return {'accuracy': float(accuracy_score(y, pred)),
            'macro_f1': float(f1_score(y, pred, labels=LABELS, average='macro', zero_division=0)),
            'cpu_recall': float((pred[y=='cpu']=='cpu').mean()),
            'log_loss': float(log_loss(y, p, labels=model.classes_)),
            'brier_multiclass': float(np.mean(np.sum((p-onehot)**2, axis=1)))}, pred, p


def selective(y, pred, p, threshold):
    keep = p.max(axis=1) >= threshold
    return {'threshold': float(threshold), 'coverage': float(keep.mean()),
            'accepted_count': int(keep.sum()),
            'accepted_accuracy': float((pred[keep]==y[keep]).mean()) if keep.any() else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--out', type=Path, default=Path('outputs_v2'))
    parser.add_argument('--windows', type=int, nargs='+', default=[10,30,60,120])
    parser.add_argument('--modes', nargs='+', choices=['full','frontend_proxy'], default=['full','frontend_proxy'])
    args = parser.parse_args()
    if any(w<3 or w>120 for w in args.windows):
        raise ValueError('Window must be 3–120 seconds')
    args.out.mkdir(parents=True, exist_ok=True)
    cases = load_cases(args.data)
    records, winners = [], {}
    for mode in args.modes:
        best = None
        for seconds in args.windows:
            X,y,g,r = collect(cases, seconds, mode)
            tr, va, te = r<=3, r==4, r==5
            for mask in (tr,va,te):
                if set(y[mask]) != set(LABELS):
                    raise ValueError('Missing classes')
            assert not (set(g[tr]) & set(g[va]) or set(g[tr]) & set(g[te]) or set(g[va]) & set(g[te]))
            cols = X.loc[tr].dropna(axis=1, how='all').columns.tolist()
            X = X[cols]
            for name, est in candidates().items():
                model = build(name, est, r[tr])
                model.fit(X.loc[tr], y[tr])
                metrics, pred, p = evaluate(model, X.loc[va], y[va])
                record = {'mode':mode, 'window_seconds':seconds, 'model':name, **metrics}
                records.append(record)
                print(record, flush=True)
                # Model/window selection uses validation only, with CPU recall as tie-breaker.
                score = (metrics['macro_f1'], metrics['cpu_recall'], -seconds)
                if name != 'majority' and (best is None or score > best[0]):
                    best = (score, name, seconds, model, X, y, g, va, te, cols, pred, p)
        _, name, seconds, model, X, y, g, va, te, cols, vp, vprob = best
        # Select maximum coverage satisfying empirical validation accuracy target.
        options = [selective(y[va], vp, vprob, t) for t in np.linspace(0,1,201)]
        feasible = [o for o in options if o['accepted_count'] >= 10 and o['accepted_accuracy'] >= .95]
        chosen = max(feasible, key=lambda o:o['coverage']) if feasible else None
        threshold = chosen['threshold'] if chosen else 1.01  # abstain all when target unmet
        metrics, pred, p = evaluate(model, X.loc[te], y[te])
        sample = X.loc[te].iloc[[0]]
        model.predict_proba(sample)
        durations = []
        for _ in range(30):
            start = time.perf_counter(); model.predict_proba(sample)
            durations.append((time.perf_counter()-start)*1000)
        result = {'model':name, 'window_seconds':seconds, 'features':len(cols),
                  'validation_threshold_result':chosen, 'exploratory_test':metrics,
                  'test_selective':selective(y[te],pred,p,threshold),
                  'inference_ms_median':float(np.median(durations)),
                  'inference_ms_p95':float(np.percentile(durations,95)),
                  'classification':classification_report(y[te],pred,labels=LABELS,output_dict=True,zero_division=0)}
        winners[mode] = result
        folder = args.out / mode; folder.mkdir(exist_ok=True)
        joblib.dump({'model':model,'features':cols,'mode':mode,'window_seconds':seconds,'threshold':threshold},folder/'classifier.joblib')
        pd.DataFrame(confusion_matrix(y[te],pred,labels=LABELS),index=LABELS,columns=LABELS).to_csv(folder/'confusion.csv')
        df = pd.DataFrame({'case':g[te],'true':y[te],'predicted':pred,'confidence':p.max(axis=1),'accepted':p.max(axis=1)>=threshold})
        for i,c in enumerate(model.classes_): df['prob_'+c] = p[:,i]
        df.to_csv(folder/'test_predictions.csv',index=False)
        pd.DataFrame(options).to_csv(folder/'validation_thresholds.csv',index=False)
    pd.DataFrame(records).to_csv(args.out/'validation_comparison.csv',index=False)
    report = {'dataset':'RCAEval RE1-OB','test_status':'exploratory: previously inspected in V1','counts':{'train':150,'validation':50,'test':50},'winners':winners}
    (args.out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
