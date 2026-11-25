import ballerina/test;

@test:Config {}
function retryStatusMatrix() {
    foreach int status in [408, 429, 502, 503, 504] {
        test:assertTrue(retryable(status, "http_error"));
    }
    foreach int status in [200, 301, 400, 401, 403, 404, 409, 422, 500, 501] {
        test:assertFalse(retryable(status, "http_error"));
    }
    test:assertTrue(retryable(0, "transport_error"));
    test:assertFalse(retryable(503, "success"));
    test:assertFalse(retryable(503, "unknown"));
}

@test:Config {}
function actionBudgetMatrix() {
    foreach int backoff in [0, 100, 300, 500] {
        foreach int timeout in [200, 400, 700, 1000, 1500] {
            Action action = {backoffMs: backoff, timeoutMs: timeout};
            decimal boundary = <decimal>(backoff + timeout + 250);
            test:assertTrue(feasibleAction(action, boundary, 250));
            test:assertFalse(feasibleAction(action, boundary - 0.001d, 250));
            test:assertTrue(feasibleAction(action, boundary + 1d, 250));
        }
    }
}

@test:Config {}
function rejectInvalidActions() {
    foreach int backoff in [-1, 1, 99, 501, 10000] {
        test:assertFalse(feasibleAction({backoffMs:backoff, timeoutMs:400}, 30000d, 250));
    }
    foreach int timeout in [-1, 0, 199, 201, 1501, 10000] {
        test:assertFalse(feasibleAction({backoffMs:0, timeoutMs:timeout}, 30000d, 250));
    }
    test:assertFalse(feasibleAction({backoffMs:0, timeoutMs:200}, 0d, 250));
    test:assertFalse(feasibleAction({backoffMs:0, timeoutMs:200}, -1d, 250));
}

@test:Config {}
function invalidConfigurations() {
    Client|error shortDeadline = new ("http://127.0.0.1:8080", {deadlineMs:449});
    Client|error wrongReserve = new ("http://127.0.0.1:8080", {reserveMs:0});
    Client|error badMode = new ("http://127.0.0.1:8080", {mode:"invalid"});
    test:assertTrue(shortDeadline is error);
    test:assertTrue(wrongReserve is error);
    test:assertTrue(badMode is error);
    foreach string mode in ["none", "fixed", "learned"] {
        Client|error valid = new ("http://127.0.0.1:8080", {deadlineMs:450, mode:mode});
        test:assertTrue(valid is Client);
    }
}
