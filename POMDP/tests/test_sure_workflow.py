import unittest

from sure_research.analyze import compare
from sure_research.benchmark import run_benchmark
from sure_research.planner_api import decide


class WorkflowTests(unittest.TestCase):
    def test_pairing_has_one_result_per_case_and_comparator(self):
        runs = run_benchmark(42, 1)
        overall, by_condition = compare(runs)
        self.assertEqual(len(overall), 3)
        self.assertEqual(len(by_condition), 48)
        for result in overall:
            self.assertEqual(result["cases"], 16)
            self.assertEqual(result["lookahead_only"] + result["comparator_only"] + result["ties"], 16)
        with self.assertRaisesRegex(ValueError, "Missing policies"):
            compare(runs[1:])

    def test_planner_interface_uses_safety_and_deadline_constraints(self):
        request = {
            "version": 1, "request_id": "demo-1", "policy": "lookahead", "evidence": "ambiguous",
            "remaining_ms": 600, "remaining_calls": 1,
            "belief": {"completed": 0.0, "unstarted": 1.0, "processing": 0.0},
            "availability": {"reconcile": 1.0, "retry": 1.0, "failover": 1.0},
        }
        self.assertEqual(decide(request)["action"], "stop")
        request["verified_deduplication_endpoints"] = ["backup"]
        self.assertEqual(decide(request)["action"], "failover")
        request["remaining_ms"] = 179
        self.assertEqual(decide(request)["action"], "stop")
        request["remaining_ms"] = True
        with self.assertRaises(ValueError):
            decide(request)
