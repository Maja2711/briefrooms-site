import unittest
from datetime import datetime, timedelta, timezone

from scripts import stock_trading_v2_reentry_learning as learning
from scripts import stock_trading_portfolio as portfolio


class ReentryProductionLearningTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)
        self.stop = {
            "position_id": "gpw:first:ASB.WA",
            "symbol": "ASB.WA",
            "closed_at": "2026-10-14T12:30:00+00:00",
            "exit_reason": "stop_loss",
            "exit_price": 184.90,
        }

    def test_post_stop_features_never_use_pre_stop_or_incomplete_bars(self):
        base = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        timestamps = [int((base + timedelta(minutes=i)).timestamp()) for i in (0, 30, 45, 60, 75)]
        chart = {"timestamp": timestamps, "indicators": {"quote": [{"low": [100.0, 177.0, 180.0, 180.3, 150.0]}]}}
        # Pre-stop 100 and partly formed 150 cannot contaminate the minimum.
        out = learning.quote_features(
            "GPW", "ASB.WA", self.stop, 184.3,
            "2026-10-14T13:12:00+00:00", .02, 1.2, chart=chart,
        )
        self.assertIsNotNone(out)
        self.assertAlmostEqual(out["rebound_atr"], (184.3 - 180.0) / (184.3 * .02), places=5)

    def test_no_features_without_post_stop_complete_candle(self):
        chart = {"timestamp": [int(self.now.timestamp())], "indicators": {"quote": [{"low": [100.0]}]}}
        self.assertIsNone(learning.quote_features(
            "GPW", "ASB.WA", self.stop, 184.3,
            "2026-10-14T12:40:00+00:00", .02, 1.0, chart=chart))

    def test_one_frozen_observation_per_sl_episode(self):
        state = learning.empty_state()
        action = {"market": "GPW", "action": "reentry_blocked",
                  "reentry_probe": {"episode_id": "GPW:one", "symbol": "ASB.WA",
                                    "observed_at": self.now.isoformat(), "price": 184.3,
                                    "risk_fraction": .02,
                                    "features": {"drop_atr": 2.0, "rebound_atr": .5, "volume_ratio": 1.4}}}
        self.assertEqual(1, learning.capture(state, [action, action]))
        self.assertEqual(0, learning.capture(state, [action]))
        self.assertEqual(1, len(state["observations"]))
        self.assertEqual("BLOCK", state["observations"][0]["decision"])
        self.assertEqual("RESEARCH_CLOSE_ONLY_NO_EXECUTION", state["observations"][0]["outcome_type"])

    def test_settlement_uses_only_five_completed_future_sessions(self):
        state = learning.empty_state()
        state["observations"].append({
            "episode_id": "GPW:one", "market": "GPW", "symbol": "ASB.WA",
            "observed_at": "2026-10-14T11:00:00+00:00", "price": 100.0,
            "risk_fraction": .02, "features": {}, "outcome": None})
        obs = state["observations"][0]
        days = [datetime(2026, 10, d, 14, tzinfo=timezone.utc) for d in (14, 15, 16, 19, 20, 21, 22)]
        chart = {"timestamp": [int(d.timestamp()) for d in days],
                 "indicators": {"quote": [{"close": [200., 101., 102., 103., 104., 105., 106.]}]}}
        before = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
        self.assertFalse(learning.resolve_one(obs, chart, before))
        self.assertTrue(learning.resolve_one(obs, chart, datetime(2026, 10, 23, 10, tzinfo=timezone.utc)))
        self.assertEqual("2026-10-21", obs["outcome"]["end_day"])
        self.assertEqual(105.0, obs["outcome"]["close"])
        self.assertFalse(obs["outcome"]["execution_proof"])

    def test_learning_rule_not_enabled_by_empty_config_or_fake_sample(self):
        features = {"drop_atr": 2.1, "rebound_atr": .8, "volume_ratio": 1.5}
        inactive = [{"id": "test", "status": "ACTIVE",
                     "conditions": {"drop_atr_min": 1.5, "rebound_atr_min": .5, "volume_ratio_min": 1.2},
                     "activated_at": self.now.isoformat(), "validation": {"n": 2, "unique_symbols": 1,
                                                                           "conservative_lower_bound_r": 10}}]
        self.assertIsNone(learning.active_exception(inactive, features, now=self.now))
        self.assertIsNone(learning.active_exception([], features, now=self.now))
        inactive[0]["validation"] = {"n": 12, "unique_symbols": 5, "conservative_lower_bound_r": .15}
        self.assertIsNotNone(learning.active_exception(inactive, features, now=self.now))

    def test_guard_rejects_repeat_asb_without_material_change(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        state["markets"]["GPW"]["closed_positions"] = [self.stop]
        selection = {"symbol": "ASB.WA",
                     "market_snapshot": {"observed_at": "2026-10-14T13:15:00+00:00", "last": 184.3},
                     "risk_percent": .02}
        verdict = portfolio.reentry_after_stop_loss(state, "GPW", selection, now=self.now, policy=policy)
        self.assertFalse(verdict["allowed"])
        self.assertEqual("reentry_material_change_required", verdict["reason"])

    def test_production_rule_requires_new_observations_after_freeze(self):
        state = learning.empty_state()
        policy = portfolio.load_policy()

        def example(day, positive, prefix):
            return {
                "episode_id": f"{prefix}:{day}", "market": "GPW",
                "symbol": f"STOCK{day % 7}.WA",
                "observed_at": day.isoformat(),
                "price": 100.0, "risk_fraction": .02,
                "features": {
                    "drop_atr": 2.5 if positive else .1,
                    "rebound_atr": .8 if positive else .05,
                    "volume_ratio": 1.5 if positive else .7,
                },
                "outcome": {"net_research_r": 1.0 if positive else -.5},
            }

        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        state["observations"] = [
            example(start + timedelta(days=i), i < 15, "training") for i in range(25)
        ]
        frozen_at = self.now
        initial = learning.update_rules(state, policy, frozen_at)
        self.assertEqual("RULE_FROZEN_AWAITING_FUTURE_COHORT", initial["GPW"]["phase"])
        self.assertEqual([], policy["markets"]["GPW"]["reentry_guard"]["learned_exception_rules"])
        # Reusing the discovery cohort cannot promote the rule.
        second = learning.update_rules(state, policy, frozen_at + timedelta(days=1))
        self.assertEqual("FRESH_PRODUCTION_COHORT", second["GPW"]["phase"])
        self.assertEqual([], policy["markets"]["GPW"]["reentry_guard"]["learned_exception_rules"])

        future = frozen_at + timedelta(days=1)
        state["observations"].extend([
            example(future + timedelta(days=i), i < 12, "future") for i in range(24)
        ])
        third = learning.update_rules(state, policy, frozen_at + timedelta(days=30))
        self.assertEqual("ACTIVATED", third["GPW"]["phase"])
        active = policy["markets"]["GPW"]["reentry_guard"]["learned_exception_rules"][0]
        self.assertEqual("ACTIVE", active["status"])
        self.assertGreaterEqual(active["validation"]["n"], 12)
        self.assertIsNotNone(learning.active_exception(
            [active], {"drop_atr": 2.5, "rebound_atr": .8, "volume_ratio": 1.5},
            now=frozen_at + timedelta(days=30),
        ))

    def test_pending_rule_requires_future_independent_cohort(self):
        state = learning.empty_state()
        policy = portfolio.load_policy()
        # A present-tense high score cannot activate a rule with no settled observations.
        report = learning.update_rules(state, policy, self.now)
        self.assertEqual("COLLECTING", report["GPW"]["phase"])
        self.assertEqual([], policy["markets"]["GPW"]["reentry_guard"]["learned_exception_rules"])


if __name__ == "__main__":
    unittest.main()
