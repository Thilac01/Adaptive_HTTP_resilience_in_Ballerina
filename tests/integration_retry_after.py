"""Live Retry-After fixed-mode checks; Python 3.8+. Port 8080 must be free."""
import collections
import json
import pathlib
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
requests = collections.defaultdict(list)
lock = threading.Lock()
HEADERS = {"one": ["1"], "zero": ["0"], "long": ["120"], "bad": ["abc"],
           "date": ["Wed, 25 Nov 2026 00:00:00 GMT"], "duplicate": ["0", "1"],
           "budget": ["3"], "mutation": ["0"]}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def handle_request(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        name = self.path.rsplit("/", 1)[-1]
        with lock:
            requests[name].append(time.monotonic())
            count = len(requests[name])
        status = 200 if name == "warmup" or count > 1 else (429 if name == "one" else 503)
        body = json.dumps({"status": status}).encode()
        self.send_response(status)
        if count == 1:
            for value in HEADERS.get(name, []):
                self.send_header("Retry-After", value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_POST = handle_request
    def log_message(self, *args):
        pass

def main():
    with ThreadingHTTPServer(("127.0.0.1", 8080), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            run = subprocess.run(["bal", "run", "--offline", "--", "-CretryAfterSuite=true"], cwd=str(ROOT / "next_demo"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
            print(run.stdout)
            run.check_returncode()
        finally:
            server.shutdown()
            thread.join()
    rows = [json.loads(line.split(",", 1)[1]) for line in run.stdout.splitlines() if line.startswith("RETRY_RESULT,")]
    assert [r["name"] for r in rows] == list(HEADERS), rows
    assert len(requests["warmup"]) == 1, requests
    for row in rows:
        name = row["name"]
        expected = 2 if name in ("one", "zero") else 1
        assert row["attempts"] == len(requests[name]) == expected, (row, requests)
        assert row["status"] == (200 if expected == 2 else 503), row
        assert not row["deadlineViolation"], row
        if name == "one":
            gap = requests[name][1] - requests[name][0]
            print("Observed server request gap: %.3f s" % gap)
            assert gap >= 1.0, gap
    print("PASS: all eight Retry-After scenarios")

if __name__ == "__main__":
    main()
