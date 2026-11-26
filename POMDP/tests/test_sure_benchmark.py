import json
import unittest
from collections import defaultdict

from sure_research.benchmark import POLICIES, run_benchmark, summarize


class BenchmarkTests(unittest.TestCase):
    def test_runs_are_reproducible_and_paired(self):
        runs = run_benchmark(seed=42, episodes_per_cell=1)
        self.assertEqual(runs, run_benchmark(seed=42, episodes_per_cell=1))
        paired = defaultdict(list)
        for row in runs:
            paired[row["case_id"]].append(row)
        self.assertEqual(len(paired), 16)
        for group in paired.values():
            self.assertEqual(tuple(row["policy"] for row in group), POLICIES)
            self.assertEqual(len({(row["seed"], row["fault"]) for row in group}), 1)
        self.assertEqual([row["episodes"] for row in summarize(runs)], [16] * len(POLICIES))

    def test_realized_actions_respect_safety_and_budgets(self):
        for row in run_benchmark(seed=91, episodes_per_cell=3):
            trace = json.loads(row["trace"])
            self.assertLessEqual(row["recovery_calls"], 1)
            self.assertLessEqual(row["latency_ms"], row["deadline_ms"])
            self.assertEqual(row["duplicate_effects"], 0)
            if not row["keyed"]:
                self.assertTrue(all(item["action"] in ("wait", "reconcile") for item in trace))
            if row["confirmed_on_time"]:
                self.assertEqual(row["effects_at_audit"], 1)
