import ballerina/http;
import ballerina/io;
import ballerina/time;

configurable string endpoint = "http://127.0.0.1:8080";

public function main() returns error? {
    io:println("AEGIS_READY");
    while true {
        string line = io:readln();
        if line == "quit" || line == "" { break; }
        int timeoutMs = check int:fromString(line);
        if timeoutMs < 1 || timeoutMs > 10000 {
            return error("Invalid timeout");
        }
        decimal started = time:monotonicNow();
        http:Client httpClient = check new (endpoint, {
            timeout: <decimal>timeoutMs / 1000
        });
        http:Response|error response = httpClient->get("/");
        int status = 0;
        string outcome = "transport_error";
        if response is http:Response {
            status = response.statusCode;
            outcome = status >= 200 && status < 300 ? "success" : "http_error";
        }
        decimal elapsed = (time:monotonicNow() - started) * 1000;
        io:println("AEGIS_RESULT,", elapsed, ",", status, ",", outcome);
    }
}
