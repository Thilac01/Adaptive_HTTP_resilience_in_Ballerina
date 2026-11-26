# SURE standalone prototype

Run these commands from the repository root in PowerShell with Python 3.10 or newer.

```powershell
py -3 -m unittest discover -s tests -p "test_sure_*.py" -v
py -3 -m sure_research.benchmark --seed 42 --episodes-per-cell 100 --out sure-results/seed-42
py -3 -m sure_research.analyze sure-results/seed-42 --out sure-results/seed-42-analysis
```

Each output directory must be new. The benchmark writes per-case traces, overall
metrics and metrics by condition. The analyzer checks that every case has one
run per policy with matching inputs. It reports the paired confirmation
difference, discordant outcomes, an exact McNemar p-value and mean metric
differences. Differences are lookahead minus comparator. The condition-level
tests are exploratory. Use a fresh seed and design for confirmatory experiments.

The current benchmark measures synthetic initial-timeout cases in virtual time.
It uses stipulated availability, fixed action durations, one recovery call and
an idealized shared deduplication contract.

## Version 1 Ballerina integration contract

`python -m sure_research.planner_api` reads one JSON request on stdin and writes
one JSON reply. The Python function `decide(request)` exposes the same interface
for a later HTTP adapter. The draft contract contains:

```json
{
  "version": 1,
  "request_id": "payment-1",
  "policy": "lookahead",
  "evidence": "ambiguous",
  "remaining_ms": 600,
  "remaining_calls": 1,
  "belief": {"unstarted": 0.25, "processing": 0.5, "completed": 0.25},
  "availability": {"reconcile": 0.9, "retry": 0.9, "failover": 0.9},
  "verified_deduplication_endpoints": []
}
```

The reply contains the protocol version, request ID, recommended action, score,
reserved milliseconds and number of recovery calls to charge. The action is one
of `stop`, `wait`, `reconcile`, `retry` (primary), or `failover` (backup).
Invalid requests receive `stop` and an error, with process exit status 2.

Ballerina will own the request lifecycle and monotonic deadline. The trusted
client must verify execution evidence and the same-request deduplication
contract, supply the current belief and model inputs, and recheck time, call
budget and semantic admission immediately before dispatch. A timeout, pending
response or missing record means `ambiguous`. Only authoritative evidence can
set `confirmed` or `no_prior_attempt_can_commit`. Following an ambiguous
repeat, previous no-commit evidence becomes stale. The client must accept no
action on an invalid or unavailable planner response. No live Ballerina
transport is enabled by this interface.
