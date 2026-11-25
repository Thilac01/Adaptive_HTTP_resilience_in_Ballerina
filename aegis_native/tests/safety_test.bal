import ballerina/test;
@test:Config {}
function safetyTest() {
    test:assertTrue(retryable(503, "http_error"));
    test:assertFalse(retryable(400, "http_error"));
    test:assertFalse(retryable(200, "success"));
    test:assertTrue(feasibleAction({backoffMs:100, timeoutMs:400}, 750, 250));
    test:assertFalse(feasibleAction({backoffMs:100, timeoutMs:400}, 749, 250));
    test:assertFalse(feasibleAction({backoffMs:0, timeoutMs:123}, 3000, 250));
}
