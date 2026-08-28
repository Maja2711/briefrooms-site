from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar, MarketSnapshot
from scripts.daily_engine_contract import DailyEngineOutput
from scripts import daily_eurusd_spot_v15 as v15

UTC = timezone.utc
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def snapshot(price: float = 1.1686) -> MarketSnapshot:
    rows = []
    first = NOW - timedelta(minutes=30 * 39)
    p = price - 0.002
    for i in range(40):
        p += 0.00005
        rows.append(Bar(
            timestamp=first + timedelta(minutes=30 * i),
            open=p - 0.0001,
            high=p + 0.0003,
            low=p - 0.0003,
            close=p,
            volume=1000 + i,
        ))
    return MarketSnapshot({"EURUSD=X": rows})


def flat_candidate(
    score: float = 51.39,
    *,
    components: dict[str, float] | None = None,
    ts: datetime = NOW,
) -> DailyEngineOutput:
    comps = components or {
        "trend": 0.0517,
        "broad_usd_environment": -0.0941,
        "us_rates_pressure_proxy": 0.1143,
    }
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        direction="FLAT",
        score=score,
        confidence=0.0,
        entry=None,
        stop=None,
        target=None,
        horizon="intraday_to_27h",
        engine_version="eurusd-daily-spot-v1.4.0",
        status="NO_TRADE",
        decision_mode="WITHOUT",
        metadata={
            "components": comps,
            "weights": {
                "trend": 0.546009,
                "broad_usd_environment": 0.250882,
                "us_rates_pressure_proxy": 0.203109,
            },
            "candidate": {
                "direction": "FLAT",
                "score": score,
                "confidence": 0.0,
                "accepted": False,
                "gate_reasons": ["raw_score_neutral"],
            },
        },
    ).validate()


class DailyEURUSDLearningExplorationTests(unittest.TestCase):
    def test_current_low_edge_structure_promotes_long_learning_trade(self):
        promoted = v15._promote_learning_exploration(
            flat_candidate(), snapshot(), {"trades": []}, now=NOW + timedelta(minutes=10)
        )
        self.assertEqual(promoted.direction, "LONG")
        self.assertEqual(promoted.engine_version, "eurusd-daily-spot-v1.5.0")
        self.assertEqual(promoted.metadata["decision_source"], "LOW_EDGE_LEARNING_EXPLORATION")
        self.assertTrue(promoted.metadata["learning_eligible"])
        self.assertEqual(promoted.metadata["learning_namespace"], "NATIVE_COMPONENTS_LOW_EDGE")
        self.assertEqual(promoted.metadata["exploration"]["supporting_component_count"], 2)
        self.assertLess(promoted.stop, promoted.entry)
        self.assertGreater(promoted.target, promoted.entry)

    def test_edge_below_one_point_stays_flat(self):
        promoted = v15._promote_learning_exploration(
            flat_candidate(score=50.70), snapshot(), {"trades": []}, now=NOW
        )
        self.assertEqual(promoted.direction, "FLAT")
        self.assertEqual(promoted.metadata["exploration"]["reason"], "edge_too_small")

    def test_component_conflict_stays_flat(self):
        promoted = v15._promote_learning_exploration(
            flat_candidate(
                score=52.0,
                components={
                    "trend": 0.12,
                    "broad_usd_environment": -0.20,
                    "us_rates_pressure_proxy": -0.08,
                },
            ),
            snapshot(),
            {"trades": []},
            now=NOW,
        )
        self.assertEqual(promoted.direction, "FLAT")
        self.assertEqual(promoted.metadata["exploration"]["reason"], "insufficient_component_agreement")

    def test_only_one_exploration_close_per_utc_day(self):
        history = {
            "trades": [{
                "closed_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                "decision_source": "LOW_EDGE_LEARNING_EXPLORATION",
                "r_multiple": 0.1,
            }]
        }
        promoted = v15._promote_learning_exploration(
            flat_candidate(), snapshot(), history, now=NOW
        )
        self.assertEqual(promoted.direction, "FLAT")
        self.assertEqual(promoted.metadata["exploration"]["reason"], "exploration_daily_limit")

    def test_exploration_waits_two_hours_after_latest_close(self):
        history = {
            "trades": [{
                "closed_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                "decision_source": "NATIVE",
                "r_multiple": -0.2,
            }]
        }
        promoted = v15._promote_learning_exploration(
            flat_candidate(), snapshot(), history, now=NOW
        )
        self.assertEqual(promoted.direction, "FLAT")
        self.assertEqual(promoted.metadata["exploration"]["reason"], "learning_exploration_cooldown")

    def test_position_and_closed_trade_keep_learning_provenance_and_excursions(self):
        candidate = v15._promote_learning_exploration(
            flat_candidate(), snapshot(), {"trades": []}, now=NOW
        )
        position = v15.create_position(candidate.to_dict())
        self.assertEqual(position["decision_source"], "LOW_EDGE_LEARNING_EXPLORATION")
        self.assertTrue(position["learning_eligible"])

        close_bar = Bar(
            timestamp=NOW + timedelta(hours=1),
            open=float(candidate.entry),
            high=float(candidate.target) + 0.0001,
            low=float(candidate.entry) - 0.0002,
            close=float(candidate.target),
            volume=1000,
        )
        trade = v15.evaluate_position(position, [close_bar], close_bar.timestamp)
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade["decision_source"], "LOW_EDGE_LEARNING_EXPLORATION")
        self.assertTrue(trade["learning_eligible"])
        self.assertIn("mfe_r", trade)
        self.assertIn("mae_r", trade)
        self.assertGreater(trade["mfe_r"], 0.0)
        self.assertLess(trade["mae_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
