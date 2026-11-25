import argparse,csv,json,statistics
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--batch',type=Path,required=True); a=p.parse_args(); rows=[]
for f in sorted(a.batch.glob('*_server.log')):
    events=[]
    for line in f.read_text(errors='replace').splitlines():
        try: events.append(json.loads(line))
        except ValueError: continue
    bg=[e for e in events if e.get('kind')=='request' and e['background']]; fg=[e for e in events if e.get('kind')=='request' and not e['background']]
    def avg(es): return statistics.mean(e['queue_ms'] for e in es if e['admitted']) if any(e['admitted'] for e in es) else None
    rows.append(dict(run=f.name.replace('_server.log',''),background_completed=len(bg),background_rejected=sum(not e['admitted'] for e in bg),background_503=sum(e['status']==503 for e in bg),background_mean_queue_ms=avg(bg),foreground_completed=len(fg),foreground_rejected=sum(not e['admitted'] for e in fg),foreground_mean_queue_ms=avg(fg),generator_drops=sum(e.get('kind')=='generator_drop' for e in events)))
if not rows: raise SystemExit('No server logs found')
with (a.batch/'server_metrics.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(a.batch/'server_metrics.csv')
