"""Black-box native GET recovery tests with deterministic stub services.
Run from any directory; requires installed local Aegis package and built demo.
No model binaries needed. Ports 8080 and 8091 must be free.
"""
import json
import subprocess
import threading
import time
import unittest
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def services(status, decision):
    requests = Counter()
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, code, body):
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
        def do_GET(self):
            requests[self.path] += 1
            code = status if self.path == '/op/11' and requests[self.path] == 1 else 200
            self.reply(code, b'{}')
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if decision == 'slow':
                time.sleep(0.8)
            code, body = {
                'valid': (200, b'{"backoffMs":0,"timeoutMs":700}'),
                'invalid': (200, b'{"backoffMs":-1,"timeoutMs":700}'),
                'stop': (200, b'{"backoffMs":0,"timeoutMs":0}'),
                'error': (503, b'{}'),
                'malformed': (200, b'not-json'),
                'slow': (200, b'{"backoffMs":0,"timeoutMs":700}'),
            }[decision]
            self.reply(code, body)
    servers = []
    try:
        for port in ([8080] if decision == 'offline' else [8080, 8091]):
            server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        yield requests, calls
    finally:
        for server in servers:
            server.shutdown(); server.server_close()

class Integration(unittest.TestCase):
    def case(self, mode, decision, expected_attempts, status=503, expected_calls=0):
        with services(status, decision) as (requests, calls):
            p = subprocess.run(['bal', 'run', '--offline', '--', '-Cmode='+mode,
                                '-Cwarmup=10', '-Coperations=1'], cwd=ROOT/'native_demo',
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stdout)
            rows = [json.loads(line.split(',',1)[1]) for line in p.stdout.splitlines()
                    if line.startswith('AEGIS_RESULT,')]
            self.assertEqual(len(rows), 1, p.stdout)
            row = rows[0]
            diagnostics = json.dumps({'mode': mode, 'decision': decision,
                'server_requests': dict(requests),
                'missing_warmup_paths': ['/op/'+str(i) for i in range(1, 11)
                                         if requests['/op/'+str(i)] == 0],
                'inference_calls': len(calls), 'result': row}, indent=2)
            print('\n'+diagnostics, flush=True)
            self.assertEqual(row['attempts'], expected_attempts, p.stdout)
            self.assertEqual(requests['/op/11'], expected_attempts)
            self.assertEqual(sum(requests.values()), 10+expected_attempts, diagnostics)
            for i in range(1, 11):
                self.assertEqual(requests['/op/'+str(i)], 1, diagnostics)
            self.assertEqual(len(calls), expected_calls)
            self.assertEqual(row['status'], 200 if expected_attempts == 2 else status)
            self.assertEqual(row['deadlineSuccess'], expected_attempts == 2)
            self.assertFalse(row['deadlineViolation'])
            if calls: self.assertEqual(len(calls[0]['history']), 10)
    def test_fixed(self): self.case('fixed','valid',2)
    def test_none(self): self.case('none','valid',1)
    def test_learned_valid(self): self.case('learned','valid',2,expected_calls=1)
    def test_learned_stop(self): self.case('learned','stop',1,expected_calls=1)
    def test_invalid_action(self): self.case('learned','invalid',1,expected_calls=1)
    def test_sidecar_error(self): self.case('learned','error',1,expected_calls=1)
    def test_malformed_response(self): self.case('learned','malformed',1,expected_calls=1)
    def test_sidecar_offline(self): self.case('learned','offline',1)
    def test_sidecar_timeout(self): self.case('learned','slow',1,expected_calls=1)
    def test_nonretryable(self): self.case('learned','valid',1,status=404)

if __name__ == '__main__': unittest.main(verbosity=2)
