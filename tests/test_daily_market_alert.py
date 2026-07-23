from __future__ import annotations

import unittest

from scripts.update_daily_market_alert import (
    INSTRUMENTS,
    material_reasons,
    normalize_probabilities,
    validate_payload,
)


def instrument(instrument_id: str, price: float, probabilities=None, drivers=None):
    return {
        "id": instrument_id,
        "price_value": price,
        "change_numeric": 0.0,
        "support_value": price * 0.98,
        "resistance_value": price * 1.02,
        "driver_keys": drivers or ["baseline"],
        "scenario_probabilities": probabilities or {"range": 45, "continuation": 35, "reversal": 20},
        "reason": {"pl": "Konkretny powód ruchu.", "en": "A concrete reason for the move."},
    }


class DailyMarketAlertTests(unittest.TestCase):
    def test_probability_normalization_is_governed(self):
        result = normalize_probabilities({"range": 47, "continuation": 34, "reversal": 19})
        self.assertEqual(sum(result.values()), 100)
        self.assertTrue(all(value % 5 == 0 for value in result.values()))
        self.assertTrue(all(10 <= value <= 70 for value in result.values()))

    def test_material_price_thresholds(self):
        opening = {
            "instruments": [
                instrument("sp500", 7500.0),
                instrument("brent", 98.0),
                instrument("us10y", 4.60),
            ]
        }
        candidate = {
            "instruments": [
                instrument("sp500", 7460.0),
                instrument("brent", 99.2),
                instrument("us10y", 4.66),
            ]
        }
        reasons = material_reasons(opening, candidate)
        self.assertIn("sp500:price", reasons)
        self.assertIn("brent:price", reasons)
        self.assertIn("us10y:price", reasons)

    def test_new_driver_is_material(self):
        opening = {"instruments": [instrument("sp500", 7500.0, drivers=["earnings"])]}
        candidate = {"instruments": [instrument("sp500", 7500.0, drivers=["earnings", "fed-path"])]}
        self.assertIn("sp500:driver", material_reasons(opening, candidate))

    def test_payload_requires_three_instruments_and_100_percent(self):
        payload = {
            "schema_version": "2.0",
            "instruments": [
                instrument("sp500", 7500.0),
                instrument("brent", 98.0),
                instrument("us10y", 4.60),
            ],
        }
        validate_payload(payload)
        payload["instruments"][0]["scenario_probabilities"] = {
            "range": 40,
            "continuation": 30,
            "reversal": 20,
        }
        with self.assertRaises(ValueError):
            validate_payload(payload)

    def test_instrument_contract_is_stable(self):
        self.assertEqual(set(INSTRUMENTS), {"sp500", "brent", "us10y"})


if __name__ == "__main__":
    unittest.main()
