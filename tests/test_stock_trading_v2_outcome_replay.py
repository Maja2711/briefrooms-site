from __future__ import annotations

import unittest

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_discovery as discovery
from scripts import stock_trading_v2_outcome_replay as replay


def event(*, selected: bool = False, skip_above: float = 105.0):
    return contracts.make_experience_event(
        market="US",
        symbol="TEST",
        decision_at="2026-09-10T15:00:00-04:00",
        session_date="2026-09-10",
        selected=selected,
        source_engine="test",
        source_schema_version="test-v1",
        source_policy_version="test-policy",
        source_payload_sha256="abc",
        candidate_state={
            "risk_plan": {
                "reference_price": 100.0,
                "entry_zone": [99.0, 102.0],
                "skip_above": skip_above,
                "stop": 95.0,
                "target": 110.0,
                "risk_percent": 0.05,
                "reward_risk": 2.0,
            },
            "decision_path": {"first_blocking_gate": "score"},
        },
        recorded_at="2026-09-10T19:00:01Z",
    )


def bar(day: str, open_: float, high: float, low: float, close: float):
    return discovery.Bar(day=day, open=open_, high=high, low=low, close=close, volume=100000)


class StockTradingV2OutcomeReplayTests(unittest.TestCase):
    def test_target_hit_settles_before_full_horizon(self):
        rows = [
            bar("2026-09-11", 100, 104, 98, 103),
            bar("2026-09-12", 103, 111, 102, 109),
        ]
        result = replay.replay_horizon(
            event(),
            rows,
            horizon_sessions=5,
            cost_stress_percent=0.1,
            replay_version="test",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "SETTLED")
        self.assertEqual(result["exit_reason"], "TARGET")
        self.assertEqual(result["sessions_held"], 2)

    def test_same_bar_stop_and_target_is_stop_first(self):
        rows = [bar("2026-09-11", 100, 112, 94, 101)]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=1, cost_stress_percent=0.0, replay_version="test"
        )
        assert result is not None
        self.assertEqual(result["exit_reason"], "STOP_FIRST")
        self.assertLess(result["net_r"], 0)

    def test_unresolved_horizon_returns_none(self):
        rows = [bar("2026-09-11", 100, 104, 98, 103)]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=5, cost_stress_percent=0.0, replay_version="test"
        )
        self.assertIsNone(result)

    def test_skip_above_prevents_counterfactual_activation(self):
        rows = [bar("2026-09-11", 106, 108, 104, 107)]
        result = replay.replay_horizon(
            event(skip_above=105), rows, horizon_sessions=1, cost_stress_percent=0.0, replay_version="test"
        )
        assert result is not None
        self.assertEqual(result["status"], "NOT_ACTIVATED")
        self.assertEqual(result["reason"], "next_session_open_above_frozen_skip")

    def test_gap_stop_uses_worse_open_after_activation(self):
        rows = [
            bar("2026-09-11", 100, 104, 98, 103),
            bar("2026-09-12", 90, 92, 88, 91),
        ]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=2, cost_stress_percent=0.0, replay_version="test"
        )
        assert result is not None
        self.assertEqual(result["exit_reason"], "GAP_STOP")
        self.assertEqual(result["exit_price"], 90.0)

    def test_outcome_hash_detects_tampering(self):
        rows = [bar("2026-09-11", 100, 103, 98, 102)]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=1, cost_stress_percent=0.1, replay_version="test"
        )
        assert result is not None
        payload = replay.make_outcome(event(), result, replay_version="test", settled_at="2026-09-12T00:00:00Z")
        replay.validate_outcome(payload)
        payload["replay"]["net_r"] = 99
        with self.assertRaises(Exception):
            replay.validate_outcome(payload)


if __name__ == "__main__":
    unittest.main()
