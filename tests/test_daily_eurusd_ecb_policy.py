from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.daily_engine_contract import DailyEngineOutput
from scripts.daily_eurusd_policy import (
    POLICY_VETO_THRESHOLD,
    build_ecb_policy_context,
    parse_ecb_decision,
    policy_conflicts,
)
from scripts.daily_eurusd_spot_v17 import ENGINE_VERSION, apply_ecb_policy_gate


def _candidate(direction: str) -> DailyEngineOutput:
    score = 38.0 if direction == "SHORT" else 62.0
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp="2026-09-10T13:05:00Z",
        direction=direction,
        score=score,
        confidence=0.24,
        entry=1.1609,
        stop=1.1640 if direction == "SHORT" else 1.1578,
        target=1.1553 if direction == "SHORT" else 1.1665,
        horizon="intraday_to_27h",
        engine_version=ENGINE_VERSION,
        status="SIGNAL",
        decision_mode="WITHOUT",
        metadata={
            "candidate": {
                "direction": direction,
                "score": score,
                "confidence": 0.24,
                "accepted": True,
                "gate_reasons": [],
            },
            "data": {"provider": "test"},
        },
    ).validate()


class DailyEurusdEcbPolicyTests(unittest.TestCase):
    def test_engine_version_is_v17(self):
        self.assertEqual(ENGINE_VERSION, "eurusd-daily-spot-v1.7.0")

    def test_explicit_hike_is_parsed_as_eurusd_bullish_rate_action(self):
        parsed = parse_ecb_decision(
            "<html><body>The Governing Council decided to raise the three key ECB "
            "interest rates by 25 basis points. Deposit facility 2.50%</body></html>"
        )
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["action"], "HIKE")
        self.assertEqual(parsed["action_score"], 1.0)
        self.assertEqual(parsed["basis_points"], 25.0)

    def test_hawkish_policy_impulse_vetoes_short(self):
        context = {
            "event_active": True,
            "coverage": True,
            "must_block_entry": False,
            "status": "ECB_POLICY_COVERED",
            "policy_score": POLICY_VETO_THRESHOLD + 0.2,
        }
        output = apply_ecb_policy_gate(_candidate("SHORT"), context)
        self.assertEqual(output.direction, "FLAT")
        self.assertEqual(output.status, "NO_TRADE")
        self.assertIn("ecb_policy_conflict_veto", output.metadata["candidate"]["gate_reasons"])

    def test_dovish_policy_impulse_vetoes_long(self):
        context = {
            "event_active": True,
            "coverage": True,
            "must_block_entry": False,
            "status": "ECB_POLICY_COVERED",
            "policy_score": -(POLICY_VETO_THRESHOLD + 0.2),
        }
        output = apply_ecb_policy_gate(_candidate("LONG"), context)
        self.assertEqual(output.direction, "FLAT")
        self.assertIn("ecb_policy_conflict_veto", output.metadata["candidate"]["gate_reasons"])

    def test_neutral_no_event_leaves_signal_unchanged(self):
        context = {
            "event_active": False,
            "coverage": False,
            "must_block_entry": False,
            "status": "NO_ACTIVE_ECB_EVENT",
        }
        output = apply_ecb_policy_gate(_candidate("SHORT"), context)
        self.assertEqual(output.direction, "SHORT")
        self.assertTrue(output.metadata["candidate"]["accepted"])

    def test_missing_event_data_blocks_fresh_entry(self):
        context = {
            "event_active": True,
            "coverage": False,
            "must_block_entry": True,
            "status": "ECB_DECISION_SOURCE_UNAVAILABLE",
            "block_reason": "ecb_policy_event_data_unavailable",
        }
        output = apply_ecb_policy_gate(_candidate("SHORT"), context)
        self.assertEqual(output.direction, "FLAT")
        self.assertIn("ecb_policy_event_data_unavailable", output.metadata["candidate"]["gate_reasons"])

    def test_pre_decision_window_fails_closed_without_network_assumptions(self):
        def no_network(_: str) -> str:
            raise OSError("offline")

        context = build_ecb_policy_context(
            datetime(2026, 9, 10, 11, 30, tzinfo=timezone.utc),
            [],
            fetcher=no_network,
        )
        self.assertTrue(context["event_active"])
        self.assertFalse(context["coverage"])
        self.assertTrue(context["must_block_entry"])
        self.assertEqual(context["block_reason"], "ecb_policy_decision_pending")

    def test_policy_conflict_direction_is_symmetric(self):
        bullish = {"coverage": True, "policy_score": 0.8}
        bearish = {"coverage": True, "policy_score": -0.8}
        self.assertTrue(policy_conflicts("SHORT", bullish))
        self.assertFalse(policy_conflicts("LONG", bullish))
        self.assertTrue(policy_conflicts("LONG", bearish))
        self.assertFalse(policy_conflicts("SHORT", bearish))


if __name__ == "__main__":
    unittest.main()
