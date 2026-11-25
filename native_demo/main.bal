import thilac/aegis;
import ballerina/io;
configurable string inferenceUrl = "http://127.0.0.1:8091";
configurable string mode = "learned";
configurable int operations = 30;
configurable int warmup = 15;
configurable int deadlineMs = 3000;
public function main() returns error? {
    aegis:Client api = check new ("http://127.0.0.1:8080", {inferenceUrl, mode, deadlineMs});
    foreach int i in 1 ... (warmup + operations) {
        string path = "/op/" + i.toString();
        aegis:Result result = api.get(path);
        if i > warmup {
            json row = {operation: i - warmup, status: result.status, outcome: result.outcome,
                attempts: result.attempts, elapsedMs: result.elapsedMs, deadlineSuccess: result.deadlineSuccess,
                deadlineViolation: result.deadlineViolation};
            io:println("AEGIS_RESULT,", row);
        }
    }
}
