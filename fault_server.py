import argparse
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=['normal', 'slow', 'flaky', 'overload', 'timeout'], default='normal')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/work':
                self.send_error(404)
                return
            with lock:
                draw = rng.random()
            delay, status = 0.02, 200
            if args.scenario == 'slow':
                delay = 0.45
            elif args.scenario == 'flaky' and draw < 0.35:
                status = 503
            elif args.scenario == 'overload':
                delay, status = 0.25, 503
            elif args.scenario == 'timeout':
                delay = 1.5
            time.sleep(delay)
            body = b'{"ok":true}' if status == 200 else b'{"ok":false}'
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Scenario={args.scenario}, seed={args.seed}, port={args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
