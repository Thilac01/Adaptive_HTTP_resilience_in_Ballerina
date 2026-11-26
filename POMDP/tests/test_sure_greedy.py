import unittest

from sure_research.admission import ExecutionEvidence
from sure_research.greedy import choose_greedy


class GreedyTests(unittest.TestCase):
    def choose(self, *, remaining_ms=600, remaining_calls=1, call_cost=0.05):
        return choose_greedy(
            belief=(12 / 44, 20 / 44, 12 / 44),
            confirmation_probabilities={"reconcile": (0, 0.25, 0.9), "retry": (1, 1, 1)},
            request_id="payment-1",
            evidence=ExecutionEvidence.AMBIGUOUS,
            deduplication=None,
            remaining_ms=remaining_ms,
            remaining_calls=remaining_calls,
            duration_bounds_ms={"wait": 200, "reconcile": 120, "retry": 100},
            call_cost=call_cost,
            time_cost_per_ms=0.001,
        )

    def test_hand_calculation_and_exclusion_of_unsafe_retry(self):
        action, score = self.choose()
        self.assertEqual(action, "reconcile")
        self.assertAlmostEqual(score, 15.8 / 44 - 0.05 - 0.12)

    def test_budget_can_exclude_reconciliation(self):
        self.assertEqual(self.choose(remaining_ms=119), ("stop", 0.0))
        self.assertEqual(self.choose(remaining_calls=0), ("stop", 0.0))

    def test_stop_when_costs_exceed_immediate_expected_reward(self):
        self.assertEqual(self.choose(call_cost=0.5), ("stop", 0.0))
