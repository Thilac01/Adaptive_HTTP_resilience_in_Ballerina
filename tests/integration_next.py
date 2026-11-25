"""Live fixed-mode method, payload, header and retry checks; Python 3.8+."""
import argparse
import collections
import json
import pathlib
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
records = []
lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def handle_request(self):
        length = int(self.headers.get('Content-Length', '0'))
        data = self.rfile.read(length) if length else b''
        with lock:
            count = sum(r['path'] == self.path for r in records) + 1
            records.append(dict(method=self.command, path=self.path, header=self.headers.get('X-Aegis-Test'), body=json.loads(data) if data else None, peer_port=self.client_address[1]))
        status = 200 if self.path == "/warmup" else (503 if count == 1 else 200)
        body = json.dumps({'method': self.command, 'status': status}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cold', action='store_true', help='Run without warm-up; original deadline assertions remain active')
    args = parser.parse_args()
    records.clear()
    with ThreadingHTTPServer(('127.0.0.1', 8080), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            command = ['bal', 'run', '--offline']
            if args.cold:
                command += ['--', '-Cwarmup=false']
            run = subprocess.run(command, cwd=str(ROOT / 'next_demo'), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
            print(run.stdout)
            if run.returncode:
                raise RuntimeError('Ballerina demo failed')
        finally:
            server.shutdown()
            thread.join()
    rows = [json.loads(line.split(',', 1)[1]) for line in run.stdout.splitlines() if line.startswith('NEXT_RESULT,')]
    print(json.dumps(records, indent=2))
    warm = [json.loads(line.split(',', 1)[1]) for line in run.stdout.splitlines() if line.startswith('PREPARE_RESULT,')]
    warm_requests = [r for r in records if r['path'] == '/warmup']
    if not args.cold:
        assert len(warm) == 1 and warm[0]['status'] == 200 and warm[0]['ready'], warm
        assert len(warm_requests) == 1, (warm, warm_requests)
        assert warm_requests[0]['header'] == 'yes', warm_requests
        print('Startup probe (outside operation deadlines):', warm[0])
        if warm[0]['elapsedMs'] > 3000:
            print('WARNING: startup exceeded 3000 ms; no cold-start deadline guarantee')
    else:
        assert not warm and not warm_requests, (warm, warm_requests)
    measured = [r for r in records if r['path'] != '/warmup']
    methods = ['GET','HEAD','POST','PUT','PATCH','DELETE']
    assert [r['method'] for r in rows] == methods, rows
    counts = collections.Counter(r['method'] for r in measured)
    for row in rows:
        method = row['method']
        expected = 2 if method in ('GET','HEAD') else 1
        status = 200 if expected == 2 else 503
        assert counts[method] == expected == row['attempts'], (row, counts)
        assert row['status'] == status and not row['deadlineViolation'], row
        if method != 'HEAD':
            assert row['body'] == {'method':method,'status':status}, row
    for record in measured:
        assert record['header'] == 'yes', record
        assert record['path'] == '/' + record['method'], record
        if record['method'] in ('POST','PUT','PATCH'):
            assert record['body'] == {'name':'robot'}, record
    print('PASS (' + ('cold' if args.cold else 'warm') + '): six methods, payloads, headers, response bodies, retry restrictions and deadlines')
    print('Observed TCP peer ports (diagnostic only):', sorted(set(r['peer_port'] for r in records)))

if __name__ == '__main__':
    main()
