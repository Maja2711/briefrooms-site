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

    def test_btc_result_units_are_percent(self):
        item = {
            "instrument_id": "btcusd",
            "direction": "short",
            "entry_price": 100.0,
            "entry_captured_at": "2026-08-03T10:00:00+02:00",
            "exit_price": 95.0,
            "exit_captured_at": "2026-08-07T22:00:00+02:00",
            "exit_reason": "scheduled_week_close",
            "trade_status": "closed",
            "notional_usd": 10000,
            "result_value": 500.0,
            "result_units": 5.0,
            "result_percent": 5.0,
        }
        self.assertEqual(audit.item_violations(item), [])

    def test_reconstructed_btc_result_units_preserve_price_move(self):
        item = {
            "instrument_id": "btcusd",
            "direction": "short",
            "entry_price": 65197.8984375,
            "entry_captured_at": "2026-07-27T10:00:00+02:00",
            "exit_price": 62891.37109375,
            "exit_captured_at": "2026-07-31T22:00:00+02:00",
            "exit_reason": "scheduled_week_close",
            "trade_status": "closed",
            "notional_usd": 10000,
            "result_value": 353.773266,
            "result_units": 2306.52734375,
            "result_percent": 3.53773266,
        }
        self.assertEqual(
            audit.item_violations(item, "5.0.1-reconstructed"),
            [],
        )

    def test_invalid_short_risk_order_is_rejected(self):
        item = self.valid_short()
        item["risk_plan"]["stop_loss_price"] = 1.15000
        codes = {row["error"] for row in audit.item_violations(item)}
        self.assertIn("invalid_short_risk_order", codes)


if __name__ == "__main__":
    unittest.main()
