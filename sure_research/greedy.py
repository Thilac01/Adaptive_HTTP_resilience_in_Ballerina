"""One-step recovery baseline for the payment prototype."""

from collections.abc import Mapping
from math import isclose, isfinite

from .admission import Action, ExecutionEvidence, VerifiedDeduplication, admissible_actions


# Order matches the simulator states: unstarted, processing, completed.
StateProbabilities = tuple[float, float, float]


def choose_greedy(
    *,
    belief: StateProbabilities,
    confirmation_probabilities: Mapping[Action, StateProbabilities],
    request_id: str,
    evidence: ExecutionEvidence,
    deduplication: VerifiedDeduplication | None,
    remaining_ms: int,
    remaining_calls: int,
    duration_bounds_ms: Mapping[Action, int],
    call_cost: float = 0.0,
    time_cost_per_ms: float = 0.0,
) -> tuple[Action, float]:
    """Select an action using only its immediate confirmation and costs.

    Each modeled network action supplies the chance of confirmation before its
    timeout, conditional on the payment's current state. The simulator uses
    fixed action durations, so the admission bounds also serve as durations.
    """
    if len(belief) != 3 or not all(0 <= p <= 1 for p in belief) or not isclose(sum(belief), 1.0):
        raise ValueError("Expected a probability distribution over three payment states")
    if not all(isfinite(cost) and cost >= 0 for cost in (call_cost, time_cost_per_ms)):
        raise ValueError("Costs must be finite and non-negative")

    eligible = admissible_actions(
        request_id=request_id,
        evidence=evidence,
        deduplication=deduplication,
        remaining_ms=remaining_ms,
        remaining_calls=remaining_calls,
        duration_bounds_ms=duration_bounds_ms,
    )
    # Stop is the zero-reward option and wins ties against taking an action.
    best_action: Action = "stop"
    best_score = 0.0
    for action in eligible:
        if action == "stop":
            continue
        score = -time_cost_per_ms * duration_bounds_ms[action]
        if action != "wait":
            probabilities = confirmation_probabilities.get(action)
            if probabilities is None:
                continue
            if len(probabilities) != 3 or not all(0 <= p <= 1 for p in probabilities):
                raise ValueError(f"Expected three confirmation probabilities for {action}")
            # Average the state-specific outcomes using the current belief.
            score += sum(weight * chance for weight, chance in zip(belief, probabilities))
            score -= call_cost
        if score > best_score:
            best_action, best_score = action, score
    return best_action, best_score
