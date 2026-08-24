import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.epistemic_consumer_interface import EpistemicConsumerInterface, build_consumer_bundle, SPX_BELIEF_IDS


def sample_state():
    states = {}
    for i, belief_id in enumerate(SPX_BELIEF_IDS):
        states[belief_id] = {
            "state_id": f"state-{i}",
            "topic": belief_id,
            "probability": 0.62 + i * 0.01,
            "confidence": 0.70,
            "delta_probability": 0.02,
            "contradiction": 0.10,
            "freshness": 0.90,
            "audit_status": "clean",
            "member_belief_ids": [belief_id],
            "dominant_support_evidence_ids": [f"e{i}"],
            "dominant_opposition_evidence_ids": [],
            "drilldown_required": False,
            "drilldown_reasons": [],
        }
    return {
        "contract_version": "belief-epistemic-state-v1",
        "created_at": "2026-08-24T08:00:00Z",
        "authority": {"llm_may_ignore_aggregate": False, "llm_may_override_probability": False},
        "controls": {"belief_core_writeback_enabled": False},
        "states": states,
    }


class EpistemicConsumerInterfaceTests(unittest.TestCase):
    def test_brace_and_wes_receive_same_authoritative_projection(self):
        interface = EpistemicConsumerInterface(sample_state(), {})
        brace = interface.envelope("BRACE_SPX")
        wes = interface.envelope("WES_SPX")
        self.assertTrue(brace.available)
        self.assertEqual(brace.aggregate_probability, wes.aggregate_probability)
        self.assertEqual(brace.stance, "risk_on")
        self.assertFalse(brace.authority.consumer_may_override_probability)
        self.assertFalse(brace.authority.belief_core_writeback_enabled)
        self.assertEqual(brace.source_created_at, "2026-08-24T08:00:00Z")

    def test_missing_state_fails_closed(self):
        payload = sample_state()
        payload["states"].pop(SPX_BELIEF_IDS[-1])
        env = EpistemicConsumerInterface(payload, {}).envelope("BRACE_SPX")
        self.assertFalse(env.available)
        self.assertEqual(env.reason, "required_epistemic_states_missing")

    def test_point_in_time_projection_rejects_future_state(self):
        interface = EpistemicConsumerInterface(sample_state(), {})
        engine_at = datetime(2026, 8, 24, 7, 59, tzinfo=timezone.utc)
        row = interface.point_in_time_projection("BRACE_SPX", engine_at)
        self.assertFalse(row["available"])
        self.assertEqual(row["reason"], "epistemic_state_created_after_engine_state")

    def test_point_in_time_projection_accepts_prior_fresh_state(self):
        interface = EpistemicConsumerInterface(sample_state(), {})
        engine_at = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
        row = interface.point_in_time_projection("WES_SPX", engine_at)
        self.assertTrue(row["available"])
        self.assertTrue(row["aggregate_authoritative"])
        self.assertFalse(row["consumer_may_override"])

    def test_drilldown_is_bounded_and_cannot_override(self):
        interface = EpistemicConsumerInterface(sample_state(), {SPX_BELIEF_IDS[0]: {"path": "state->belief->evidence->source"}})
        request = interface.drilldown_request("WES_SPX", SPX_BELIEF_IDS[0], depth=4)
        self.assertEqual(request["max_depth"], 4)
        self.assertEqual(request["max_reinspection_cycles"], 1)
        self.assertFalse(request["consumer_may_override"])
        self.assertTrue(request["recalculation_required_for_state_change"])
        with self.assertRaises(ValueError):
            interface.drilldown_request("WES_SPX", SPX_BELIEF_IDS[0], depth=5)

    def test_bundle_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "epistemic_state.json").write_text(json.dumps(sample_state()), encoding="utf-8")
            (root / "epistemic_drilldown_index.json").write_text("{}", encoding="utf-8")
            bundle = build_consumer_bundle(root)
            self.assertFalse(bundle["authority"]["decision_writeback_enabled"])
            self.assertFalse(bundle["authority"]["automatic_tuning_enabled"])
            self.assertTrue((root / "epistemic_consumer_bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
