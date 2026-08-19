from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "investments_wes_counterfactual.py"
spec = importlib.util.spec_from_file_location("wes_counterfactual_deadline", MODULE_PATH)
cf = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cf)


class CounterfactualDeadlineTests(unittest.TestCase):
    def test_deadline_bar_uses_open_not_post_deadline_high_low(self):
        contract = {
            "entry_captured_at": "2026-08-24T08:00:00+00:00",
            "scheduled_exit": "2026-08-28T20:00:00+00:00",
            "entry_price": 100.0,
            "direction": "long",
            "round_trip_cost_percent": 0.0,
            "risk_plan": {
                "stop_loss_price": 90.0,
                "take_profit_price": 120.0,
                "same_bar_rule": "stop_loss_first_conservative",
            },
            "contract_sha256": "test",
        }
        frame = pd.DataFrame(
            {
                "Open": [100.0, 106.0],
                "High": [105.0, 125.0],
                "Low": [95.0, 85.0],
                "Close": [102.0, 110.0],
            },
            index=pd.to_datetime([
                "2026-08-24T08:05:00Z",
                "2026-08-28T20:00:00Z",
            ], utc=True),
        )
        result = cf.replay_from_bars(
            contract,
            frame,
            evaluated_at=datetime(2026, 8, 28, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["exit_reason"], "scheduled_week_close")
        self.assertEqual(result["exit_price"], 106.0)
        self.assertEqual(result["execution_rule"], "first_5m_bar_at_or_after_frozen_deadline")


if __name__ == "__main__":
    unittest.main()
