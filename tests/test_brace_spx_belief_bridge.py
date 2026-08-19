import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_belief_bridge as bridge


BELIEF_IDS = bridge.SPX_BELIEF_IDS


def active_brace(updated_at="2026-08-20T21:40:00Z", exposures=None):
    exposures = exposures or [0.75] * 8
    return {
        "generation_id": "spx-orthogonal-core-v6",
        "candidate_signature": "sig-123",
        "updated_at": updated_at,
        "latest_market_date": "2026-08-20",
        "status": "shadow_active_no_orders",
        "observations_collected": 70,
        "warmup_required": 70,
        "holdout_accessed": False,
        "live_orders": False,
        "autonomous_trading": False,
        "single_champion_selected": False,
        "family_scores": {"price_trend": 0.4},
        "latest_regime": "risk_on",
        "candidate_snapshots": [
            {"candidate_id": f"c{i}", "target_exposure_next_session": value}
            for i, value in enumerate(exposures)
        ],
    }


def warmup_brace(updated_at="2026-08-20T21:40:00Z"):
    row = active_brace(updated_at)
    row["status"] = "warming_up"
    row["observations_collected"] = 14
    row["candidate_snapshots"] = []
    return row


def belief_state(forecast_at="2026-08-20T20:00:00Z", target_at="2026-08-21T20:00:00Z", probability=0.70, confidence=0.80, set_id="set-1", missing=None):
    rows = []
    for belief_id in BELIEF_IDS:
        if belief_id == missing:
            continue
        rows.append({
            "forecast_id": "f-" + belief_id,
            "forecast_set_id": set_id,
            "belief_id": belief_id,
            "predicted_probability": probability,
            "forecast_confidence": confidence,
            "forecast_at": forecast_at,
            "target_at": target_at,
            "horizon_hours": 24,
            "domain": belief_id.split(".")[1],
            "entity": "SPX",
            "regime": "risk_on",
            "representative_evidence_ids": ["e1"],
            "evidence_snapshot": [{"evidence_id": "e1"}],
        })
    return {"forecasts": rows}


class BridgeUnitTests(unittest.TestCase):
    def test_warmup_brace_has_no_opinion(self):
        state = bridge.brace_specialist_state(warmup_brace())
        self.assertFalse(state["available"])
        self.assertEqual(state["stance"], "unavailable")
        self.assertEqual(state["reason"], "brace_spx_warmup_no_opinion")

    def test_active_brace_consensus_is_risk_on(self):
        state = bridge.brace_specialist_state(active_brace())
        self.assertTrue(state["available"])
        self.assertEqual(state["stance"], "risk_on")
        self.assertEqual(state["candidate_consensus"]["candidate_count"], 8)
        self.assertFalse(state["source"]["single_champion_selected"])

    def test_governance_failure_fails_closed(self):
        raw = active_brace()
        raw["holdout_accessed"] = True
        state = bridge.brace_specialist_state(raw)
        self.assertFalse(state["available"])
        self.assertEqual(state["reason"], "brace_spx_governance_guard_failed")

    def test_selects_latest_complete_point_in_time_belief_set(self):
        old = belief_state("2026-08-20T16:00:00Z", set_id="old", probability=0.45)["forecasts"]
        new = belief_state("2026-08-20T20:00:00Z", set_id="new", probability=0.72)["forecasts"]
        state = {"forecasts": old + new}
        selected = bridge.select_frozen_belief_state(state, datetime(2026, 8, 20, 21, 40, tzinfo=timezone.utc))
        self.assertTrue(selected["available"])
        self.assertEqual(selected["forecast_set_id"], "new")
        self.assertEqual(selected["stance"], "risk_on")
        self.assertAlmostEqual(selected["risk_on_probability_mean"], 0.72)
        self.assertEqual(len(selected["beliefs"]), 5)

    def test_future_belief_set_is_excluded(self):
        state = belief_state("2026-08-20T22:00:00Z", target_at="2026-08-21T22:00:00Z")
        selected = bridge.select_frozen_belief_state(state, datetime(2026, 8, 20, 21, 40, tzinfo=timezone.utc))
        self.assertFalse(selected["available"])
        self.assertEqual(selected["reason"], "no_complete_point_in_time_spx_belief_set")

    def test_incomplete_belief_set_is_unavailable(self):
        state = belief_state(missing=BELIEF_IDS[-1])
        selected = bridge.select_frozen_belief_state(state, datetime(2026, 8, 20, 21, 40, tzinfo=timezone.utc))
        self.assertFalse(selected["available"])

    def test_relationship_agreement_and_conflict(self):
        brace_state = {"available": True, "stance": "risk_on", "confidence": 0.8}
        belief_on = {"available": True, "stance": "risk_on", "confidence": 0.9}
        belief_off = {"available": True, "stance": "defensive", "confidence": 0.9}
        self.assertEqual(bridge.relationship(brace_state, belief_on)["class"], "STRONG_AGREEMENT")
        self.assertEqual(bridge.relationship(brace_state, belief_off)["class"], "STRONG_CONFLICT")

    def test_first_run_activates_without_historical_capture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            belief = root / "belief.json"
            brace = root / "brace.json"
            belief.write_text(json.dumps(belief_state()))
            brace.write_text(json.dumps(active_brace("2026-08-20T20:00:00Z")))
            result = bridge.run_bridge(root / "out", belief, brace, datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc))
            self.assertEqual(result["status"], "activated_waiting_for_prospective_brace_state")
            self.assertEqual(result["records_total"], 0)

    def test_prospective_capture_then_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            belief = root / "belief.json"
            brace = root / "brace.json"
            out = root / "out"
            belief.write_text(json.dumps(belief_state(forecast_at="2026-08-20T20:00:00Z")))
            brace.write_text(json.dumps(active_brace("2026-08-20T20:00:00Z")))
            bridge.run_bridge(out, belief, brace, datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc))
            brace.write_text(json.dumps(active_brace("2026-08-21T21:40:00Z")))
            belief.write_text(json.dumps(belief_state(forecast_at="2026-08-21T20:00:00Z", target_at="2026-08-22T20:00:00Z", set_id="set-2")))
            first = bridge.run_bridge(out, belief, brace, datetime(2026, 8, 21, 21, 45, tzinfo=timezone.utc))
            second = bridge.run_bridge(out, belief, brace, datetime(2026, 8, 21, 21, 50, tzinfo=timezone.utc))
            self.assertEqual(first["status"], "captured")
            self.assertEqual(first["records_total"], 1)
            self.assertEqual(second["status"], "retry_idempotent_no_new_record")
            self.assertEqual(second["records_total"], 1)
            row = json.loads((out / "engine_belief_observations.jsonl").read_text().strip())
            self.assertTrue(row["engine_belief_calibration_eligible"])
            self.assertFalse(row["decision_influence"])
            self.assertFalse(row["with_without_evaluation_enabled"])

    def test_later_belief_update_cannot_rewrite_same_brace_record(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            belief = root / "belief.json"
            brace = root / "brace.json"
            out = root / "out"
            belief.write_text(json.dumps(belief_state()))
            brace.write_text(json.dumps(active_brace("2026-08-20T20:00:00Z")))
            bridge.run_bridge(out, belief, brace, datetime(2026, 8, 20, 21, 0, tzinfo=timezone.utc))
            brace.write_text(json.dumps(active_brace("2026-08-21T21:40:00Z")))
            belief.write_text(json.dumps(belief_state("2026-08-21T20:00:00Z", "2026-08-22T20:00:00Z", 0.70, set_id="frozen")))
            bridge.run_bridge(out, belief, brace, datetime(2026, 8, 21, 21, 45, tzinfo=timezone.utc))
            original = (out / "engine_belief_observations.jsonl").read_text()
            belief.write_text(json.dumps(belief_state("2026-08-21T21:30:00Z", "2026-08-22T21:30:00Z", 0.20, set_id="later")))
            bridge.run_bridge(out, belief, brace, datetime(2026, 8, 21, 22, 0, tzinfo=timezone.utc))
            self.assertEqual((out / "engine_belief_observations.jsonl").read_text(), original)

    def test_hard_controls_are_all_false(self):
        self.assertTrue(bridge.controls())
        self.assertTrue(all(value is False for value in bridge.controls().values()))


if __name__ == "__main__":
    unittest.main()
