# Aegis developer guide (experimental release candidate)

## Versions
- `thilac/aegis:0.1.0`: frozen GET-only research implementation. The 1,440-operation comparison belongs to this version only.
- `thilac/aegis_next:0.2.0`: separate sequential HTTP client, not a replacement for the evaluated research package.

## Setup (Ubuntu/WSL, Ballerina 2201.10.2, Python 3.8+)
From the project root:
```bash
(cd aegis_next && bal test && bal pack && bal push --repository local)
(cd next_demo && bal build --offline)
python3 tests/integration_next.py
python3 tests/integration_retry_after.py
```
Stop existing services on ports 8080/8091 before integration tests. The above fixed-mode tests do not require models. For v0.1.0, build/install aegis_native and build native_demo, then run tests/integration_native.py (mock inference).

## Usage
```ballerina
import ballerina/http;
import ballerina/io;
import thilac/aegis_next;

public function main() returns error? {
    aegis_next:Client api = check new ("http://127.0.0.1:8080", {mode:"fixed", deadlineMs:3000});
    aegis_next:Preparation startup = api.prepare("/health");
    if !startup.ready { return error("Service not ready"); }
    aegis_next:Result r = api.get("/products", {"Accept":"application/json"});
    http:Response? response = r.response;
    if response is http:Response {
        io:println(check response.getJsonPayload());
    }
}
```
Declare the local package dependency as in next_demo/Ballerina.toml. The health endpoint must be a safe GET returning small valid JSON. Preparation is outside application deadlines and does not initialize every retry transport or the inference service. Inspect status, outcome, attempts and deadline flags; a returned response is not necessarily successful.

`request(method,path,payload,headers)` supports GET, HEAD, POST, PUT, PATCH, DELETE. JSON bodies are transmitted for POST/PUT/PATCH. Automatic Aegis recovery is limited to GET (fixed/learned) and HEAD (fixed only); mutations get one explicit attempt. No idempotency-key protocol is implemented. Use separate client instances per calling strand; shared-instance concurrency is unsupported. Timeouts are best effort, not hard cancellation; response body processing by callers occurs outside reported operation timing. Integer Retry-After is supported conservatively; HTTP dates and ambiguous values suppress retry. See RETRY_AFTER.md and STARTUP_READINESS.md.

## Learned mode
Use mode `learned` and run the local inference service (`python inference_server.py`, default loopback port 8091) in an environment with requirements.txt dependencies and the original trusted frozen model files. Model binaries and training data are intentionally absent; do not load untrusted joblib files. The service checks model hashes. Ten GET observations are required; preparation does not count. The extended client has not undergone a new learned-mode comparative evaluation.

## Verification record (2026-11-25)
User-reported local results: v0.1.0 5 unit tests and 10 mock integration tests passed in the latest complete run (earlier intermittent warm-up failure). v0.2.0 2 unit tests, 8 Retry-After scenarios and 6 prepared fixed-mode HTTP method scenarios passed. Server request gap for Retry-After: 1 was 1.053 s. Cold-start test failed previously (3333.94 ms versus 3000 ms deadline); it remains a strict diagnostic, not part of the passing prepared suite. Concurrency, hard cancellation and full RFC Retry-After compliance are not established. CI configuration is included but remote execution has not been verified.

## Public upload
Extract the archive and upload contents at repository root, preserving paths including .github/workflows. Existing root README is deliberately omitted. Review source/data release permissions before committing. No manuscript, raw experiment dataset, credentials, or model binaries are included. This is an experimental research release candidate, not a production-ready client.
