import ballerina/http;
import ballerina/io;
import ballerina/time;
import ballerina/lang.runtime;

public function main() returns error? {
    http:Client client = check new ("http://127.0.0.1:8080", {timeout: 1});
    decimal origin = time:monotonicNow();
    io:println("AEGIS_CSV,request_id,elapsed_ms,latency_ms,status_code,outcome");
    foreach int i in 1 ... 60 {
        decimal started = time:monotonicNow();
        http:Response|error result = client->/work.get();
        int status = 0;
        string outcome = "transport_error";
        if result is http:Response {
            status = result.statusCode;
            string|error body = result.getTextPayload();
            if body is error {
                outcome = "body_error";
            } else if status >= 200 && status < 300 {
                outcome = "success";
            } else {
                outcome = "http_error";
            }
        }
        decimal finished = time:monotonicNow();
        io:println("AEGIS_CSV,", i, ",", (started-origin)*1000d, ",", (finished-started)*1000d, ",", status, ",", outcome);
        runtime:sleep(0.2);
    }
}
