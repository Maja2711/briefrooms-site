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
    def test_eligible_low_edge_structure_remains_flat_in_production(self):
        out = v15._promote_learning_exploration(
            flat_candidate(), snapshot(), {"trades": []}, now=NOW + timedelta(minutes=10)
        )
        self.assertEqual(out.direction, "FLAT")
        self.assertEqual(out.status, "NO_TRADE")
        self.assertIsNone(out.entry)
        self.assertFalse(out.metadata["exploration"]["production_authority"])
        self.assertTrue(out.metadata["exploration"]["eligible_shadow"])
        self.assertEqual(out.metadata["exploration"]["supporting_component_count"], 2)
        self.assertEqual(out.metadata["exploration"]["risk_budget_multiplier"], 0.0)

    def test_edge_below_one_point_stays_flat(self):
        out = v15._promote_learning_exploration(
            flat_candidate(score=50.70), snapshot(), {"trades": []}, now=NOW
        )
        self.assertEqual(out.direction, "FLAT")
        self.assertEqual(out.metadata["exploration"]["reason"], "edge_too_small")

    def test_component_conflict_stays_flat(self):
        out = v15._promote_learning_exploration(
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
        self.assertEqual(out.direction, "FLAT")
        self.assertEqual(out.metadata["exploration"]["reason"], "insufficient_component_agreement")

    def test_exploration_history_never_creates_position(self):
        out = v15._promote_learning_exploration(
            flat_candidate(), snapshot(),
            {"trades": [{
                "closed_at": (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
                "decision_source": "NATIVE",
                "r_multiple": 0.2,
            }]},
            now=NOW,
        )
        self.assertEqual(out.direction, "FLAT")
        with self.assertRaises(ValueError):
            v15.create_position(out.to_dict())

    def test_native_direction_passes_through_unchanged(self):
        native = DailyEngineOutput(
            instrument="EUR/USD",
            timestamp=NOW.isoformat().replace("+00:00", "Z"),
            direction="SHORT",
            score=35.0,
            confidence=0.30,
            entry=1.1686,
            stop=1.1718,
            target=1.1628,
            horizon="intraday_to_27h",
            engine_version="eurusd-daily-spot-v1.4.0",
            status="SIGNAL",
            decision_mode="WITHOUT",
            metadata={"decision_source": "NATIVE", "candidate": {"direction": "SHORT", "accepted": True}},
        ).validate()
        out = v15._promote_learning_exploration(native, snapshot(), {"trades": []}, now=NOW)
        self.assertEqual(out.direction, "SHORT")
        self.assertEqual(out.entry, native.entry)
        self.assertFalse(out.metadata["exploration"]["production_authority"])




if __name__ == "__main__":
    unittest.main()
