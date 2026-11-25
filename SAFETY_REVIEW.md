# Safety verification (post-evaluation)

The frozen native implementation is unchanged. Run `bal test` inside `aegis_native` with the additional test file. These are deterministic unit tests, not proof of end-to-end safety.

## Scope and limitations

- GET-only API; no POST/idempotency-key implementation or semantic safety claim for arbitrary operations.
- One explicit application retry at most; implicit transport behavior requires separate instrumentation.
- Inference errors or missing history leave the stop action selected. Invalid action parameters are rejected by the existing allowlist.
- Budget checked before and after backoff; no hard cancellation guarantee. Reserve is fixed at 250 ms by constructor validation.
- Retry-After is not enforced by the native client; HTTP 429 is retryable. Do not claim compliance with server retry instructions.
- Each attempt constructs a client; connection reuse and inference overhead require further investigation.
- Sequential usage only; concurrent calls to the same client are not validated.

## Pending integration tests

1. Inference service unavailable, malformed response, out-of-grid action, stop action, slow response.
2. Target server returns non-retryable 400, 401, 404; verify received request count.
3. Eligible 503 followed by 200; verify at most one explicit retry.
4. Deadline exhaustion during backoff and response; verify result flags and record overruns.
5. Verify server-directed Retry-After handling before production use (not implemented).

Do not alter frozen benchmark artifacts. Any future runtime modifications require separately labeled evaluation.
