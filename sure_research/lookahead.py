"""Depth-two belief planning with fixed action durations."""

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from math import isclose
from typing import Literal

from .admission import Action, ExecutionEvidence, VerifiedDeduplication, admissible_actions
from .greedy import StateProbabilities, choose_greedy, immediate_utility


State = Literal["unstarted", "processing", "completed"]
STATES: tuple[State, ...] = ("unstarted", "processing", "completed")


@dataclass(frozen=True)
class Outcome:
    next_state: State
    observation: str
    probability: float


# Each action supplies one row of outcomes per current state, in STATES order.
OutcomeModel = Mapping[Action, tuple[tuple[Outcome, ...], ...]]


def confirmation_chances(model: OutcomeModel) -> dict[Action, StateProbabilities]:
    chances: dict[Action, StateProbabilities] = {}
    for action, rows in model.items():
        if len(rows) != len(STATES):
            raise ValueError(f"Expected one outcome row per state for {action}")
        row_chances = []
        for row in rows:
            if any(not 0 <= outcome.probability <= 1 for outcome in row) or not isclose(
                sum(outcome.probability for outcome in row), 1.0
            ):
                raise ValueError(f"Expected an outcome distribution for {action}")
            for outcome in row:
                if outcome.next_state not in STATES:
                    raise ValueError(f"Unknown next state for {action}")
                if outcome.observation == "confirmed" and (
                    action == "wait" or outcome.next_state != "completed"
                ):
                    raise ValueError(f"Invalid confirmation outcome for {action}")
            row_chances.append(sum(o.probability for o in row if o.observation == "confirmed"))
        chances[action] = (row_chances[0], row_chances[1], row_chances[2])
    return chances


def observation_branches(
    belief: StateProbabilities, rows: tuple[tuple[Outcome, ...], ...]
) -> Iterator[tuple[str, float, StateProbabilities]]:
    """Yield each possible observation, its probability, and its posterior."""
    mass_by_observation = defaultdict(lambda: [0.0, 0.0, 0.0])
    for weight, row in zip(belief, rows):
        for outcome in row:
            mass_by_observation[outcome.observation][STATES.index(outcome.next_state)] += (
                weight * outcome.probability
            )
    for observation, (unstarted, processing, completed) in mass_by_observation.items():
        probability = unstarted + processing + completed
        if probability > 0:
            yield observation, probability, (
                unstarted / probability, processing / probability, completed / probability
            )


def choose_lookahead(
    *,
    belief: StateProbabilities,
    model: OutcomeModel,
    request_id: str,
    evidence: ExecutionEvidence,
    deduplication: VerifiedDeduplication | None,
    remaining_ms: int,
    remaining_calls: int,
    duration_bounds_ms: Mapping[Action, int],
    call_cost: float = 0.0,
    time_cost_per_ms: float = 0.0,
) -> tuple[Action, float]:
    """Choose the first action while allowing one observation-dependent follow-up."""
    chances = confirmation_chances(model)
    best_action, best_score = choose_greedy(
        belief=belief,
        confirmation_probabilities=chances,
        request_id=request_id,
        evidence=evidence,
        deduplication=deduplication,
        remaining_ms=remaining_ms,
        remaining_calls=remaining_calls,
        duration_bounds_ms=duration_bounds_ms,
        call_cost=call_cost,
        time_cost_per_ms=time_cost_per_ms,
    )
    for action in admissible_actions(
        request_id=request_id,
        evidence=evidence,
        deduplication=deduplication,
        remaining_ms=remaining_ms,
        remaining_calls=remaining_calls,
        duration_bounds_ms=duration_bounds_ms,
    ):
        if action == "stop" or action not in model:
            continue
        duration = duration_bounds_ms[action]
        calls = int(action != "wait")
        probability = sum(weight * chance for weight, chance in zip(belief, chances[action]))
        score = immediate_utility(probability, duration, calls, call_cost, time_cost_per_ms)
        # Another mutation can make earlier no-commit evidence stale.
        next_evidence = (
            ExecutionEvidence.AMBIGUOUS if action in ("retry", "failover") else evidence
        )
        for observation, branch_probability, posterior in observation_branches(belief, model[action]):
            if observation == "confirmed":
                continue
            _, follow_up_score = choose_greedy(
                belief=posterior,
                confirmation_probabilities=chances,
                request_id=request_id,
                evidence=next_evidence,
                deduplication=deduplication,
                remaining_ms=remaining_ms - duration,
                remaining_calls=remaining_calls - calls,
                duration_bounds_ms=duration_bounds_ms,
                call_cost=call_cost,
                time_cost_per_ms=time_cost_per_ms,
            )
            score += branch_probability * follow_up_score
        if score > best_score:
            best_action, best_score = action, score
    return best_action, best_score
