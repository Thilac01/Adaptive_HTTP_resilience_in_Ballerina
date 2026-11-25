"""Bounded-capacity HTTP service with independent background HTTP traffic."""
import argparse, json, random, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import subprocess


def serve(seed, scenario):
    # Fixed profiles; background traffic shares the same service slots as foreground.
    profiles={'flaky':(0.12, 5, .20), 'overload':(.35, 10, .03), 'timeout':(.65, 7, .03)}
    service,rate,error=profiles[scenario]
    slots=threading.BoundedSemaphore(3); admission=threading.BoundedSemaphore(8)
    lock=threading.Lock(); rng=random.Random(seed); stop=threading.Event()
    def emit(**kw):
        with lock: print(json.dumps(kw),flush=True)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            bg=self.path=='/background'; start=time.monotonic(); queued=0.; status=503; admitted=admission.acquire(False)
            if admitted:
                try:
                    with slots:
                        queued=time.monotonic()-start
                        with lock: delay=rng.uniform(.8,1.2)*service; failed=rng.random()<error
                        time.sleep(delay); status=503 if failed else 200
                finally: admission.release()
            disconnected=False
            try:
                self.send_response(status); self.send_header('Content-Length','2'); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{}')
            except (BrokenPipeError,ConnectionResetError): disconnected=True
            emit(kind='request',background=bg,admitted=admitted,status=status,queue_ms=queued*1000,service_total_ms=(time.monotonic()-start)*1000,disconnected=disconnected)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',8080),Handler)
    # Fixed offered arrival rate, bounded local generator concurrency; log dropped arrivals.
    outstanding=threading.BoundedSemaphore(32)
    def background():
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/background',timeout=8) as r: r.read()
        except (OSError,urllib.error.URLError): pass
        finally: outstanding.release()
    def arrivals():
        with ThreadPoolExecutor(max_workers=32) as pool:
            due=time.monotonic()
            while not stop.is_set():
                if stop.wait(max(0,due-time.monotonic())): break
                if outstanding.acquire(False): pool.submit(background)
                else: emit(kind='generator_drop')
                due+=1/rate
    emit(kind='configuration',scenario=scenario,seed=seed,slots=3,admission_capacity=8,background_rps=rate,service_s=service)
    threading.Thread(target=arrivals,daemon=True).start()
    try: server.serve_forever()
    finally: stop.set(); server.server_close()

@contextmanager
def server_for(scenario,seed,root,run):
    from benchmark_v7_2 import port_open
    if port_open(): raise RuntimeError('Stop existing server on port 8080')
    wsl=['wsl','-d','Ubuntu-20.04']; pidfile=f'.aegis_load_{root.name}_{run}.pid'
    with (root/f'{run}_server.log').open('w') as log:
        cmd=f'echo $$ > {pidfile}; exec python3 -u load_server.py --scenario {scenario} --seed {seed}'
        p=subprocess.Popen(wsl+['bash','-c',cmd],stdout=log,stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                if p.poll() is not None: raise RuntimeError('Server exited; inspect server log')
                if port_open(): break
                time.sleep(.2)
            else: raise RuntimeError('Server not ready')
            yield
        finally:
            subprocess.run(wsl+['bash','-c',f'if test -f {pidfile}; then kill "$(cat {pidfile})" 2>/dev/null || true; rm -f {pidfile}; fi'],check=False)
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.terminate(); p.wait(timeout=10)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--scenario',choices=['flaky','overload','timeout'],required=True); p.add_argument('--seed',type=int,required=True); a=p.parse_args(); serve(a.seed,a.scenario)
