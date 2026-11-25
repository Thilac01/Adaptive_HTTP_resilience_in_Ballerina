# Aegis | Adaptive HTTP resilience in Ballerina

Research prototype combining client-side communication telemetry, a frozen state estimator, action-conditioned recovery prediction and deadline-aware utility selection. Ballerina owns the sequential GET request lifecycle; Python serves the learned decisions locally.

## Final evaluation

48 runs, 1,440 measured operations, 12 scenario–seed blocks. All configurations had zero observed deadline violations.

| Controller | Deadline success | Server requests | P95 (ms) |
|---|---:|---:|---:|
| Native HTTP | 63.61% | 359 | 1359.94 |
| Native retry | 83.61% | 490 | 2714.47 |
| Aegis fixed | 78.61% | 490 | 2528.99 |
| Aegis learned | 76.11% | 441 | 2164.25 |

Aegis learned trades reliability for lower request volume and lower overall tail latency; matched successful operations have higher P95 than native retry. No statistical superiority or equivalence is established.
<img width="2400" height="1350" alt="scenario_success" src="https://github.com/user-attachments/assets/a93574b0-8576-421d-9338-51a6461aaa0b" />


## Reproduce archived results without models

From this repository root, run `python reproduce_results.py`. This verifies the archived aggregate metrics using only the Python standard library. Raw operation records, server logs and metadata are in `benchmarks_native/`. Previously generated plots and matched analytical tables are in `analysis/`. The IEEE manuscript and LaTeX source are in `paper/`.

## Run live experiments

Original environment: Windows Python 3.14, Ubuntu-20.04 WSL Python 3.8, Ballerina 2201.10.2. Exact Python dependency versions from the original environment have not been supplied. Restore trusted original model files in `frozen_v5/` and `frozen_v8/`; their required SHA256 values are documented there. Do not load untrusted joblib files. The inference server checks these hashes. Install dependencies in the Windows Python environment using `python -m pip install -r requirements.txt` (prefer the original environment if available).

In Ubuntu WSL, from the repository root:

```bash
cd aegis_native
bal build --offline
bal test --offline
bal pack
bal push --repository local
cd ..
```

In Windows PowerShell, from the same repository root:

```powershell
python inference_server.py
```

Leave inference running. In WSL, verify `curl --max-time 5 http://127.0.0.1:8091/health`. Ensure port 8080 is free, then run:

```bash
python3 benchmark_native.py --scenarios flaky --repeats 1 --operations 10
python3 benchmark_native.py --repeats 3 --operations 30 --seed 98173
```

The runner manages its own HTTP server. It matches application-fault draws by seed, logical operation and received attempt. Live timings and transport failures may vary. Archived results must not be overwritten or confused with new runs.

## Scope and limitations

Sequential GET only; at most one retry in the native library; best-effort deadline budgeting, not hard cancellation. The inference service is local and experimental, without production authentication. No verified packet-level impairment experiment, comprehensive non-idempotent safety validation, or production deployment claim. Native retry and Aegis have different initial timeout settings. Server request counts are received HTTP requests, not all transport attempts. Earlier development scripts are included for provenance, but their training datasets, exact environment lock and frozen model binaries are not bundled. Earlier results cannot be independently retrained from this archive alone.

## Repository and publication

This is a research artifact draft, not an official Ballerina distribution. Confirm author information, permissions, third-party data rights and license before public release. No license has been assumed. See `paper/README.md` for manuscript build instructions.
