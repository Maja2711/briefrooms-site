from __future__ import annotations

import unittest
from dataclasses import dataclass

from scripts import daily_market_alert_materiality as materiality


@dataclass
class Snapshot:
    instrument_id: str
    price: float
    atr: float
    support: float
    resistance: float
    next_support: float
    next_resistance: float
    support_text: str = ""
    resistance_text: str = ""
    next_support_text: str = ""
    next_resistance_text: str = ""


class DailyMarketAlertMaterialityTests(unittest.TestCase):
    def snapshots(self):
        return [
            Snapshot("sp500", 7485.42, 55.0, 7470.0, 7520.0, 7420.0, 7550.0),
            Snapshot("brent", 90.01, 2.1, 89.5, 91.5, 86.5, 93.0),
            Snapshot("us10y", 4.743, 0.09, 4.71, 4.75, 4.69, 4.81),
        ]

    def test_promotes_us10y_levels_beyond_two_basis_point_noise(self):
        snapshots = self.snapshots()
        materiality.apply_materiality_levels(snapshots)
        rates = snapshots[2]
        self.assertGreaterEqual(rates.price - rates.support, 0.05)
        self.assertGreaterEqual(rates.resistance - rates.price, 0.05)
        self.assertGreaterEqual(rates.support - rates.next_support, 0.08)
        self.assertGreaterEqual(rates.next_resistance - rates.resistance, 0.08)
        self.assertNotEqual(rates.next_support_text, "4,69%")

    def test_all_assets_use_asset_specific_materiality_floors(self):
        snapshots = self.snapshots()
        materiality.apply_materiality_levels(snapshots)
        for snapshot in snapshots:
            row = snapshot.materiality
            self.assertGreaterEqual(snapshot.price - snapshot.support, row["minimum_trigger_distance"] - 1e-8)
            self.assertGreaterEqual(snapshot.resistance - snapshot.price, row["minimum_trigger_distance"] - 1e-8)
            self.assertGreaterEqual(snapshot.support - snapshot.next_support, row["minimum_target_extension"] - 1e-8)
            self.assertGreaterEqual(snapshot.next_resistance - snapshot.resistance, row["minimum_target_extension"] - 1e-8)

    def test_editorial_explicitly_labels_in_range_moves_as_noise(self):
        snapshots = self.snapshots()
        materiality.apply_materiality_levels(snapshots)
        editorial = {
            "instruments": [
                {
                    "id": snapshot.instrument_id,
                    "narrative": {
                        "pl": {"what_changed": "x", "why_it_matters": "y", "base_case": "z"},
                        "en": {"what_changed": "x", "why_it_matters": "y", "base_case": "z"},
                    },
                }
                for snapshot in snapshots
            ]
        }
        materiality.rewrite_editorial(editorial, snapshots)
        for item in editorial["instruments"]:
            pl = item["narrative"]["pl"]["base_case"].lower()
            en = item["narrative"]["en"]["base_case"].lower()
            self.assertTrue(any(marker in pl for marker in ("szum", "nie uzasadniają prognozy", "nie sygnał")))
            self.assertTrue(any(marker in en for marker in ("noise", "do not justify", "not a directional")))

    def test_public_payload_gate_blocks_trivial_target(self):
        snapshots = self.snapshots()
        materiality.apply_materiality_levels(snapshots)
        payload = {
            "instruments": [],
        }
        for snapshot in snapshots:
            payload["instruments"].append({
                "id": snapshot.instrument_id,
                "price_value": snapshot.price,
                "support_value": snapshot.support,
                "resistance_value": snapshot.resistance,
                "next_support_value": snapshot.next_support,
                "next_resistance_value": snapshot.next_resistance,
                "narrative": {
                    "pl": {"base_case": materiality._base_case(snapshot.instrument_id, snapshot, "pl")},
                    "en": {"base_case": materiality._base_case(snapshot.instrument_id, snapshot, "en")},
                },
                "scenario_probabilities": {"range": 50, "continuation": 30, "reversal": 20},
            })
        materiality.enrich_payload(payload, snapshots)
        materiality.validate_payload(payload)
        payload["instruments"][2]["next_support_value"] = payload["instruments"][2]["support_value"] - 0.02
        with self.assertRaisesRegex(materiality.MaterialityError, "Trivial"):
            materiality.validate_payload(payload)


if __name__ == "__main__":
    unittest.main()
