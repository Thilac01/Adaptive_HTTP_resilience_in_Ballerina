import ballerina/test;
@test:Config {}
function methodSafety() {
    Method[] mutations = ["POST", "PUT", "PATCH", "DELETE"];
    foreach Method method in mutations {
        foreach string mode in ["fixed", "learned", "none"] {
            test:assertFalse(recoveryAllowed(method, mode));
        }
    }
    test:assertTrue(recoveryAllowed("GET", "learned"));
    test:assertTrue(recoveryAllowed("GET", "fixed"));
    test:assertTrue(recoveryAllowed("HEAD", "fixed"));
    test:assertFalse(recoveryAllowed("HEAD", "learned"));
    test:assertFalse(recoveryAllowed("GET", "none"));
}
