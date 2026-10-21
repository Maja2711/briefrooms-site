"""Prevent presentation regressions from stopping immutable FSE collection."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FSERepairWorkflowTests(unittest.TestCase):
    def test_asd_collection_depends_only_on_backend_evidence_validation(self):
        text=(ROOT/".github/workflows/fse-autonomous-structure-discovery.yml").read_text(encoding="utf-8")
        backend=text.split("\n  test:",1)[1].split("\n  frontend:",1)[0]
        self.assertIn("tests.test_fse_autonomous_discovery",backend)
        self.assertNotIn("tests.test_decision_lab_fse_frontend",backend)
        self.assertNotIn("test_fse_discovery_match_visibility.js",backend)
        self.assertIn("test_fse_discovery_match_visibility.js",text.split("\n  frontend:",1)[1])
        collector=text.split("\n  discover:",1)[1]
        self.assertIn("needs: test",collector)
        self.assertNotIn("needs: [test, frontend]",collector)
        self.assertIn("FSE_ASD_FAIL_CLOSED",collector)

    def test_edge_deploy_checks_actual_source_and_bounded_requests(self):
        text=(ROOT/".github/workflows/deploy-fse-fast-edge.yml").read_text(encoding="utf-8")
        self.assertIn("--connect-timeout 5 --max-time 35",text)
        self.assertIn('j.transport_version!=="FSE-FAST-2.0"',text)
        self.assertIn("source_lag_minutes",text)
        self.assertIn("source_reference_at",text)
        self.assertIn("FSE_FAST_EDGE_SOURCE_STALE",text)


if __name__ == "__main__":
    unittest.main()
