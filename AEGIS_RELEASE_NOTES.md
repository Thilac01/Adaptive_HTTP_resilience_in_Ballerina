# Aegis v0.2.0 experimental release candidate

## Scope
The frozen `thilac/aegis:0.1.0` package is the implementation associated with the 1,440-operation research benchmark. The separate `thilac/aegis_next:0.2.0` package extends the sequential HTTP API; it has not been evaluated in that benchmark.

## Changes
- Six HTTP methods, JSON payloads, custom headers and response access.
- Conservative method-aware retry rules: GET fixed/learned, HEAD fixed only; no explicit mutation retries.
- Explicit startup preparation and integer Retry-After handling.
- Native unit and mock integration verification, plus CI configuration.

## Local verification (reported 2026-11-25)
- v0.1.0: 5 unit tests and 10 mock integration tests passed in the latest complete run; an earlier intermittent warm-up failure remains unexplained.
- v0.2.0: 2 unit tests, 6 prepared fixed-mode method scenarios and 8 Retry-After scenarios passed; 1.053 s observed request gap for a one-second server delay.
- Remote CI: pending at preparation time. No claim of a passing remote workflow.

## Research results (v0.1.0 only)
Native retry: 301/360 deadline successes, 490 server requests, P95 2714.47 ms. Aegis Learned: 274/360, 441 requests, P95 2164.25 ms. Lower aggregate traffic and latency came with lower reliability; matched successful-operation latency did not improve. No observed deadline violations in this benchmark; no hard guarantee.

## Limitations
Not production-ready. Cold-start deadline failures observed; prepare before accepting requests. Shared-client concurrency, hard cancellation, extended learned-mode live verification and HTTP-date Retry-After support remain unverified or unsupported. Model binaries and training datasets are not distributed; learned mode requires trusted original model artifacts. No license is granted by these notes; confirm rights and choose a license before public reuse or distribution.

## Release gate
Confirm the GitHub Actions workflow passes on the exact release commit. Confirm release permissions, licensing and author/supervisor approval. Publish as a pre-release, not a stable production release.
