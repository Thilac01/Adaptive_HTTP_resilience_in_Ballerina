import unittest

from sure_research.admission import (
    ExecutionEvidence,
    VerifiedDeduplication,
    admissible_actions,
)


class ActionAdmissionTests(unittest.TestCase):
    def actions(
        self,
        *,
        evidence=ExecutionEvidence.AMBIGUOUS,
        deduplication=None,
        remaining_ms=600,
        remaining_calls=1,
    ):
        return admissible_actions(
            request_id="payment-1",
            evidence=evidence,
            deduplication=deduplication,
            remaining_ms=remaining_ms,
            remaining_calls=remaining_calls,
            duration_bounds_ms={"wait": 200, "reconcile": 120, "retry": 150, "failover": 180},
        )

    def test_ambiguous_payment_cannot_be_repeated(self):
        self.assertEqual(self.actions(), ("stop", "wait", "reconcile"))

    def test_deduplication_is_scoped_to_destination_and_request(self):
        primary = VerifiedDeduplication("payment-1", frozenset({"primary"}))
        backup = VerifiedDeduplication("payment-1", frozenset({"backup"}))
        wrong_request = VerifiedDeduplication("payment-2", frozenset({"primary", "backup"}))
        self.assertEqual(self.actions(deduplication=primary), ("stop", "wait", "reconcile", "retry"))
        self.assertEqual(self.actions(deduplication=backup), ("stop", "wait", "reconcile", "failover"))
        self.assertEqual(self.actions(deduplication=wrong_request), ("stop", "wait", "reconcile"))

    def test_authoritative_no_commit_evidence_still_obeys_deadline(self):
        evidence = ExecutionEvidence.NO_PRIOR_ATTEMPT_CAN_COMMIT
        self.assertEqual(self.actions(evidence=evidence, remaining_ms=150), ("stop", "reconcile", "retry"))
        self.assertEqual(self.actions(evidence=evidence), ("stop", "wait", "reconcile", "retry", "failover"))

    def test_expired_deadline_and_exhausted_calls(self):
        self.assertEqual(self.actions(remaining_ms=0), ("stop",))
        self.assertEqual(self.actions(remaining_calls=0), ("stop", "wait"))

    def test_confirmed_payment_is_terminal(self):
        keyed = VerifiedDeduplication("payment-1", frozenset({"primary", "backup"}))
        self.assertEqual(self.actions(evidence=ExecutionEvidence.CONFIRMED, deduplication=keyed), ("stop",))
