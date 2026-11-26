"""Run paired, virtual-time payment recovery experiments."""

import argparse
import csv
import json
import math
import random
from collections import Counter
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from statistics import mean

from .admission import ExecutionEvidence, VerifiedDeduplication, admissible_actions
from .greedy import choose_greedy
from .lookahead import STATES, Outcome, choose_lookahead, confirmation_chances, observation_branches
from .simulator import PaymentTimeline


INITIAL_TIMEOUT_MS = 400
AUDIT_GRACE_MS = 1_000
COMMIT_MEAN_MS = 220
PRIOR = (12 / 44, 20 / 44, 12 / 44)
DURATIONS = {"wait": 200, "reconcile": 120, "retry": 150, "failover": 180}
CALL_COST = 0.05
TIME_COST_PER_MS = 0.001
POLICIES = ("greedy", "lookahead", "fixed_status", "fixed_backoff")


@dataclass(frozen=True)
class Case:
    case_id: int
    seed: int
    remaining_ms: int
    keyed: bool
    status_availability: float
    primary_availability: float

    @property
    def availability(self):
        return {"reconcile": self.status_availability, "retry": self.primary_availability, "failover": 0.9}


def outcome_model(availability):
    """Build the model given to both planning policies, in simulator state order."""
    model = {}
    for action, duration in DURATIONS.items():
        commit_chance = -math.expm1(-duration / COMMIT_MEAN_MS)
        rows = []
        for state in STATES:
            predicted = (
                (("processing", 1 - commit_chance), ("completed", commit_chance))
                if state == "processing"
                else ((state, 1.0),)
            )
            outcomes = []
            for next_state, weight in predicted:
                if action == "wait":
                    outcomes.append(Outcome(next_state, "waited", weight))
                    continue
                reach = availability[action]
                on_reply = "completed" if action in ("retry", "failover") and next_state == "unstarted" else next_state
                observation = {"unstarted": "unknown", "processing": "pending", "completed": "confirmed"}[on_reply]
                outcomes.append(Outcome(next_state, "timeout", weight * (1 - reach)))
                outcomes.append(Outcome(on_reply, observation, weight * reach))
            rows.append(tuple(outcomes))
        model[action] = tuple(rows)
    return model


class PaymentEpisode:
    """Private simulator state. Policies receive only observations and their budgets."""

    def __init__(self, case):
        sample = random.Random(case.seed)
        state = sample.choices(STATES, weights=PRIOR, k=1)[0]
        if state == "unstarted":
            self.fault = "request_dropped"
            self.timeline = PaymentTimeline(None, None, None)
        elif state == "processing":
            self.fault = "slow_commit"
            commit_ms = INITIAL_TIMEOUT_MS + max(1, math.ceil(sample.expovariate(1 / COMMIT_MEAN_MS)))
            self.timeline = PaymentTimeline(50, commit_ms, commit_ms + 20)
        else:
            self.fault = "response_lost"
            self.timeline = PaymentTimeline(50, 250, None)
        self.case = case
        self.availability = case.availability
        self.elapsed_ms = INITIAL_TIMEOUT_MS
        self.action_counts = Counter()
        self.commit_times = [] if self.timeline.commit_ms is None else [self.timeline.commit_ms]

    def execute(self, action):
        self.elapsed_ms += DURATIONS[action]
        self.action_counts[action] += 1
        if action == "wait":
            return "waited"
        # Match the first draw for each action across policies, even after different preceding actions.
        draw = random.Random(f"{self.case.seed}:{action}:{self.action_counts[action]}").random()
        if draw >= self.availability[action]:
            return "timeout"
        # The fixture gives keyed mutations one shared deduplication ledger across destinations.
        if action in ("retry", "failover") and (not self.case.keyed or not self.commit_times):
            self.commit_times.append(self.elapsed_ms)
        if any(commit <= self.elapsed_ms for commit in self.commit_times):
            return "confirmed"
        return "pending" if self.commit_times else "unknown"


def run_episode(case, policy):
    episode = PaymentEpisode(case)
    deadline = INITIAL_TIMEOUT_MS + case.remaining_ms
    initial = episode.timeline.observe_initial(INITIAL_TIMEOUT_MS, deadline)
    request_id = f"payment-{case.case_id}"
    dedup = VerifiedDeduplication(request_id, frozenset({"primary", "backup"})) if case.keyed else None
    model = outcome_model(case.availability)
    chances = confirmation_chances(model)
    observation = initial.observation
    belief = PRIOR
    trace = []
    calls_left = 1
    while observation != "confirmed":
        context = dict(
            request_id=request_id,
            evidence=ExecutionEvidence.AMBIGUOUS,
            deduplication=dedup,
            remaining_ms=deadline - episode.elapsed_ms,
            remaining_calls=calls_left,
            duration_bounds_ms=DURATIONS,
        )
        allowed = admissible_actions(**context)
        if policy == "greedy":
            action, _ = choose_greedy(
                belief=belief, confirmation_probabilities=chances, call_cost=CALL_COST,
                time_cost_per_ms=TIME_COST_PER_MS, **context,
            )
        elif policy == "lookahead":
            action, _ = choose_lookahead(
                belief=belief, model=model, call_cost=CALL_COST,
                time_cost_per_ms=TIME_COST_PER_MS, **context,
            )
        elif policy == "fixed_status":
            action = "reconcile" if "reconcile" in allowed else "stop"
        elif policy == "fixed_backoff":
            target = "failover" if "failover" in allowed else "reconcile"
            action = target if target in allowed else "stop"
            if (
                not trace and action != "stop" and "wait" in allowed
                and DURATIONS["wait"] + DURATIONS[target] <= context["remaining_ms"]
            ):
                action = "wait"
        else:
            raise ValueError(f"Unknown policy: {policy}")

        if action == "stop":
            break
        if action not in admissible_actions(**context):
            raise RuntimeError(f"Policy selected inadmissible action {action}")
        before = episode.elapsed_ms
        observation = episode.execute(action)
        calls_left -= int(action != "wait")
        trace.append(dict(action=action, started_ms=before, ended_ms=episode.elapsed_ms, observation=observation))
        if observation != "confirmed" and policy in ("greedy", "lookahead"):
            posteriors = {obs: posterior for obs, _, posterior in observation_branches(belief, model[action])}
            belief = posteriors[observation]

    effects = sum(time <= deadline + AUDIT_GRACE_MS for time in episode.commit_times)
    latency = episode.elapsed_ms
    return dict(
        **asdict(case), policy=policy, fault=episode.fault, deadline_ms=deadline,
        confirmed_on_time=int(observation == "confirmed" and latency <= deadline and effects == 1),
        unresolved=int(observation != "confirmed"), duplicate_request=int(effects > 1),
        duplicate_effects=max(0, effects - 1), effects_at_audit=effects,
        retries=sum(item["action"] == "retry" for item in trace),
        failovers=sum(item["action"] == "failover" for item in trace),
        recovery_calls=1 - calls_left, latency_ms=latency, deadline_overrun=int(latency > deadline),
        final_observation=observation, trace=json.dumps(trace, separators=(",", ":")),
    )


def run_benchmark(seed, episodes_per_cell):
    samples = random.Random(seed)
    runs = []
    for remaining_ms, keyed, status, primary in product((250, 600), (False, True), (0.25, 0.9), (0.3, 0.9)):
        for _ in range(episodes_per_cell):
            case = Case(len(runs) // len(POLICIES), samples.getrandbits(64), remaining_ms, keyed, status, primary)
            runs.extend(run_episode(case, policy) for policy in POLICIES)
    return runs


def summarize(rows, condition_fields=()):
    groups = {}
    fields = ("policy", *condition_fields)
    for row in rows:
        key = tuple(row[field] for field in fields)
        groups.setdefault(key, []).append(row)
    summary = []
    for key, group in groups.items():
        latencies = sorted(row["latency_ms"] for row in group)
        count = len(group)
        summary.append(dict(
            zip(fields, key), episodes=count,
            confirmed_on_time_pct=100 * mean(row["confirmed_on_time"] for row in group),
            unresolved_pct=100 * mean(row["unresolved"] for row in group),
            duplicate_requests=sum(row["duplicate_request"] for row in group),
            duplicate_effects=sum(row["duplicate_effects"] for row in group),
            deadline_overruns=sum(row["deadline_overrun"] for row in group),
            mean_retries=mean(row["retries"] for row in group),
            mean_failovers=mean(row["failovers"] for row in group),
            mean_recovery_calls=mean(row["recovery_calls"] for row in group),
            mean_latency_ms=mean(latencies),
            p95_latency_ms=latencies[math.ceil(0.95 * count) - 1],
        ))
    return summary


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--episodes-per-cell", type=int, default=100)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    if args.episodes_per_cell < 1:
        parser.error("--episodes-per-cell must be positive")
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--out must point to a new directory")
    runs = run_benchmark(args.seed, args.episodes_per_cell)
    write_csv(args.out / "runs.csv", runs)
    write_csv(args.out / "summary.csv", summarize(runs))
    write_csv(
        args.out / "by_condition.csv",
        summarize(runs, ("remaining_ms", "keyed", "status_availability", "primary_availability")),
    )
    params = dict(
        seed=args.seed, episodes_per_cell=args.episodes_per_cell, policies=POLICIES,
        population="synthetic initial-timeout cases", initial_timeout_ms=INITIAL_TIMEOUT_MS,
        recovery_call_budget=1, audit_grace_ms=AUDIT_GRACE_MS,
        mean_residual_commit_ms=COMMIT_MEAN_MS, initial_state_order=STATES, prior=PRIOR,
        durations_ms=DURATIONS, call_cost=CALL_COST, time_cost_per_ms=TIME_COST_PER_MS,
    )
    (args.out / "config.json").write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(runs)} policy runs across {len(runs) // len(POLICIES)} matched cases to {args.out}")


if __name__ == "__main__":
    main()
