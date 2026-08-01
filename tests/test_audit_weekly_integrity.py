import unittest

from scripts import audit_weekly_model as audit


class WeeklyIntegrityTests(unittest.TestCase):
    def valid_short(self):
        return {
            "instrument_id": "eurusd",
            "direction": "short",
            "entry_price": 1.15380,
            "entry_captured_at": "2026-07-31T20:10:00+02:00",
            "exit_price": 1.16090,
            "exit_captured_at": "2026-07-31T21:00:00+02:00",
            "exit_reason": "stop_loss",
            "trade_status": "closed",
            "notional_eur": 10000,
            "risk_plan": {
                "stop_loss_price": 1.16090,
                "take_profit_price": 1.14220,
            },
            "result_value": -71.0,
            "result_units": -71.0,
            "result_percent": -0.615376,
        }

    def test_valid_short_passes(self):
        self.assertEqual(audit.item_violations(self.valid_short()), [])

    def test_exit_before_entry_is_rejected(self):
        item = self.valid_short()
        item["exit_captured_at"] = "2026-07-30T09:00:00+02:00"
        codes = {row["error"] for row in audit.item_violations(item)}
        self.assertIn("exit_before_entry", codes)

    def test_result_mismatch_is_rejected(self):
        item = self.valid_short()
        item["result_units"] = 78.4
        item["result_value"] = 78.4
        codes = {row["error"] for row in audit.item_violations(item)}
        self.assertIn("result_units_mismatch", codes)
        self.assertIn("result_value_mismatch", codes)

    def test_invalid_short_risk_order_is_rejected(self):
        item = self.valid_short()
        item["risk_plan"]["stop_loss_price"] = 1.15000
        codes = {row["error"] for row in audit.item_violations(item)}
        self.assertIn("invalid_short_risk_order", codes)


if __name__ == "__main__":
    unittest.main()
