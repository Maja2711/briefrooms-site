from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_contextual_policy_learning as learner


class ContextualPolicyLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    def bars_30m(self) -> list[Bar]:
        rows = []
        start = self.t0 - timedelta(hours=36)
        price = 1.1400
        for i in range(73):
            ts = start + timedelta(minutes=30 * i)
            close = price - 0.00006 * i
            rows.append(Bar(ts, close, open=close + 0.00002, high=close + 0.00012, low=close - 0.00012))
        return rows

    def belief_state(self) -> dict:
        return {
            "beliefs": [{
                "belief_id": "eurusd.trend.bullish",
                "probability": 0.37,
                "previous_probability": 0.44,
                "confidence": 0.72,
            }],
            "forecasts": [
                {
                    "belief_id": "eurusd.trend.bullish",
                    "predicted_probability": p,
                    "forecast_confidence": 0.7,
                    "forecast_at": (self.t0 - timedelta(minutes=20)).isoformat(),
                    "target_at": (self.t0 + timedelta(hours=h)).isoformat(),
                    "horizon_hours": h,
                    "metadata": {"consumer": "WES-ASSET-SHADOW"},
                }
                for h,p in ((3,0.41),(12,0.36),(24,0.34))
            ],
        }

    def fse_state(self) -> dict:
        return {
            "production_impact": False,
            "instruments": [{
                "instrument": "EURUSD",
                "observed_at": (self.t0 - timedelta(minutes=10)).isoformat(),
                "regime": "TURBULENT",
                "risk_score": 0.49,
                "persistence": {"mean_hurst_q2": 0.47},
                "fractal_memory": {
                    "p_up_4h": 0.43,
                    "analogues_n": 50,
                    "mean_similarity": 0.42,
                    "median_forward_return": -0.00025,
                },
            }],
        }

    def spot(self) -> dict:
        return {
            "direction": "SHORT",
            "score": 28.0,
            "confidence": 0.44,
            "metadata": {
                "decision_source": "A_TECHNICAL_FALLBACK",
                "candidate": {"direction":"SHORT","score":28.0,"confidence":0.44,"source":"A_TECHNICAL_FALLBACK"},
                "belief_macro": {"available": True, "score": -4.2, "direction": "short"},
                "event_intelligence": {
                    "decision_overlay":"HOLD",
                    "score":{"score_delta":-2.2,"confidence":0.51,"dominant_event_scope":"macro_geopolitical"},
                },
            },
        }

    def test_context_joins_move_probability_fse_and_event_features(self) -> None:
        context = learner.build_context(
            spot=self.spot(),
            rows_30m=self.bars_30m(),
            when=self.t0,
            belief_state=self.belief_state(),
            fse_public=self.fse_state(),
        )
        features=context["features"]
        self.assertLess(features["move_12h_atr"],0)
        self.assertEqual(features["p_up_current"],0.37)
        self.assertEqual(features["p_up_delta"],-0.07)
        self.assertEqual(features["p_up_24h"],0.34)
        self.assertEqual(features["fse_p_up_4h"],0.43)
        self.assertEqual(features["event_score"],-2.2)
        self.assertEqual(features["event_confidence"],0.51)
        self.assertEqual(features["belief_confidence"],0.72)
        self.assertEqual(features["belief_macro_score"],-4.2)
        self.assertFalse(context["fse"]["production_impact"])

    def test_policy_settlement_compares_now_pullback_reversal_and_flat(self) -> None:
        episode={
            "market_observed_at":self.t0.isoformat(),
            "base_direction":"SHORT",
            "reference_mid":1.13400,
            "atr_30m":0.0010,
            "risk_distance":0.00135,
            "reward_risk":1.8,
        }
        bars=[]
        # First rise enough to trigger 0.20 ATR pullback, then sustained selloff.
        closes=[1.13415,1.13425,1.13410,1.13360,1.13280,1.13190,1.13120]
        for i,close in enumerate(closes):
            bars.append(Bar(
                self.t0+timedelta(minutes=5*i),
                close,
                open=close,
                high=close+0.00010,
                low=close-0.00014,
            ))
        now = learner._settle_policy(
            episode,
            "CONTINUATION_NOW",
            {"family":"CONTINUATION_NOW"},
            bars,
        )
        pb = learner._settle_policy(
            episode,
            "PULLBACK_20_ATR",
            {"family":"CONTINUATION_PULLBACK","depth_atr":0.20},
            bars,
        )
        rev = learner._settle_policy(
            episode,
            "REVERSAL_NOW",
            {"family":"REVERSAL"},
            bars,
        )
        flat = learner._settle_policy(
            episode,
            "FLAT",
            {"family":"FLAT"},
            bars,
        )
        self.assertEqual(pb["entry_status"],"TRIGGERED")
        self.assertGreater(pb["entry_mid"],now["entry_mid"])
        self.assertTrue(math.isfinite(pb["net_r"]))
        self.assertTrue(math.isfinite(now["net_r"]))
        self.assertLess(rev["net_r"],0)
        self.assertEqual(flat["net_r"],0)

    def test_recommendation_requires_prospective_sample(self) -> None:
        state=learner._initial_state()
        context={"features":{"move_3h_atr":-1.0,"move_12h_atr":-2.0,"move_24h_atr":-2.5,"ema20_distance_atr":-1.1}}
        result=learner.recommend_policy(state,context)
        self.assertEqual(result["status"],"INSUFFICIENT_EVIDENCE")
        self.assertFalse(result["decision_influence"])

    def test_similarity_learner_can_prefer_pullback_without_hardcoded_direction_rule(self) -> None:
        state=learner._initial_state()
        episodes=[]
        for i in range(30):
            features={
                "move_3h_atr":-1.0 + (i%3)*0.02,
                "move_12h_atr":-2.0 + (i%4)*0.03,
                "move_24h_atr":-2.4 + (i%5)*0.02,
                "ema20_distance_atr":-1.0 + (i%2)*0.03,
                "p_up_current":0.34 + (i%3)*0.01,
                "p_up_24h":0.36,
            }
            outcomes={
                "CONTINUATION_NOW":{"net_r":0.18},
                "PULLBACK_20_ATR":{"net_r":0.42},
                "PULLBACK_35_ATR":{"net_r":0.78},
                "PULLBACK_50_ATR":{"net_r":0.31},
                "REVERSAL_NOW":{"net_r":-0.25},
                "FLAT":{"net_r":0.0},
            }
            episodes.append({
                "schema_version":learner.EPISODE_SCHEMA,
                "episode_id":f"e{i}",
                "status":"RESOLVED",
                "policy_change_applied":False,
                "context":{"features":features},
                "settlement":{"outcomes":outcomes},
            })
        state["episodes"]=episodes
        current={"features":{
            "move_3h_atr":-1.01,
            "move_12h_atr":-2.01,
            "move_24h_atr":-2.39,
            "ema20_distance_atr":-1.01,
            "p_up_current":0.35,
            "p_up_24h":0.36,
        }}
        result=learner.recommend_policy(state,current)
        self.assertEqual(result["status"],"AUTONOMOUS_POLICY_EDGE")
        self.assertEqual(result["recommended_policy_id"],"PULLBACK_35_ATR")
        self.assertEqual(result["recommended_family"],"CONTINUATION_PULLBACK")
        self.assertEqual(result["authority_level"],"FULL")
        self.assertTrue(result["decision_influence"])

    def test_two_highly_similar_clean_examples_can_earn_low_authority(self) -> None:
        state=learner._initial_state()
        features={
            "move_3h_atr":-2.1,
            "move_12h_atr":-5.4,
            "move_24h_atr":-6.2,
            "ema20_distance_atr":-1.7,
            "p_up_current":0.31,
            "belief_macro_score":-4.0,
        }
        outcomes={
            "CONTINUATION_NOW":{"net_r":-0.40},
            "PULLBACK_20_ATR":{"net_r":1.20},
            "PULLBACK_35_ATR":{"net_r":0.20},
            "PULLBACK_50_ATR":{"net_r":0.10},
            "REVERSAL_NOW":{"net_r":-1.0},
            "FLAT":{"net_r":0.0},
        }
        state["episodes"]=[
            {
                "schema_version":learner.EPISODE_SCHEMA,
                "episode_id":f"clean-{i}",
                "status":"RESOLVED",
                "base_direction":"SHORT",
                "policy_change_applied":False,
                "context":{"features":features,"fse":{"regime":"TURBULENT"}},
                "settlement":{"outcomes":outcomes},
            }
            for i in range(2)
        ]
        current={"features":features,"daily":{"direction":"SHORT"},"fse":{"regime":"TURBULENT"}}
        result=learner.recommend_policy(state,current)
        self.assertEqual(result["recommended_policy_id"],"PULLBACK_20_ATR")
        self.assertEqual(result["authority_level"],"LOW")
        self.assertTrue(result["decision_influence"])

    def test_many_noisy_examples_do_not_force_promotion(self) -> None:
        state=learner._initial_state()
        outcomes={
            "CONTINUATION_NOW":{"net_r":0.10},
            "PULLBACK_20_ATR":{"net_r":0.10},
            "PULLBACK_35_ATR":{"net_r":0.10},
            "PULLBACK_50_ATR":{"net_r":0.10},
            "REVERSAL_NOW":{"net_r":0.10},
            "FLAT":{"net_r":0.0},
        }
        state["episodes"]=[
            {
                "schema_version":learner.EPISODE_SCHEMA,
                "episode_id":f"noisy-{i}",
                "status":"RESOLVED",
                "base_direction":"SHORT",
                "policy_change_applied":False,
                "context":{"features":{
                    "move_3h_atr":-1.0+(i%5)*0.01,
                    "move_12h_atr":-2.0+(i%7)*0.01,
                    "move_24h_atr":-3.0+(i%9)*0.01,
                    "ema20_distance_atr":-1.0+(i%3)*0.01,
                }},
                "settlement":{"outcomes":outcomes},
            }
            for i in range(50)
        ]
        current={"features":{"move_3h_atr":-1.0,"move_12h_atr":-2.0,"move_24h_atr":-3.0,"ema20_distance_atr":-1.0},"daily":{"direction":"SHORT"}}
        result=learner.recommend_policy(state,current)
        self.assertEqual(result["authority_level"],"SHADOW")
        self.assertFalse(result["decision_influence"])

    def test_reversal_remains_research_only_even_when_counterfactual_is_best(self) -> None:
        state=learner._initial_state()
        outcomes={
            "CONTINUATION_NOW":{"net_r":0.40},
            "PULLBACK_20_ATR":{"net_r":0.30},
            "PULLBACK_35_ATR":{"net_r":0.20},
            "PULLBACK_50_ATR":{"net_r":0.10},
            "REVERSAL_NOW":{"net_r":2.0},
            "FLAT":{"net_r":0.0},
        }
        state["episodes"]=[
            {
                "schema_version":learner.EPISODE_SCHEMA,
                "episode_id":f"rev-{i}",
                "status":"RESOLVED",
                "base_direction":"SHORT",
                "policy_change_applied":False,
                "context":{"features":{"move_3h_atr":-1.0,"move_12h_atr":-2.0,"move_24h_atr":-3.0,"ema20_distance_atr":-1.0}},
                "settlement":{"outcomes":outcomes},
            }
            for i in range(8)
        ]
        current={"features":{"move_3h_atr":-1.0,"move_12h_atr":-2.0,"move_24h_atr":-3.0,"ema20_distance_atr":-1.0},"daily":{"direction":"SHORT"}}
        result=learner.recommend_policy(state,current)
        self.assertEqual(result["research_best_policy_id"],"REVERSAL_NOW")
        self.assertNotEqual(result["recommended_policy_id"],"REVERSAL_NOW")
        self.assertTrue(result["research_reversal_is_non_authoritative"])

    def test_v1_state_migrates_without_backfill_or_direction_authority(self) -> None:
        legacy=learner._initial_state()
        legacy["schema_version"]=learner.LEGACY_SCHEMA_VERSION
        migrated=learner.normalize_state(legacy)
        self.assertEqual(migrated["schema_version"],learner.SCHEMA_VERSION)
        self.assertTrue(migrated["authority"]["entry_timing_decision_influence"])
        self.assertFalse(migrated["authority"]["direction_decision_influence"])
        self.assertFalse(migrated["authority"]["historical_backfill"])

    def test_state_validation_enforces_bounded_timing_authority(self) -> None:
        state=learner._initial_state()
        learner.validate_state(state)
        broken=learner._initial_state()
        broken["authority"]["direction_decision_influence"]=True
        with self.assertRaises(ValueError):
            learner.validate_state(broken)


if __name__=="__main__":
    unittest.main()
