from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import execution_price_engine as epe
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_spot_v17 as v17


class ExecutionPriceEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 19, 2, 30, tzinfo=timezone.utc)

    def quote(self, price: float, age_seconds: float, source: str) -> epe.Quote:
        return epe.Quote(
            price=price,
            timestamp=self.now - timedelta(seconds=age_seconds),
            source=source,
        )

    def test_live_eurusd_fill_uses_current_primary_mid_after_cross_check(self) -> None:
        result = epe.verify_live_mid_fill(
            "SHORT",
            self.quote(1.13400, 8, "primary"),
            self.quote(1.13408, 14, "secondary"),
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["status"], "VERIFIED_FILL")
        self.assertEqual(result["fill_price"], 1.13400)
        self.assertEqual(result["price_type"], "LIVE_MID_PAPER_FILL")
        self.assertLess(result["cross_feed_difference_pips"], 1.0)
        self.assertFalse(result["executable_bid_ask_available"])

    def test_live_eurusd_fill_blocks_ghost_price_divergence(self) -> None:
        result = epe.verify_live_mid_fill(
            "SHORT",
            self.quote(1.13400, 8, "primary"),
            self.quote(1.13430, 10, "secondary"),
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(result["reason"], "cross_feed_divergence")

    def test_live_eurusd_fill_blocks_stale_primary(self) -> None:
        result = epe.verify_live_mid_fill(
            "LONG",
            self.quote(1.13400, 181, "primary"),
            self.quote(1.13402, 5, "secondary"),
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "primary_quote_stale_or_future")

    def test_recenter_geometry_preserves_daily_risk_distances(self) -> None:
        geometry = epe.recenter_geometry(
            "SHORT",
            analytical_entry=1.13456,
            stop=1.13762,
            target=1.12904,
            fill_price=1.13400,
        )
        self.assertEqual(geometry["entry"], 1.13400)
        self.assertEqual(geometry["stop"], 1.13706)
        self.assertEqual(geometry["target"], 1.12848)
        self.assertAlmostEqual(geometry["risk_distance"], 0.00306, places=8)
        self.assertAlmostEqual(geometry["reward_distance"], 0.00552, places=8)

    def test_wes_frozen_limit_touch_is_verified_only_inside_authorized_window(self) -> None:
        start = self.now - timedelta(minutes=10)
        expires = self.now + timedelta(minutes=30)
        point = {
            "price": 1.13420,
            "timestamp": (self.now - timedelta(minutes=2)).isoformat(),
            "source": "Yahoo Finance:EURUSD=X:5m:frozen_entry_target_touch",
            "observed_high": 1.13428,
            "observed_low": 1.13395,
        }
        result = epe.verify_frozen_limit_touch(
            point,
            direction="short",
            target_price=1.13420,
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["fill_price"], 1.13420)
        self.assertEqual(result["mode"], "FROZEN_LIMIT_TOUCH")

        early = dict(point, timestamp=(start - timedelta(seconds=1)).isoformat())
        rejected = epe.verify_frozen_limit_touch(
            early,
            direction="short",
            target_price=1.13420,
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
        )
        self.assertFalse(rejected["verified"])
        self.assertEqual(rejected["reason"], "touch_before_authorization")


class DailyEpeIntegrationTests(unittest.TestCase):
    def candidate(self) -> DailyEngineOutput:
        return DailyEngineOutput(
            instrument="EUR/USD",
            timestamp="2026-09-29T19:02:00Z",
            direction="SHORT",
            score=25.57,
            confidence=0.489,
            entry=1.13456,
            stop=1.13762,
            target=1.12904,
            horizon="intraday_to_27h",
            engine_version=v17.ENGINE_VERSION,
            status="SIGNAL",
            decision_mode="WITHOUT",
            metadata={
                "candidate": {
                    "direction": "SHORT",
                    "score": 25.57,
                    "confidence": 0.489,
                    "accepted": True,
                    "gate_reasons": [],
                },
                "risk": {},
            },
        ).validate()

    @patch("daily_eurusd_spot_v17.epe.eurusd_market_fill")
    def test_daily_entry_is_repriced_to_verified_epe_fill(self, market_fill) -> None:
        market_fill.return_value = {
            "schema_version": epe.SCHEMA_VERSION,
            "engine_version": epe.ENGINE_VERSION,
            "instrument": "EUR/USD",
            "mode": "MARKET_NOW",
            "status": "VERIFIED_FILL",
            "verified": True,
            "fill_price": 1.13400,
            "verified_at": "2026-09-29T19:03:01Z",
            "price_type": "LIVE_MID_PAPER_FILL",
        }

        output = v17._prepare_entry_candidate(self.candidate(), [], self.now())

        self.assertEqual(output.direction, "SHORT")
        self.assertEqual(output.entry, 1.13400)
        self.assertEqual(output.stop, 1.13706)
        self.assertEqual(output.target, 1.12848)
        self.assertEqual(output.timestamp, "2026-09-29T19:03:01Z")
        self.assertTrue(output.metadata["execution_price_engine"]["verified"])
        self.assertTrue(output.metadata["risk"]["execution_geometry_recentered"])

    @patch("daily_eurusd_spot_v17.epe.eurusd_market_fill")
    def test_daily_entry_fails_closed_when_epe_cannot_verify_price(self, market_fill) -> None:
        market_fill.return_value = epe.blocked(
            "cross_feed_divergence",
            mode="MARKET_NOW",
        )

        output = v17._prepare_entry_candidate(self.candidate(), [], self.now())

        self.assertEqual(output.direction, "FLAT")
        self.assertEqual(output.status, "NO_TRADE")
        self.assertIsNone(output.entry)
        self.assertFalse(output.metadata["candidate"]["accepted"])
        self.assertIn("epe_cross_feed_divergence", output.metadata["candidate"]["gate_reasons"])

    @staticmethod
    def now() -> datetime:
        return datetime(2026, 9, 29, 19, 3, 1, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
