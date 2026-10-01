from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts.belief_market_data_adapter import Bar
from scripts.daily_engine_contract import DailyEngineOutput
from scripts import daily_eurusd_spot_v18 as v18

UTC=timezone.utc
NOW=datetime(2026,10,1,18,0,tzinfo=UTC)


def candidate(direction: str="SHORT") -> DailyEngineOutput:
    entry=1.1200
    risk=0.0030
    stop=entry-risk if direction=="LONG" else entry+risk
    target=entry+1.8*risk if direction=="LONG" else entry-1.8*risk
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=NOW.isoformat().replace("+00:00","Z"),
        direction=direction,
        score=30.0 if direction=="SHORT" else 70.0,
        confidence=0.50,
        entry=entry,
        stop=stop,
        target=target,
        horizon="intraday_to_27h",
        engine_version="eurusd-daily-spot-v1.7.0",
        status="SIGNAL",
        decision_mode="WITHOUT",
        metadata={
            "decision_source":"NATIVE",
            "candidate":{"direction":direction,"score":30.0 if direction=="SHORT" else 70.0,"confidence":0.50,"accepted":True,"gate_reasons":[]},
            "components":{"trend":-0.4 if direction=="SHORT" else 0.4},
            "weights":{"trend":1.0},
        },
    ).validate()


def recommendation(policy: str, level: str="LOW") -> dict:
    fractions={"SHADOW":0.0,"LOW":0.25,"MEDIUM":0.60,"FULL":1.0}
    return {
        "status":"AUTONOMOUS_POLICY_EDGE" if level!="SHADOW" else "NO_ROBUST_POLICY_EDGE",
        "recommended_policy_id":policy,
        "recommended_family":"CONTINUATION_PULLBACK" if policy.startswith("PULLBACK_") else policy,
        "authority_level":level,
        "authority_fraction":fractions[level],
        "evidence_confidence":0.45 if level=="LOW" else 0.9 if level=="FULL" else 0.1,
        "decision_influence":level!="SHADOW",
        "automatic_promotion":True,
        "automatic_rollback":level=="SHADOW",
        "expected_r":0.8,
        "edge_vs_second_r":0.5,
        "policy_stats":{},
    }


def context(reference: float=1.1200, atr: float=0.0010) -> dict:
    return {
        "observed_at":NOW.isoformat().replace("+00:00","Z"),
        "reference_mid":reference,
        "atr_30m":atr,
        "features":{"move_12h_atr":-5.0,"move_24h_atr":-6.0,"p_up_current":0.30},
        "fse":{"regime":"TURBULENT"},
    }


def bars(close: float) -> list[Bar]:
    return [Bar(NOW,close,open=close,high=close+0.00005,low=close-0.00005)]


class DailyEURUSDContextualProductionTests(unittest.TestCase):
    def test_shadow_keeps_directional_candidate_unchanged(self) -> None:
        c=candidate()
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("PULLBACK_35_ATR","SHADOW"),context())):
            out=v18._fresh_policy(c,object(),bars(1.1200),NOW)
        self.assertEqual(out.direction,"SHORT")
        self.assertEqual(out.status,"SIGNAL")
        self.assertEqual(out.metadata["contextual_entry_policy"]["status"],"SHADOW_OBSERVE")

    def test_low_authority_pullback_creates_frozen_pending_entry(self) -> None:
        c=candidate()
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("PULLBACK_35_ATR","LOW"),context())):
            out=v18._fresh_policy(c,object(),bars(1.1200),NOW)
        self.assertEqual(out.direction,"FLAT")
        self.assertEqual(out.status,"WAITING_ENTRY")
        pending=out.metadata["contextual_entry_policy"]["pending_entry"]
        self.assertEqual(pending["policy_id"],"PULLBACK_35_ATR")
        self.assertAlmostEqual(pending["trigger_mid"],1.12035,places=8)
        self.assertEqual(pending["authority_level"],"LOW")

    def test_authoritative_flat_skips_entry_without_changing_direction_model(self) -> None:
        c=candidate()
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("FLAT","MEDIUM"),context())):
            out=v18._fresh_policy(c,object(),bars(1.1200),NOW)
        self.assertEqual(out.direction,"FLAT")
        self.assertEqual(out.status,"NO_TRADE")
        self.assertFalse(out.metadata["contextual_entry_policy"]["direction_mutation_allowed"])

    def test_pending_short_triggers_only_after_pullback_level_is_reached(self) -> None:
        c=candidate()
        pending={
            "schema_version":"eurusd-contextual-pending-entry-v1",
            "policy_id":"PULLBACK_35_ATR",
            "direction":"SHORT",
            "created_at":NOW.isoformat().replace("+00:00","Z"),
            "expires_at":(NOW+timedelta(hours=24)).isoformat().replace("+00:00","Z"),
            "reference_mid":1.1200,
            "trigger_mid":1.12035,
            "pullback_depth_atr":0.35,
            "atr_30m":0.001,
            "risk_distance":0.003,
            "reward_risk":1.8,
            "entry_score":30.0,
            "entry_confidence":0.5,
            "authority_level":"LOW",
        }
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("PULLBACK_35_ATR","LOW"),context())):
            waiting=v18._resume_pending(c,pending,object(),bars(1.12020),NOW)
            triggered=v18._resume_pending(c,pending,object(),bars(1.12040),NOW)
        self.assertEqual(waiting.status,"WAITING_ENTRY")
        self.assertEqual(waiting.direction,"FLAT")
        self.assertEqual(triggered.direction,"SHORT")
        self.assertEqual(triggered.status,"SIGNAL")
        self.assertAlmostEqual(triggered.entry,1.12035,places=5)
        self.assertEqual(triggered.metadata["contextual_entry_policy"]["status"],"PULLBACK_TRIGGERED")

    def test_pending_pullback_rolls_back_when_authority_falls_to_shadow(self) -> None:
        c=candidate()
        pending={
            "schema_version":"eurusd-contextual-pending-entry-v1",
            "policy_id":"PULLBACK_35_ATR",
            "direction":"SHORT",
            "created_at":NOW.isoformat().replace("+00:00","Z"),
            "expires_at":(NOW+timedelta(hours=24)).isoformat().replace("+00:00","Z"),
            "reference_mid":1.1200,
            "trigger_mid":1.12035,
            "pullback_depth_atr":0.35,
            "atr_30m":0.001,
            "risk_distance":0.003,
            "reward_risk":1.8,
            "entry_score":30.0,
            "entry_confidence":0.5,
            "authority_level":"LOW",
        }
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("PULLBACK_35_ATR","SHADOW"),context())):
            out=v18._resume_pending(c,pending,object(),bars(1.12020),NOW)
        self.assertEqual(out.direction,"SHORT")
        self.assertEqual(out.status,"SIGNAL")
        policy=out.metadata["contextual_entry_policy"]
        self.assertEqual(policy["status"],"SHADOW_OBSERVE")
        self.assertTrue(policy["prior_pending_cancelled"])
        self.assertTrue(policy["automatic_rollback_applied"])
        self.assertEqual(policy["cancellation_reason"],"automatic_rollback_authority_lost")
        self.assertIsNone(policy["pending_entry"])

    def test_pending_pullback_rotates_to_new_authoritative_policy(self) -> None:
        c=candidate()
        pending={
            "schema_version":"eurusd-contextual-pending-entry-v1",
            "policy_id":"PULLBACK_35_ATR",
            "direction":"SHORT",
            "created_at":NOW.isoformat().replace("+00:00","Z"),
            "expires_at":(NOW+timedelta(hours=24)).isoformat().replace("+00:00","Z"),
            "reference_mid":1.1200,
            "trigger_mid":1.12035,
            "pullback_depth_atr":0.35,
            "atr_30m":0.001,
            "risk_distance":0.003,
            "reward_risk":1.8,
            "entry_score":30.0,
            "entry_confidence":0.5,
            "authority_level":"LOW",
        }
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("CONTINUATION_NOW","FULL"),context())):
            out=v18._resume_pending(c,pending,object(),bars(1.12020),NOW)
        self.assertEqual(out.direction,"SHORT")
        self.assertEqual(out.status,"SIGNAL")
        policy=out.metadata["contextual_entry_policy"]
        self.assertEqual(policy["status"],"APPLIED_NOW")
        self.assertTrue(policy["prior_pending_cancelled"])
        self.assertTrue(policy["automatic_rollback_applied"])
        self.assertEqual(policy["cancellation_reason"],"automatic_rollback_policy_changed")
        self.assertEqual(policy["cancelled_pending_policy_id"],"PULLBACK_35_ATR")

    def test_reversal_policy_fails_closed_and_cannot_flip_direction(self) -> None:
        c=candidate()
        with patch.object(v18,"_live_recommendation",return_value=(recommendation("REVERSAL_NOW","FULL"),context())):
            out=v18._fresh_policy(c,object(),bars(1.1200),NOW)
        self.assertEqual(out.direction,"FLAT")
        self.assertEqual(out.status,"NO_TRADE")
        self.assertEqual(out.metadata["contextual_entry_policy"]["status"],"NON_DIRECTIONAL_FAIL_CLOSED")


if __name__=="__main__":
    unittest.main()
