from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import gpw_daily_pick as gpw
from scripts import gpw_market_data as market


WARSAW = ZoneInfo("Europe/Warsaw")


def sample_bars(count: int = 70) -> list[gpw.Bar]:
    start = date(2026, 4, 1)
    return [
        gpw.Bar(
            day=start,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=100_000,
        )
        for _ in range(count)
    ]


class GpwMarketDataTests(unittest.TestCase):
    def test_resilient_history_falls_back_to_stooq(self):
        expected = sample_bars()
        with (
            patch.object(market, "_ORIGINAL_YAHOO_FETCHER", side_effect=gpw.PublicationError("down")),
            patch.object(market, "fetch_stooq_daily_bars", return_value=expected) as stooq,
        ):
            result = market.fetch_resilient_bars("PKO.WA")
        self.assertIs(result, expected)
        stooq.assert_called_once_with("PKO.WA", range_value="6mo")

    def test_opening_snapshot_confirms_close_independent_prices(self):
        now = datetime(2026, 8, 18, 9, 6, tzinfo=WARSAW)
        yahoo = market.OpeningQuote("Yahoo", "PKO.WA", now.date(), now, 80.0, 81.0, 79.8, 80.50, 1000)
        stooq = market.OpeningQuote("Stooq", "PKO.WA", now.date(), now, 80.0, 81.0, 79.8, 80.55, 1100)
        with (
            patch.object(market, "fetch_yahoo_opening_quote", return_value=yahoo),
            patch.object(market, "fetch_stooq_opening_quote", return_value=stooq),
        ):
            snapshot = market.opening_snapshot("PKO.WA", now=now)
        self.assertEqual(snapshot["crosscheck"]["status"], "confirmed")
        self.assertLess(snapshot["crosscheck"]["last_price_deviation"], 0.02)
        self.assertTrue(snapshot["market_snapshot_id"].startswith("mkt-"))
        self.assertEqual(snapshot["canonical_market_snapshot"]["instrument_id"], "equity.pl.pko")
        self.assertEqual(snapshot["canonical_data_quality"]["status"], "OK")
        self.assertTrue(snapshot["canonical_market_snapshot"]["observed_at"].endswith("Z"))
        self.assertEqual(
            snapshot["canonical_market_snapshot"]["snapshot_hash"],
            snapshot["market_snapshot_hash"],
        )

    def test_opening_snapshot_rejects_material_provider_divergence(self):
        now = datetime(2026, 8, 18, 9, 6, tzinfo=WARSAW)
        yahoo = market.OpeningQuote("Yahoo", "PKO.WA", now.date(), now, 80.0, 81.0, 79.8, 80.0, 1000)
        stooq = market.OpeningQuote("Stooq", "PKO.WA", now.date(), now, 80.0, 84.0, 79.8, 84.0, 1100)
        with (
            patch.object(market, "fetch_yahoo_opening_quote", return_value=yahoo),
            patch.object(market, "fetch_stooq_opening_quote", return_value=stooq),
        ):
            with self.assertRaises(gpw.PublicationError):
                market.opening_snapshot("PKO.WA", now=now)

    def test_reprice_transaction_anchors_setup_to_current_session(self):
        now = datetime(2026, 8, 18, 9, 6, tzinfo=WARSAW)
        payload = {
            "decision": "TRANSAKCJA",
            "selection": {
                "symbol": "PKO.WA",
                "reference_price": 78.0,
                "entry_zone": [77.5, 79.0],
                "stop": 76.0,
                "target": 81.6,
                "reward_risk": 1.8,
            },
            "data_quality": {},
        }
        snapshot = {
            "provider": "Stooq",
            "symbol": "PKO.WA",
            "date": "2026-08-18",
            "observed_at": "2026-08-18T09:06:00+02:00",
            "open": 80.0,
            "high": 81.0,
            "low": 79.8,
            "last": 80.5,
            "volume": 1200,
            "crosscheck": {"status": "confirmed", "available_providers": ["Yahoo", "Stooq"]},
        }
        with patch.object(market, "opening_snapshot", return_value=snapshot):
            result = market.reprice_transaction(payload, now=now)
        selection = result["selection"]
        self.assertEqual(selection["reference_price"], 80.5)
        self.assertEqual(selection["market_snapshot"]["date"], "2026-08-18")
        self.assertGreater(selection["target"], selection["reference_price"])
        self.assertLess(selection["stop"], selection["reference_price"])
        self.assertEqual(result["data_quality"]["opening_quote"], "confirmed")


if __name__ == "__main__":
    unittest.main()
