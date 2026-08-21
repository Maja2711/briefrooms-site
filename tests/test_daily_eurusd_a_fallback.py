from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar, MarketSnapshot
from scripts.daily_engine_contract import DailyEngineOutput
from scripts import daily_eurusd_spot_v13 as v13

UTC = timezone.utc
NOW = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)


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


def native_flat(ts: datetime = NOW) -> DailyEngineOutput:
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=ts.isoformat().replace("+00:00", "Z"),
        direction="FLAT",
        score=44.0,
        confidence=0.0,
        entry=None,
        stop=None,
        target=None,
        horizon="intraday_to_24h",
        engine_version="eurusd-daily-spot-v1.2.0",
        status="NO_TRADE",
        decision_mode="WITHOUT",
        metadata={"candidate": {"direction": "FLAT", "score": 44.0}, "components": {"trend": -0.2}},
    ).validate()


class DailyEURUSDAFallbackTests(unittest.TestCase):
    def test_native_directional_candidate_keeps_priority(self):
        native = DailyEngineOutput(
            instrument="EUR/USD", timestamp=NOW.isoformat().replace("+00:00", "Z"),
            direction="SHORT", score=38.0, confidence=.24,
            entry=1.1686, stop=1.1718, target=1.1628,
            horizon="intraday_to_24h", engine_version="eurusd-daily-spot-v1.2.0",
            status="SIGNAL", decision_mode="WITHOUT", metadata={},
        ).validate()
        promoted = v13._promote_a_fallback(native, snapshot(), {"direction": "LONG", "score": 72.0, "confidence": .44}, now=NOW)
        self.assertIs(promoted, native)

    def test_fresh_a_long_promotes_native_flat(self):
        promoted = v13._promote_a_fallback(
            native_flat(), snapshot(),
            {"direction": "LONG", "score": 64.5, "confidence": .29},
            now=NOW + timedelta(minutes=10),
        )
        self.assertEqual(promoted.direction, "LONG")
        self.assertEqual(promoted.engine_version, "eurusd-daily-spot-v1.3.0")
        self.assertEqual(promoted.metadata["decision_source"], "A_TECHNICAL_FALLBACK")
        self.assertFalse(promoted.metadata["learning_eligible"])
        self.assertEqual(promoted.metadata["components"], {})
        self.assertLess(promoted.stop, promoted.entry)
        self.assertGreater(promoted.target, promoted.entry)

    def test_fresh_a_short_is_directionally_symmetric(self):
        promoted = v13._promote_a_fallback(
            native_flat(), snapshot(),
            {"direction": "SHORT", "score": 36.0, "confidence": .28},
            now=NOW + timedelta(minutes=10),
        )
        self.assertEqual(promoted.direction, "SHORT")
        self.assertGreater(promoted.stop, promoted.entry)
        self.assertLess(promoted.target, promoted.entry)

    def test_stale_native_market_cannot_be_promoted(self):
        promoted = v13._promote_a_fallback(
            native_flat(NOW - timedelta(hours=3)), snapshot(),
            {"direction": "LONG", "score": 70.0, "confidence": .40},
            now=NOW,
        )
        self.assertEqual(promoted.direction, "FLAT")

    def test_a_flat_keeps_native_flat(self):
        promoted = v13._promote_a_fallback(
            native_flat(), snapshot(),
            {"direction": "FLAT", "score": 58.0, "confidence": 0.0},
            now=NOW,
        )
        self.assertEqual(promoted.direction, "FLAT")


if __name__ == "__main__":
    unittest.main()
