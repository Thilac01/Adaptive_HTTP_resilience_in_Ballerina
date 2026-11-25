import ballerina/test;
@test:Config {}
function retryAfterParsing() {
    test:assertEquals(retryAfterDelay("0", 3000), 0);
    test:assertEquals(retryAfterDelay("1", 3000), 1000);
    test:assertEquals(retryAfterDelay(" 2 ", 3000), 2000);
    foreach string invalid in ["", "-1", "+1", "1.5", "120", "Wed, 25 Nov 2026 00:00:00 GMT", "999999999999999999999999999"] {
        int|error delay = retryAfterDelay(invalid, 3000);
        test:assertTrue(delay is error);
    }
}
