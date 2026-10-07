import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/shadow-engines-observatory.yml"


class ShadowEnginesObservatoryWorkflowTests(unittest.TestCase):
    def test_observatory_is_status_only_not_wes_executor(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("WES live execution safety hook", text)
        self.assertNotIn("update_weekly_live_prices_fast.py", text)
        self.assertNotIn("investments_weekly_v5.py --mode ensure-exposure", text)
        self.assertNotIn("audit_intraday_risk_exits.py --persist-report", text)
        self.assertNotIn("Execute WES live ENTRY SL TP", text)

    def test_current_fse_workflow_names_trigger_observatory(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('"FSE Fractal Structure Engine Shadow"', text)
        self.assertIn('"FSE Phase & Cross-Scale Shadow"', text)
        self.assertNotIn('"FSE v2 Deep Fractal Memory Shadow"', text)

    def test_observatory_still_builds_and_publishes_read_only_snapshot(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("build_shadow_engines_public.py", text)
        self.assertIn("shadow_engines_public.json", text)
        self.assertIn("tests.test_shadow_engines_public", text)


if __name__ == "__main__":
    unittest.main()
