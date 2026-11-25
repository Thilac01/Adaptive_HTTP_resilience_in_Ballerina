import ballerina/http;
import ballerina/io;
import ballerina/time;
configurable int operations = 30;
configurable int warmup = 15;
configurable int retryCount = 1;
configurable int deadlineMs = 3000;
configurable decimal timeoutSeconds = 1.25d;
public function main() returns error? {
    http:Client api = check new ("http://127.0.0.1:8080", {
        timeout: timeoutSeconds,
        retryConfig: {count: retryCount, interval: 0.1d, backOffFactor: 1.0,
            maxWaitInterval: 0.1d, statusCodes: [408, 429, 502, 503, 504]}
    });
    foreach int i in 1 ... (warmup + operations) {
        string path = "/op/" + i.toString();
        decimal startedAt = time:monotonicNow();
        http:Response|error response = api->get(path);
        decimal elapsedMs = (time:monotonicNow() - startedAt) * 1000d;
        int status = 0;
        string outcome = "transport_error";
        if response is http:Response {
            status = response.statusCode;
            outcome = status >= 200 && status < 300 ? "success" : "http_error";
        }
        if i > warmup {
            json result = {operation: i - warmup, status: status, outcome: outcome,
                elapsedMs: elapsedMs, deadlineSuccess: outcome == "success" && elapsedMs <= <decimal>deadlineMs,
                deadlineViolation: elapsedMs > <decimal>deadlineMs};
            io:println("AEGIS_RESULT,", result);
        }
    }
}
