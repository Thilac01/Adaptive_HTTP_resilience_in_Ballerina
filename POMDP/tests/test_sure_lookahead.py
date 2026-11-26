import unittest

from sure_research.admission import ExecutionEvidence
from sure_research.greedy import choose_greedy
from sure_research.lookahead import (
    Outcome,
    choose_lookahead,
    confirmation_chances,
    observation_branches,
)


MODEL = {
    "wait": (
        (Outcome("unstarted", "waited", 1.0),),
        (Outcome("processing", "waited", 3 / 13), Outcome("completed", "waited", 10 / 13)),
        (Outcome("completed", "waited", 1.0),),
    ),
    "reconcile": (
        (Outcome("unstarted", "unknown", 1.0),),
        (Outcome("processing", "pending", 0.75), Outcome("completed", "confirmed", 0.25)),
        (Outcome("completed", "unknown", 0.1), Outcome("completed", "confirmed", 0.9)),
    ),
}


class LookaheadTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(
            belief=(12 / 44, 20 / 44, 12 / 44),
            request_id="payment-1",
            evidence=ExecutionEvidence.AMBIGUOUS,
            deduplication=None,
            remaining_ms=600,
            remaining_calls=1,
            duration_bounds_ms={"wait": 200, "reconcile": 120},
            call_cost=0.05,
            time_cost_per_ms=0.001,
        )

    def test_same_model_greedy_queries_and_lookahead_waits(self):
        greedy_action, greedy_score = choose_greedy(
            confirmation_probabilities=confirmation_chances(MODEL), **self.context
        )
        planned_action, planned_score = choose_lookahead(model=MODEL, **self.context)
        self.assertEqual(greedy_action, "reconcile")
        self.assertAlmostEqual(greedy_score, 15.8 / 44 - 0.17)
        self.assertEqual(planned_action, "wait")
        self.assertAlmostEqual(planned_score, 25.8 / 44 - 0.37)

    def test_wait_then_query_must_fit_deadline(self):
        self.context["remaining_ms"] = 250
        self.assertEqual(choose_lookahead(model=MODEL, **self.context)[0], "reconcile")

    def test_posterior_depends_on_which_observation_is_seen(self):
        branches = {
            observation: (probability, posterior)
            for observation, probability, posterior in observation_branches(
                self.context["belief"], MODEL["reconcile"]
            )
        }
        self.assertEqual(branches["pending"][1], (0.0, 1.0, 0.0))
        self.assertEqual(branches["confirmed"][1], (0.0, 0.0, 1.0))
        self.assertAlmostEqual(branches["unknown"][1][2], 1.2 / 13.2)
        self.assertAlmostEqual(sum(probability for probability, _ in branches.values()), 1.0)

    def test_ambiguous_repeat_cannot_reuse_no_commit_evidence(self):
        self.context.update(
            belief=(1.0, 0.0, 0.0),
            evidence=ExecutionEvidence.NO_PRIOR_ATTEMPT_CAN_COMMIT,
            remaining_calls=2,
            duration_bounds_ms={"retry": 100},
            call_cost=0.0,
            time_cost_per_ms=0.0,
        )
        retry_outcomes = (Outcome("completed", "confirmed", 0.3), Outcome("unstarted", "timeout", 0.7))
        self.assertEqual(
            choose_lookahead(model={"retry": (retry_outcomes,) * 3}, **self.context),
            ("retry", 0.3),
        )
