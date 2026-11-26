import unittest

from sure_research.simulator import ClientView, SCENARIOS


class PaymentTimelineTests(unittest.TestCase):
    def test_same_timeout_hides_three_different_states(self):
        expected = {
            "request_dropped": "unstarted",
            "slow_commit": "processing",
            "response_lost": "completed",
        }
        for name, state in expected.items():
            with self.subTest(scenario=name):
                payment = SCENARIOS[name]
                self.assertEqual(payment.state_at(400), state)
                self.assertEqual(payment.observe_initial(400, 1000), ClientView("timeout", 600))

    def test_client_timeout_does_not_cancel_server_commit(self):
        payment = SCENARIOS["slow_commit"]
        self.assertEqual(payment.state_at(549), "processing")
        self.assertEqual(payment.state_at(550), "completed")

    def test_normal_reply_leaves_remaining_budget(self):
        self.assertEqual(SCENARIOS["normal"].observe_initial(400, 1000), ClientView("confirmed", 730))

    def test_deadline_caps_initial_wait(self):
        self.assertEqual(SCENARIOS["normal"].observe_initial(400, 200), ClientView("timeout", 0))
