from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "stock-trading-v2-production.yml"


class StockTradingV2WorkflowDecouplingTests(unittest.TestCase):
    def test_stock_champion_does_not_execute_wes_as_prerequisite(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: Stock Trading v2 Production Champion", text)
        self.assertNotIn("WES live execution hook", text)
        self.assertNotIn("scripts/update_weekly_live_prices_fast.py", text)
        self.assertNotIn("scripts/investments_weekly_v5.py", text)
        self.assertNotIn("multi_instrument_exposure_report_v5.json", text)
        self.assertNotIn("multi_instrument_exposure_state_v5.json", text)

    def test_stock_champion_keeps_its_own_execution_airlock(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("NO_RETROACTIVE_EXECUTION=1", text)
        self.assertIn("scripts/verify_no_retroactive_execution.py", text)
        self.assertIn("Revalidate and admit v2 Champion opportunities", text)
        self.assertIn("Persist canonical v2 portfolio state through NO RETROACTIVE airlock", text)


if __name__ == "__main__":
    unittest.main()
