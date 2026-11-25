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
public type Preparation record {| boolean ready; int status; decimal elapsedMs; |};
public type Method "GET"|"HEAD"|"POST"|"PUT"|"PATCH"|"DELETE";
public type Result record {|
    http:Response? response;
    int status;
    string outcome;
    int attempts;
    decimal elapsedMs;
    boolean deadlineSuccess;
    boolean deadlineViolation;
|};
type Attempt record {| int status; string outcome; decimal latency; http:Response? response; |};

// The frozen model was trained on GET only. Never retry mutations here.
public function recoveryAllowed(Method method, string mode) returns boolean {
    return mode != "none" && (method == "GET" || (method == "HEAD" && mode == "fixed"));
}

public function retryable(int status, string outcome) returns boolean {
    return outcome == "transport_error" || (outcome == "http_error" && (status == 408 || status == 429 || status == 502 || status == 503 || status == 504));
}
public function feasibleAction(Action action, decimal remaining, int reserve) returns boolean {
    return (action.backoffMs == 0 || action.backoffMs == 100 || action.backoffMs == 300 || action.backoffMs == 500) &&
        (action.timeoutMs == 200 || action.timeoutMs == 400 || action.timeoutMs == 700 || action.timeoutMs == 1000 || action.timeoutMs == 1500) &&
        remaining >= <decimal>(action.backoffMs + action.timeoutMs + reserve);
}
// Delta-seconds only. Unsupported HTTP dates and malformed values fail closed.
// Values above the operation budget are rejected before multiplication.
public function retryAfterDelay(string value, int deadlineMs) returns int|error {
    string cleaned = value.trim();
    byte[] digits = cleaned.toBytes();
    if digits.length() == 0 { return error("Empty Retry-After"); }
    foreach byte digit in digits {
        if digit < 48 || digit > 57 { return error("Unsupported Retry-After"); }
    }
    int|error seconds = 'int:fromString(cleaned);
    if seconds is error { return seconds; }
    if seconds > deadlineMs / 1000 { return error("Retry-After exceeds deadline"); }
    return seconds * 1000;
}
function serverDelay(http:Response? response, int deadlineMs) returns int|error {
    if response is http:Response && response.hasHeader("Retry-After") {
        string[]|error values = response.getHeaders("Retry-After");
        if values is error { return values; }
        if values.length() != 1 { return error("Ambiguous Retry-After"); }
        return retryAfterDelay(values[0], deadlineMs);
    }
    return 0;
}
function elapsed(decimal started) returns decimal {
    return (time:monotonicNow() - started) * 1000d;
}

// Experimental sequential client. Use a separate instance per calling strand.
public class Client {
    private string endpoint;
    private Config cfg;
    private json[][] history = [];
    private map<http:Client> clients = {};
    public function init(string endpoint, Config cfg = {}) returns error? {
        if cfg.deadlineMs < 450 || cfg.reserveMs != 250 || (cfg.mode != "learned" && cfg.mode != "fixed" && cfg.mode != "none") {
            return error("Invalid configuration");
        }
        self.endpoint = endpoint;
        self.cfg = cfg;
    }
    private function transport(int timeoutMs) returns http:Client|error {
        string key = timeoutMs.toString();
        http:Client? cached = self.clients[key];
        if cached is http:Client { return cached; }
        http:Client target = check new (self.endpoint, {timeout: <decimal>timeoutMs / 1000d});
        self.clients[key] = target;
        return target;
    }
    private function attempt(Method method, string path, json payload, map<string> headers, int timeoutMs) returns Attempt {
        decimal startedAt = time:monotonicNow();
        http:Client|error target = self.transport(timeoutMs);
        int status = 0;
        string outcome = "transport_error";
        http:Response? raw = ();
        if target is http:Client {
            http:Request req = new;
            if method == "POST" || method == "PUT" || method == "PATCH" {
                req.setJsonPayload(payload);
            }
            foreach var [name, value] in headers.entries() {
                req.setHeader(name, value);
            }
            http:Response|error response = target->execute(method, path, req);
            if response is http:Response {
                raw = response;
                status = response.statusCode;
                outcome = status >= 200 && status < 300 ? "success" : "http_error";
            }
        }
        decimal latency = elapsed(startedAt);
        return {status, outcome, latency, response:raw};
    }
    // Explicit startup probe, outside application operation deadlines.
    // Use only a safe endpoint with a small JSON response. No retries or history updates.
    public function prepare(string path, map<string> headers = {}) returns Preparation {
        decimal startedAt = time:monotonicNow();
        int firstTimeout = self.cfg.deadlineMs - self.cfg.reserveMs;
        if firstTimeout > 2000 { firstTimeout = 2000; }
        Attempt probe = self.attempt("GET", path, (), headers, firstTimeout);
        boolean ready = false;
        http:Response? response = probe.response;
        if response is http:Response {
            json|error body = response.getJsonPayload();
            ready = probe.outcome == "success" && !(body is error);
        }
        return {ready, status:probe.status, elapsedMs:elapsed(startedAt)};
    }
    public function get(string path, map<string> headers = {}) returns Result {
        return self.request("GET", path, (), headers);
    }
    public function request(Method method, string path, json payload = (), map<string> headers = {}) returns Result {
        decimal startedAt = time:monotonicNow();
        int firstTimeout = self.cfg.deadlineMs - self.cfg.reserveMs;
        if firstTimeout > 2000 { firstTimeout = 2000; }
        Attempt first = self.attempt(method, path, payload, headers, firstTimeout);
        decimal firstRoundtrip = elapsed(startedAt);
        if method == "GET" {
            self.history.push([first.latency, first.outcome]);
            if self.history.length() > 10 { _ = self.history.shift(); }
        }
        Attempt last = first;
        int attempts = 1;
        if retryable(first.status, first.outcome) && recoveryAllowed(method, self.cfg.mode) {
            Action action = {backoffMs:0, timeoutMs:0};
            decimal remaining = <decimal>self.cfg.deadlineMs - elapsed(startedAt);
            int|error minimumDelay = serverDelay(first.response, self.cfg.deadlineMs);
            int waitMs = 0;
            if minimumDelay is int && self.cfg.mode == "fixed" {
                foreach int budget in [1000,700,400,200] {
                    Action candidate = {backoffMs:100, timeoutMs:budget};
                    int proposedWait = minimumDelay > candidate.backoffMs ? minimumDelay : candidate.backoffMs;
                    if feasibleAction(candidate, remaining, self.cfg.reserveMs) && remaining >= <decimal>proposedWait + <decimal>budget + <decimal>self.cfg.reserveMs {
                        action = candidate; waitMs = proposedWait; break;
                    }
                }
            } else if minimumDelay is int && self.cfg.mode == "learned" && self.history.length() == 10 && remaining >= 450d {
                http:Client|error inference = new (self.cfg.inferenceUrl, {timeout:0.25d});
                if inference is http:Client {
                    json inferencePayload = {history:self.history, deadline_ms:self.cfg.deadlineMs, reserve_ms:self.cfg.reserveMs, first_timeout_ms:firstTimeout, first_latency_ms:first.latency, first_roundtrip_ms:firstRoundtrip, first_status:first.status, remaining_ms:remaining};
                    Action|error selected = inference->post("/decide", inferencePayload);
                    if selected is Action && selected.backoffMs >= minimumDelay { action = selected; waitMs = selected.backoffMs; }
                }
            }
            decimal beforeBackoff = <decimal>self.cfg.deadlineMs - elapsed(startedAt);
            if feasibleAction(action, beforeBackoff, self.cfg.reserveMs) && beforeBackoff >= <decimal>waitMs + <decimal>action.timeoutMs + <decimal>self.cfg.reserveMs {
                runtime:sleep(<decimal>waitMs / 1000d);
                decimal afterBackoff = <decimal>self.cfg.deadlineMs - elapsed(startedAt);
                if afterBackoff >= <decimal>(action.timeoutMs + self.cfg.reserveMs) {
                    attempts += 1;
                    last = self.attempt(method, path, payload, headers, action.timeoutMs);
                }
            }
        }
        decimal duration = elapsed(startedAt);
        return {response:last.response, status:last.status, outcome:last.outcome, attempts, elapsedMs:duration, deadlineSuccess:last.outcome == "success" && duration <= <decimal>self.cfg.deadlineMs, deadlineViolation:duration > <decimal>self.cfg.deadlineMs};
    }
}
