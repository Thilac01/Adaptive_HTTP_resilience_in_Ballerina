"""Versioned JSON decision interface for a trusted payment recovery client."""

import json
import sys
from math import isfinite

from .admission import ExecutionEvidence, VerifiedDeduplication
from .benchmark import CALL_COST, DURATIONS, TIME_COST_PER_MS, outcome_model
from .greedy import choose_greedy
from .lookahead import STATES, choose_lookahead, confirmation_chances


def decide(request):
    """Return an action recommendation without issuing any payment requests."""
    if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
        raise ValueError("Expected a version 1 JSON request")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string")
    policy = request.get("policy")
    if policy not in ("greedy", "lookahead"):
        raise ValueError("policy must be greedy or lookahead")
    evidence = ExecutionEvidence(request.get("evidence"))
    time_left = request.get("remaining_ms")
    calls_left = request.get("remaining_calls")
    if any(type(value) is not int or value < 0 for value in (time_left, calls_left)):
        raise ValueError("Remaining time and calls must be non-negative integers")

    belief = probabilities(request.get("belief"), STATES)
    availability = probabilities(request.get("availability"), ("reconcile", "retry", "failover"))
    endpoints = request.get("verified_deduplication_endpoints", [])
    if not isinstance(endpoints, list) or any(endpoint not in ("primary", "backup") for endpoint in endpoints):
        raise ValueError("Verified deduplication endpoints must be primary or backup")
    verified = VerifiedDeduplication(request_id, frozenset(endpoints)) if endpoints else None
    context = dict(
        belief=tuple(belief[state] for state in STATES),
        request_id=request_id, evidence=evidence, deduplication=verified,
        remaining_ms=time_left, remaining_calls=calls_left, duration_bounds_ms=DURATIONS,
        call_cost=CALL_COST, time_cost_per_ms=TIME_COST_PER_MS,
    )
    model = outcome_model(availability)
    if policy == "greedy":
        action, score = choose_greedy(confirmation_probabilities=confirmation_chances(model), **context)
    else:
        action, score = choose_lookahead(model=model, **context)
    return {
        "version": 1, "request_id": request_id, "action": action, "score": score,
        "reserved_ms": DURATIONS.get(action, 0), "recovery_calls": int(action not in ("wait", "stop")),
    }


def probabilities(value, keys):
    if (
        not isinstance(value, dict) or value.keys() != set(keys)
        or any(type(number) not in (int, float) or not 0 <= number <= 1 or not isfinite(number)
               for number in value.values())
    ):
        raise ValueError(f"Expected probabilities in [0, 1] for {', '.join(keys)}")
    return value


def main():
    try:
        response = decide(json.load(sys.stdin))
    except ValueError as error:
        print(json.dumps({"version": 1, "action": "stop", "error": str(error)}))
        return 2
    print(json.dumps(response, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
