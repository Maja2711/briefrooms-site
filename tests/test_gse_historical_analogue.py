from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gse_historical_analogue as v2

UTC = timezone.utc


def points(start: str, closes: list[float]) -> list[v2.DailyClose]:
    base = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return [v2.DailyClose(base + timedelta(days=i), close) for i, close in enumerate(closes)]


def baseline_forecast(*, forecast_at="2026-08-19T12:00:00Z", asset="SPX", horizon=24, direction=-1):
    return {
        "forecast_id": "f1",
        "batch_id": "b1",
        "asset": asset,
        "symbol": "SPY" if asset == "SPX" else "^TNX",
        "forecast_at": forecast_at,
        "target_at": "2026-08-20T12:00:00Z",
        "horizon_hours": horizon,
        "direction": direction,
        "predicted_probability": 0.70,
        "scenario_snapshot": [
            {
                "scenario_type": "middle_east_energy_escalation",
                "probability": 0.60,
                "confidence": 0.70,
            }
        ],
    }


class HistoricalAnalogueTests(unittest.TestCase):
    def test_future_completed_response_is_excluded_from_past_forecast(self):
        library = {
            "responses": [
                {
                    "event_id": "old",
                    "market_anchor_key": "2020-01-01|SPX|24",
                    "event_at": "2020-01-01T00:00:00Z",
                    "response_complete_at": "2020-01-03T00:00:00Z",
                    "scenario_type": "middle_east_energy_escalation",
                    "asset": "SPX",
                    "horizon_hours": 24,
                    "transmission_weight": -0.45,
                    "raw_return": -0.02,
                    "aligned_return": 0.02,
                    "directional_success": True,
                },
                {
                    "event_id": "future",
                    "market_anchor_key": "2025-01-01|SPX|24",
                    "event_at": "2025-01-01T00:00:00Z",
                    "response_complete_at": "2025-01-03T00:00:00Z",
                    "scenario_type": "middle_east_energy_escalation",
                    "asset": "SPX",
                    "horizon_hours": 24,
                    "transmission_weight": -0.45,
                    "raw_return": -0.03,
                    "aligned_return": 0.03,
                    "directional_success": True,
                },
            ]
        }
        rows = v2.eligible_analogue_rows(
            library,
            scenario_type="middle_east_energy_escalation",
            asset="SPX",
            horizon_hours=24,
            forecast_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        self.assertEqual([x["event_id"] for x in rows], ["old"])

    def test_same_market_date_is_one_effective_episode(self):
        library = {
            "responses": [
                {
                    "event_id": "a",
                    "market_anchor_key": "2022-02-24|SPX|24",
                    "event_at": "2022-02-24T00:00:00Z",
                    "response_complete_at": "2022-02-26T00:00:00Z",
                    "scenario_type": "russia_ukraine_black_sea_escalation",
                    "asset": "SPX",
                    "horizon_hours": 24,
                    "transmission_weight": -0.30,
                    "raw_return": -0.01,
                    "aligned_return": 0.01,
                    "directional_success": True,
                },
                {
                    "event_id": "b",
                    "market_anchor_key": "2022-02-24|SPX|24",
                    "event_at": "2022-02-24T00:00:00Z",
                    "response_complete_at": "2022-02-26T00:00:00Z",
                    "scenario_type": "russia_ukraine_black_sea_escalation",
                    "asset": "SPX",
                    "horizon_hours": 24,
                    "transmission_weight": -0.40,
                    "raw_return": -0.01,
                    "aligned_return": 0.01,
                    "directional_success": True,
                },
            ]
        }
        rows = v2.eligible_analogue_rows(
            library,
            scenario_type="russia_ukraine_black_sea_escalation",
            asset="SPX",
            horizon_hours=24,
            forecast_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "b")

    def test_event_response_uses_close_before_event_not_future_baseline(self):
        catalog = {
            "events": [
                {
                    "event_id": "e1",
                    "event_at": "2024-01-03T00:00:00Z",
                    "label": "event",
                    "scenario_types": ["middle_east_energy_escalation"],
                    "source": "x",
                    "source_ref": "x",
                }
            ]
        }
        history = {
            "SPX": points("2024-01-01T00:00:00Z", [100, 101, 90, 80, 79]),
        }
        rows = v2.event_response_rows(catalog, history)
        spx24 = next(x for x in rows if x["asset"] == "SPX" and x["horizon_hours"] == 24)
        self.assertEqual(spx24["baseline_value"], 101.0)
        self.assertEqual(spx24["target_value"], 80.0)
        self.assertTrue(spx24["directional_success"])

    def test_candidate_overlay_is_bounded_and_never_modifies_v1(self):
        library = {
            "catalog_version": "x",
            "catalog_sha256": "abc",
            "responses": [
                {
                    "event_id": "a",
                    "market_anchor_key": "2024-01-01|SPX|24",
                    "event_at": "2024-01-01T00:00:00Z",
                    "response_complete_at": "2024-01-03T00:00:00Z",
                    "scenario_type": "middle_east_energy_escalation",
                    "asset": "SPX",
                    "horizon_hours": 24,
                    "transmission_weight": -0.45,
                    "raw_return": -0.02,
                    "aligned_return": 0.02,
                    "directional_success": True,
                }
            ],
        }
        base = baseline_forecast()
        original = json.loads(json.dumps(base))
        candidate = v2.candidate_from_baseline(base, library)
        self.assertIsNotNone(candidate)
        self.assertLessEqual(candidate["overlay_weight"], 0.20)
        self.assertFalse(candidate["v1_forecast_modified"])
        self.assertFalse(candidate["decision_influence"])
        self.assertEqual(base, original)

    def test_candidate_converts_analogue_probability_to_baseline_direction(self):
        library = {
            "catalog_version": "x",
            "catalog_sha256": "abc",
            "responses": [
                {
                    "event_id": "a",
                    "market_anchor_key": "2024-01-01|US10Y|24",
                    "event_at": "2024-01-01T00:00:00Z",
                    "response_complete_at": "2024-01-03T00:00:00Z",
                    "scenario_type": "china_taiwan_trade_escalation",
                    "asset": "US10Y",
                    "horizon_hours": 24,
                    "transmission_weight": -0.20,
                    "raw_return": -0.02,
                    "aligned_return": 0.02,
                    "directional_success": True,
                }
            ],
        }
        base = {
            **baseline_forecast(asset="US10Y", direction=1),
            "scenario_snapshot": [
                {
                    "scenario_type": "china_taiwan_trade_escalation",
                    "probability": 0.60,
                    "confidence": 0.70,
                }
            ],
        }
        candidate = v2.candidate_from_baseline(base, library)
        self.assertIsNotNone(candidate)
        # The analogue supports LOWER yields while the baseline direction is UP,
        # so its probability must be inverted before blending.
        self.assertLess(candidate["historical_analogue_probability"], 0.5)
        self.assertLess(candidate["v2_candidate_probability"], candidate["baseline_v1_probability"])

    def test_verification_produces_paired_v1_v2_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = {
                "candidate_id": "c1",
                "baseline_forecast_id": "f1",
                "asset": "SPX",
                "horizon_hours": 24,
                "baseline_v1_probability": 0.70,
                "v2_candidate_probability": 0.65,
                "effective_analogue_n": 2,
            }
            (root / "gse_v2_forecasts.jsonl").write_text(json.dumps(candidate) + "\n")
            baseline_v = {
                "verification_id": "v1",
                "forecast_id": "f1",
                "outcome": False,
                "verified_at": "2026-08-20T12:00:00Z",
            }
            (root / "gse_verifications.jsonl").write_text(json.dumps(baseline_v) + "\n")
            self.assertEqual(v2.verify_candidates(root, datetime(2026, 8, 20, 13, tzinfo=UTC)), 1)
            report = v2.build_calibration(root)
            self.assertEqual(report["overall"]["paired_n"], 1)
            self.assertLess(report["overall"]["delta_brier_v2_minus_v1"], 0)
            self.assertFalse(report["controls"]["automatic_tuning_enabled"])


if __name__ == "__main__":
    unittest.main()
