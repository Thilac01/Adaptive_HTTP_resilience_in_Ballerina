import ballerina/http;
import ballerina/time;
import ballerina/lang.runtime;

public type Config record {|
    int deadlineMs = 3000;
    int reserveMs = 250;
    string inferenceUrl = "http://127.0.0.1:8091";
    string mode = "learned";
|};
public type Action record {| int backoffMs; int timeoutMs; |};
public type Result record {|
    int status;
    string outcome;
    int attempts;
    decimal elapsedMs;
    boolean deadlineSuccess;
    boolean deadlineViolation;
|};
type Attempt record {| int status; string outcome; decimal latency; |};

public function retryable(int status, string outcome) returns boolean {
    return outcome == "transport_error" || (outcome == "http_error" && (status == 408 || status == 429 || status == 502 || status == 503 || status == 504));
}
public function feasibleAction(Action action, decimal remaining, int reserve) returns boolean {
    return (action.backoffMs == 0 || action.backoffMs == 100 || action.backoffMs == 300 || action.backoffMs == 500) &&
        (action.timeoutMs == 200 || action.timeoutMs == 400 || action.timeoutMs == 700 || action.timeoutMs == 1000 || action.timeoutMs == 1500) &&
        remaining >= <decimal>(action.backoffMs + action.timeoutMs + reserve);
}
function elapsed(decimal started) returns decimal {
    return (time:monotonicNow() - started) * 1000d;
}

// Sequential GET-only research client. Use a separate instance per calling strand.
public class Client {
    private string endpoint;
    private Config cfg;
    private json[][] history = [];
    public function init(string endpoint, Config cfg = {}) returns error? {
        if cfg.deadlineMs < 450 || cfg.reserveMs != 250 || (cfg.mode != "learned" && cfg.mode != "fixed" && cfg.mode != "none") {
            return error("Invalid configuration");
        }
        self.endpoint = endpoint;
        self.cfg = cfg;
    }
    private function attempt(string path, int timeoutMs) returns Attempt {
        decimal startedAt = time:monotonicNow();
        http:Client|error target = new (self.endpoint, {timeout: <decimal>timeoutMs / 1000d});
        int status = 0;
        string outcome = "transport_error";
        if target is http:Client {
            http:Response|error response = target->get(path);
            if response is http:Response {
                status = response.statusCode;
                outcome = status >= 200 && status < 300 ? "success" : "http_error";
            }
        }
        decimal latency = elapsed(startedAt);
        return {status, outcome, latency};
    }
    public function get(string path) returns Result {
        decimal startedAt = time:monotonicNow();
        int firstTimeout = self.cfg.deadlineMs - self.cfg.reserveMs;
        if firstTimeout > 2000 { firstTimeout = 2000; }
        Attempt first = self.attempt(path, firstTimeout);
        decimal firstRoundtrip = elapsed(startedAt);
        self.history.push([first.latency, first.outcome]);
        if self.history.length() > 10 { _ = self.history.shift(); }
        Attempt last = first;
        int attempts = 1;
        if retryable(first.status, first.outcome) && self.cfg.mode != "none" {
            Action action = {backoffMs:0, timeoutMs:0};
            decimal remaining = <decimal>self.cfg.deadlineMs - elapsed(startedAt);
            if self.cfg.mode == "fixed" {
                foreach int budget in [1000,700,400,200] {
                    Action candidate = {backoffMs:100, timeoutMs:budget};
                    if feasibleAction(candidate, remaining, self.cfg.reserveMs) { action = candidate; break; }
                }
            } else if self.history.length() == 10 && remaining >= 450d {
                http:Client|error inference = new (self.cfg.inferenceUrl, {timeout:0.25d});
                if inference is http:Client {
                    json payload = {history:self.history, deadline_ms:self.cfg.deadlineMs, reserve_ms:self.cfg.reserveMs, first_timeout_ms:firstTimeout, first_latency_ms:first.latency, first_roundtrip_ms:firstRoundtrip, first_status:first.status, remaining_ms:remaining};
                    Action|error selected = inference->post("/decide", payload);
                    if selected is Action { action = selected; }
                }
            }
            decimal beforeBackoff = <decimal>self.cfg.deadlineMs - elapsed(startedAt);
            if feasibleAction(action, beforeBackoff, self.cfg.reserveMs) {
                runtime:sleep(<decimal>action.backoffMs / 1000d);
                decimal afterBackoff = <decimal>self.cfg.deadlineMs - elapsed(startedAt);
                if afterBackoff >= <decimal>(action.timeoutMs + self.cfg.reserveMs) {
                    attempts += 1;
                    last = self.attempt(path, action.timeoutMs);
                }
            }
        }
        decimal duration = elapsed(startedAt);
        return {status:last.status, outcome:last.outcome, attempts, elapsedMs:duration, deadlineSuccess:last.outcome == "success" && duration <= <decimal>self.cfg.deadlineMs, deadlineViolation:duration > <decimal>self.cfg.deadlineMs};
    }
}
