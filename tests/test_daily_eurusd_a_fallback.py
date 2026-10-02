from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
    def test_native_directional_candidate_is_never_changed_by_arm_a(self):
        native = DailyEngineOutput(
            instrument="EUR/USD", timestamp=NOW.isoformat().replace("+00:00", "Z"),
            direction="SHORT", score=38.0, confidence=.24,
            entry=1.1686, stop=1.1718, target=1.1628,
            horizon="intraday_to_24h", engine_version="eurusd-daily-spot-v1.2.0",
            status="SIGNAL", decision_mode="WITHOUT",
            metadata={"decision_source": "NATIVE"},
        ).validate()
        out = v13._promote_a_fallback(
            native, snapshot(),
            {"direction": "LONG", "score": 72.0, "confidence": .44},
            now=NOW,
        )
        self.assertEqual(out.direction, "SHORT")
        self.assertEqual(out.entry, native.entry)
        self.assertFalse(out.metadata["a_fallback"]["production_authority"])
        self.assertEqual(out.metadata["a_fallback"]["shadow_direction"], "LONG")

    def test_arm_a_long_cannot_promote_native_flat(self):
        out = v13._promote_a_fallback(
            native_flat(), snapshot(),
            {"direction": "LONG", "score": 64.5, "confidence": .29},
            now=NOW + timedelta(minutes=10),
        )
        self.assertEqual(out.direction, "FLAT")
        self.assertIsNone(out.entry)
        self.assertEqual(out.status, "NO_TRADE")
        self.assertFalse(out.metadata["a_fallback"]["production_authority"])
        self.assertEqual(out.metadata["a_fallback"]["shadow_direction"], "LONG")

    def test_arm_a_short_cannot_promote_native_flat(self):
        out = v13._promote_a_fallback(
            native_flat(), snapshot(),
            {"direction": "SHORT", "score": 36.0, "confidence": .28},
            now=NOW + timedelta(minutes=10),
        )
        self.assertEqual(out.direction, "FLAT")
        self.assertIsNone(out.entry)
        self.assertEqual(out.metadata["a_fallback"]["shadow_direction"], "SHORT")

    @patch.object(v13, "fetch_a_technical_signal")
    def test_production_build_does_not_fetch_or_use_arm_a(self, fetch) -> None:
        native = native_flat()
        with patch.object(v13, "_original_build_output", return_value=native):
            out = v13.build_output(snapshot(), {"trades": []})
        fetch.assert_not_called()
        self.assertEqual(out.direction, "FLAT")
        self.assertEqual(out.metadata["a_fallback"]["status"], "DETACHED_FROM_PRODUCTION")




if __name__ == "__main__":
    unittest.main()
