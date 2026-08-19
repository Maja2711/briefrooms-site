from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import investments_wes_belief_bridge as bridge


def wes_record(
    decision_id="wes-spx-new",
    decision_at="2026-08-20T09:05:00Z",
    first_captured_at="2026-08-20T09:10:00Z",
    direction="long",
    raw_score=80.0,
    decision_type="entered_position",
):
    return {
        "decision_id": decision_id,
        "week_id": "2026-W34",
        "instrument_id": "sp500_futures",
        "decision_type": decision_type,
        "decision_at": decision_at,
        "first_captured_at": first_captured_at,
        "wes_actual": {
            "direction": direction,
            "strategy_id": "weekly_trend",
            "raw_score": raw_score,
            "entry_class": "monday_weekly",
            "entry_price": 7800.0,
            "entry_captured_at": decision_at,
            "risk_plan": {"stop_loss_price": 7700.0, "take_profit_price": 8000.0},
        },
    }


def belief_state(forecast_at="2026-08-20T08:30:00Z", target_at="2026-08-20T20:00:00Z", p=0.75, confidence=0.8):
    ids = [
        "spx.trend.bullish",
        "spx.breadth.healthy",
        "spx.volatility.benign",
        "spx.liquidity.supportive",
        "spx.financial_conditions.supportive",
    ]
    return {
        "forecasts": [
            {
                "forecast_id": f"f-{i}",
                "forecast_set_id": "set-1",
                "belief_id": belief_id,
                "predicted_probability": p,
                "forecast_confidence": confidence,
                "forecast_at": forecast_at,
                "target_at": target_at,
                "horizon_hours": 12,
                "domain": "markets",
                "entity": "SPX",
                "regime": "risk_on",
                "representative_evidence_ids": [],
                "evidence_snapshot": [],
            }
            for i, belief_id in enumerate(ids)
        ]
    }


class WESBeliefBridgeTests(unittest.TestCase):
    def test_controls_are_hard_off(self):
        self.assertTrue(bridge.controls())
        self.assertTrue(all(value is False for value in bridge.controls().values()))

    def test_relationship_agreement_and_conflict(self):
        belief = {"available": True, "stance": "risk_on", "confidence": 0.8}
        long = {"available": True, "actionable": True, "direction": "long", "score_strength": 0.9}
        short = {"available": True, "actionable": True, "direction": "short", "score_strength": 0.9}
        self.assertEqual(bridge.relationship(long, belief)["class"], "STRONG_AGREEMENT")
        self.assertEqual(bridge.relationship(short, belief)["class"], "STRONG_CONFLICT")

    def test_non_actionable_is_not_calibration_eligible(self):
        belief = {"available": True, "stance": "risk_on", "confidence": 0.8}
        wes = {"available": True, "actionable": False, "direction": "long", "score_strength": 0.9}
        rel = bridge.relationship(wes, belief)
        self.assertEqual(rel["class"], "UNAVAILABLE")
        self.assertFalse(rel["alpha_eligible"])

    def test_capture_window_missed_is_never_reconstructed(self):
        row = wes_record(first_captured_at="2026-08-20T09:00:00Z")
        status = bridge._capture_status(row, datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc))
        self.assertFalse(status["eligible"])
        self.assertEqual(status["status"], "missed_not_reconstructed")

    def test_first_run_only_activates_without_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            belief = root / "belief.json"
            source.write_text(json.dumps({"records": [wes_record()]}))
            belief.write_text(json.dumps(belief_state()))
            out = bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 9, 20, tzinfo=timezone.utc))
            self.assertEqual(out["status"], "activated_waiting_for_prospective_wes_decision")
            self.assertEqual(out["records_total"], 0)

    def test_prospective_capture_is_point_in_time_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            belief = root / "belief.json"
            source.write_text(json.dumps({"records": []}))
            belief.write_text(json.dumps(belief_state()))
            bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc))
            source.write_text(json.dumps({"records": [wes_record()]}))
            first = bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 9, 20, tzinfo=timezone.utc))
            second = bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))
            self.assertEqual(first["written"], 1)
            self.assertEqual(first["engine_belief_calibration_eligible"], 1)
            self.assertEqual(second["written"], 0)
            rows = (root / "bridge" / "engine_belief_observations.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            record = json.loads(rows[0])
            self.assertEqual(record["relationship"]["class"], "STRONG_AGREEMENT")
            self.assertFalse(record["decision_influence"])

    def test_future_belief_snapshot_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            belief = root / "belief.json"
            source.write_text(json.dumps({"records": []}))
            belief.write_text(json.dumps(belief_state(forecast_at="2026-08-20T10:00:00Z")))
            bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc))
            source.write_text(json.dumps({"records": [wes_record()]}))
            out = bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 9, 20, tzinfo=timezone.utc))
            self.assertEqual(out["written"], 1)
            self.assertEqual(out["engine_belief_calibration_eligible"], 0)
            record = json.loads((root / "bridge" / "engine_belief_observations.jsonl").read_text())
            self.assertEqual(record["relationship"]["class"], "UNAVAILABLE")

    def test_old_wes_record_after_activation_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            belief = root / "belief.json"
            source.write_text(json.dumps({"records": []}))
            belief.write_text(json.dumps(belief_state()))
            bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
            old = wes_record(decision_at="2026-08-20T09:05:00Z", first_captured_at="2026-08-20T09:10:00Z")
            source.write_text(json.dumps({"records": [old]}))
            out = bridge.run_bridge(root / "bridge", source, belief, datetime(2026, 8, 20, 12, 5, tzinfo=timezone.utc))
            self.assertEqual(out["records_total"], 0)

    def test_score_strength_caps_relationship_strength(self):
        belief = {"available": True, "stance": "risk_on", "confidence": 0.95}
        wes = {"available": True, "actionable": True, "direction": "long", "score_strength": 0.4}
        rel = bridge.relationship(wes, belief)
        self.assertEqual(rel["class"], "WEAK_AGREEMENT")
        self.assertAlmostEqual(rel["strength"], 0.4)


if __name__ == "__main__":
    unittest.main()
