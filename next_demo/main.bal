import ballerina/http;
import ballerina/io;
import thilac/aegis_next;

configurable boolean warmup = true;
configurable boolean retryAfterSuite = false;

public function main() returns error? {
    aegis_next:Client api = check new ("http://127.0.0.1:8080", {mode:"fixed"});
    if warmup {
        aegis_next:Preparation preparation = api.prepare("/warmup", {"X-Aegis-Test":"yes"});
        json startup = {ready:preparation.ready, status:preparation.status, elapsedMs:preparation.elapsedMs};
        io:println("PREPARE_RESULT,", startup.toJsonString());
        if !preparation.ready { return error("Startup probe failed"); }
    }
    if retryAfterSuite {
        foreach string name in ["one", "zero", "long", "bad", "date", "duplicate", "budget", "mutation"] {
            aegis_next:Method method = name == "mutation" ? "POST" : "GET";
            aegis_next:Result r = api.request(method, "/retry-after/" + name, {name:"robot"});
            json result = {name:name, status:r.status, attempts:r.attempts, deadlineViolation:r.deadlineViolation, elapsedMs:r.elapsedMs};
            io:println("RETRY_RESULT,", result.toJsonString());
        }
        return;
    }
    aegis_next:Method[] methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"];
    foreach aegis_next:Method method in methods {
        aegis_next:Result r = api.request(method, "/" + method, {name:"robot"}, {"X-Aegis-Test":"yes"});
        http:Response? response = r.response;
        json body = ();
        if response is http:Response && method != "HEAD" {
            body = check response.getJsonPayload();
        }
        json result = {method:method, status:r.status, attempts:r.attempts, deadlineViolation:r.deadlineViolation, elapsedMs:r.elapsedMs, body:body};
        io:println("NEXT_RESULT,", result.toJsonString());
    }
}
