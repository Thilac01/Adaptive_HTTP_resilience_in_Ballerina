"""Compare lookahead against other policies on identical benchmark cases."""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from .benchmark import POLICIES, write_csv


CONDITIONS = ("remaining_ms", "keyed", "status_availability", "primary_availability")
METRICS = ("unresolved", "duplicate_effects", "retries", "recovery_calls", "latency_ms")


def compare(rows):
    pairs = defaultdict(dict)
    for row in rows:
        case, policy = row["case_id"], row["policy"]
        if policy not in POLICIES or policy in pairs[case]:
            raise ValueError(f"Unexpected or duplicate policy {policy} in case {case}")
        pairs[case][policy] = row
    if not pairs:
        raise ValueError("No policy runs found")
    grouped = defaultdict(list)
    for case, policies in pairs.items():
        if set(policies) != set(POLICIES):
            raise ValueError(f"Missing policies in case {case}")
        matching = ("seed", "fault", *CONDITIONS)
        if len({tuple(row[field] for field in matching) for row in policies.values()}) != 1:
            raise ValueError(f"Mismatched inputs in case {case}")
        condition = tuple(policies["lookahead"][field] for field in CONDITIONS)
        grouped[condition].append(policies)

    overall = compare_group(pairs.values())
    by_condition = [
        dict(zip(CONDITIONS, condition), **result)
        for condition, cases in grouped.items()
        for result in compare_group(cases)
    ]
    return overall, by_condition


def compare_group(cases):
    results = []
    for comparator in POLICIES:
        if comparator == "lookahead":
            continue
        deltas = [
            int(case["lookahead"]["confirmed_on_time"]) - int(case[comparator]["confirmed_on_time"])
            for case in cases
        ]
        wins, losses = deltas.count(1), deltas.count(-1)
        discordant = wins + losses
        exact_p = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / 2**discordant)
        result = dict(
            comparator=comparator, cases=len(deltas), lookahead_only=wins, comparator_only=losses,
            ties=deltas.count(0), confirmed_difference_pp=100 * mean(deltas), mcnemar_exact_p=exact_p,
        )
        for metric in METRICS:
            result[f"mean_difference_{metric}"] = mean(
                int(case["lookahead"][metric]) - int(case[comparator][metric]) for case in cases
            )
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Benchmark directory containing runs.csv")
    parser.add_argument("--out", required=True, type=Path, help="New output directory")
    args = parser.parse_args()
    try:
        with (args.run_dir / "runs.csv").open(newline="", encoding="utf-8") as source:
            overall, conditions = compare(csv.DictReader(source))
        args.out.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    write_csv(args.out / "paired_overall.csv", overall)
    write_csv(args.out / "paired_by_condition.csv", conditions)
    print(f"Wrote paired comparisons for {overall[0]['cases']} matched cases to {args.out}")


if __name__ == "__main__":
    main()
