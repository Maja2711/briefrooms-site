import unittest

from scripts.briefrooms_harness_core import plan


class HarnessTests(unittest.TestCase):
    def test_upstream_failure_blocks_daily_not_wes(self):
        rows = plan({"belief": "FAILED", "epistemic": "HEALTHY",
                     "daily_eurusd": "HEALTHY", "wes": "HEALTHY"})["nodes"]
        self.assertEqual(rows["belief"]["intent"], "INVESTIGATE")
        self.assertEqual(rows["daily_eurusd"]["effective"], "BLOCKED")
        self.assertEqual(rows["wes"]["effective"], "HEALTHY")

    def test_missing_evidence(self):
        result = plan({})
        self.assertEqual(result["nodes"]["belief"]["effective"], "UNKNOWN")
        self.assertFalse(result["dispatch_enabled"])

    def test_invalid_graph(self):
        for graph in ({"a": ("b",)}, {"a": ("a",)}):
            with self.assertRaises(ValueError):
                plan({}, graph)

    def test_healthy_chain(self):
        rows = plan({"belief": "HEALTHY", "epistemic": "HEALTHY",
                     "daily_eurusd": "HEALTHY"})["nodes"]
        self.assertEqual(rows["daily_eurusd"]["intent"], "OBSERVE")

    def test_malformed_status(self):
        self.assertEqual(plan({"belief": []})["nodes"]["belief"]["observed"], "UNKNOWN")
