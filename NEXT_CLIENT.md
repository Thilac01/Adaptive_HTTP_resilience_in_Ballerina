# Aegis Next 0.2.0 — experimental draft
Separate package `thilac/aegis_next`, preserving `thilac/aegis:0.1.0` and its benchmark. Supports GET, HEAD, POST, PUT, PATCH, DELETE; JSON bodies for POST/PUT/PATCH; custom headers; raw final HTTP response. Only GET may use learned retries; fixed mode also permits HEAD. All mutations are single application-level attempts even when given idempotency keys; no server deduplication assumed. No new model training. Clients cached by timeout per sequential instance; actual socket reuse not verified. Deadline is best effort and response body consumption is outside measured operation latency. Retry-After, streaming, concurrency, response disposal on retries and hard cancellation are not validated. Do not describe this as production-ready. Compile and test on Ballerina 2201.10.2 before uploading.

```ballerina
import thilac/aegis_next;
import ballerina/http;
import ballerina/io;
public function main() returns error? {
    aegis_next:Client api = check new ("http://127.0.0.1:8080", {mode:"none"});
    aegis_next:Result result = api.get("/products");
    http:Response? response = result.response;
    if response is http:Response {
        json body = check response.getJsonPayload();
        io:println(body);
    }
}
```
