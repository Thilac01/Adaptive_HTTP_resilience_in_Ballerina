import argparse
import csv
import datetime
import io
import json
import math
import random
import socket
import statistics
import subprocess
import time
from pathlib import Path

SCENARIOS = ['normal', 'slow', 'flaky', 'overload', 'timeout']
FIELDS = ['request_id', 'latency_ms', 'status_code', 'outcome']


def port_open():
    try:
        with socket.create_connection(('127.0.0.1', 8080), timeout=0.3):
            return True
    except OSError:
        return False


def parse_log(text, expected):
    lines = [line[len('AEGIS_CSV,'):] for line in text.splitlines()
             if line.startswith('AEGIS_CSV,')]
    reader = csv.DictReader(io.StringIO('\n'.join(lines)))
    if reader.fieldnames != FIELDS:
        raise ValueError('Missing or incorrect CSV header')
    rows = list(reader)
    if len(rows) != expected:
        raise ValueError(f'Expected {expected} requests, got {len(rows)}')
    for i, row in enumerate(rows, 1):
        if int(row['request_id']) != i:
            raise ValueError('Unexpected request ID')
        latency = float(row['latency_ms'])
        if not math.isfinite(latency) or latency < 0:
            raise ValueError('Invalid latency')
        status = int(row['status_code'])
        outcome = row['outcome']
        if not ((outcome == 'success' and 200 <= status < 300)
                or (outcome == 'http_error' and 100 <= status <= 599 and not 200 <= status < 300)
                or (outcome == 'transport_error' and status == 0)):
            raise ValueError('Inconsistent response classification')
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repeats', type=int, default=5)
    p.add_argument('--requests', type=int, default=60)
    p.add_argument('--warmup', type=int, default=15)
    p.add_argument('--seed', type=int, default=42, help='Randomizes scenario order, not server RNG')
    p.add_argument('--scenarios', nargs='+', choices=SCENARIOS, default=SCENARIOS)
    p.add_argument('--out', type=Path, default=Path('data_v3'))
    a = p.parse_args()
    if a.repeats < 1 or not 0 <= a.warmup < a.requests:
        p.error('Require repeats >= 1 and 0 <= warmup < requests')
    if port_open():
        p.error('Port 8080 is already occupied. Stop the manually running server first.')
    for name in ('collector.bal', 'fault_server.py'):
        if not Path(name).is_file():
            p.error(f'Missing {name}; run from project directory')
    root = a.out / datetime.datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f')
    root.mkdir(parents=True)
    plan = [(rep, scenario) for rep in range(1, a.repeats + 1) for scenario in a.scenarios]
    random.Random(a.seed).shuffle(plan)
    (root / 'metadata.json').write_text(json.dumps({'requests': a.requests, 'warmup_requests': a.warmup, 'order_seed': a.seed, 'plan': plan, 'fault_type': 'application-level emulation'}, indent=2))
    summary = []
    for index, (rep, scenario) in enumerate(plan, 1):
        run_id = f'{scenario}_{rep:02d}'
        print(f'[{index}/{len(plan)}] {run_id}', flush=True)
        if port_open():
            raise RuntimeError('Port 8080 unexpectedly occupied')
        with (root / f'{run_id}_server.log').open('w') as server_log:
            server = subprocess.Popen(['python3', '-u', 'fault_server.py', '--scenario', scenario], stdout=server_log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Server exited; inspect server log')
                    if port_open():
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError('Server did not become ready')
                with (root / f'{run_id}_client.log').open('w') as client_log:
                    subprocess.run(['bal', 'run', 'collector.bal', '--', f'-Crequests={a.requests}'], stdout=client_log, stderr=subprocess.STDOUT, check=True, timeout=max(600, a.requests * 10 + 120))
                rows = parse_log((root / f'{run_id}_client.log').read_text(errors='replace'), a.requests)
                with (root / f'{run_id}.csv').open('w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['run_id', 'scenario', 'repeat', 'warmup'] + FIELDS)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row, run_id=run_id, scenario=scenario, repeat=rep, warmup=int(int(row['request_id']) <= a.warmup)))
                stable = rows[a.warmup:]
                lat = [float(r['latency_ms']) for r in stable]
                result = {'run_id': run_id, 'scenario': scenario, 'repeat': rep, 'requests': len(rows), 'post_warmup_requests': len(stable), 'median_ms': statistics.median(lat), 'mean_ms': statistics.mean(lat), 'success_rate': sum(r['outcome'] == 'success' for r in stable)/len(stable)}
                summary.append(result)
                with (root / 'summary.csv').open('w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=list(result))
                    writer.writeheader()
                    writer.writerows(summary)
                print(json.dumps(result), flush=True)
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
    print('Collection complete:', root.resolve(), flush=True)


if __name__ == '__main__':
    main()
