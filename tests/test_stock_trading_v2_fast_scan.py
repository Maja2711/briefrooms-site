from __future__ import annotations

import unittest

from scripts import stock_trading_v2_fast_scan as scan
from scripts import stock_trading_v2_universe as universe


NASDAQ_SAMPLE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
BIG|Big Corp Common Stock|Q|N|N|100|N|N
MID|Midcap Systems Common Stock|Q|N|N|100|N|N
MOVE|Mover Labs Common Stock|Q|N|N|100|N|N
SPAC|Example Acquisition Corp Class A Ordinary Shares|Q|N|N|100|N|N
LOW|Low Price Common Stock|Q|N|N|100|N|N
File Creation Time: 0916202617:15|||||||
"""

OTHER_SAMPLE = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
NYSEX|NYSE Example Common Stock|N|NYSEX|N|100|N|NYSEX
File Creation Time: 0916202617:15|||||||
"""

CONFIG = {
    "schema_version": "stock-trading-v2-fast-scan-config-v1",
    "filters": {
        "minimum_price_usd": 1.0,
        "absolute_minimum_dollar_volume_usd": 0.0,
        "excluded_industries": ["Blank Checks"],
        "excluded_name_patterns": [r"\bWarrant(s)?\b", r"\bRights?\b", r"\bPreferred\b", r"\bUnits?\b"],
    },
    "selection": {
        "maximum_candidates": 4,
        "liquidity_lane": 2,
        "mover_lane": 2,
        "midcap_lane": 2,
        "mover_minimum_dollar_volume_usd": 1_000_000,
        "midcap_market_cap_min_usd": 100_000_000,
        "midcap_market_cap_max_usd": 15_000_000_000,
    },
    "governance": {"production_decision_influence": False},
}

SCREENER_ROWS = [
    {
        "symbol": "BIG",
        "name": "Big Corp Common Stock",
        "lastsale": "$200",
        "pctchange": "0.5%",
        "volume": "2000000",
        "marketCap": "100000000000",
        "sector": "Technology",
        "industry": "Computer Manufacturing",
    },
    {
        "symbol": "MID",
        "name": "Midcap Systems Common Stock",
        "lastsale": "$20",
        "pctchange": "2.0%",
        "volume": "1000000",
        "marketCap": "2000000000",
        "sector": "Technology",
        "industry": "Computer Software",
    },
    {
        "symbol": "MOVE",
        "name": "Mover Labs Common Stock",
        "lastsale": "$10",
        "pctchange": "12.0%",
        "volume": "300000",
        "marketCap": "500000000",
        "sector": "Health Care",
        "industry": "Biotechnology",
    },
    {
        "symbol": "SPAC",
        "name": "Example Acquisition Corp Class A Ordinary Shares",
        "lastsale": "$10",
        "pctchange": "0.0%",
        "volume": "5000000",
        "marketCap": "800000000",
        "sector": "Finance",
        "industry": "Blank Checks",
    },
    {
        "symbol": "LOW",
        "name": "Low Price Common Stock",
        "lastsale": "$0.50",
        "pctchange": "20.0%",
        "volume": "10000000",
        "marketCap": "300000000",
        "sector": "Industrials",
        "industry": "Industrial Machinery/Components",
    },
    {
        "symbol": "NYSEX",
        "name": "NYSE Example Common Stock",
        "lastsale": "$30",
        "pctchange": "1.0%",
        "volume": "500000",
        "marketCap": "3000000000",
        "sector": "Industrials",
        "industry": "Industrial Machinery/Components",
    },
    {
        "symbol": "NOT_IN_AUDITED_UNIVERSE",
        "name": "Unknown",
        "lastsale": "$100",
        "pctchange": "99%",
        "volume": "999999999",
        "marketCap": "999999999999",
        "sector": "Technology",
        "industry": "Computer Software",
    },
]


class StockTradingV2FastScanTests(unittest.TestCase):
    def setUp(self):
        self.universe = universe.build_us_snapshot(
            NASDAQ_SAMPLE,
            OTHER_SAMPLE,
            generated_at="2026-09-16T16:00:00Z",
        )

    def test_join_uses_only_audited_universe_and_filters_obvious_bad_inputs(self):
        payload = scan.build_shortlist(
            self.universe,
            SCREENER_ROWS,
            CONFIG,
            generated_at="2026-09-16T16:01:00Z",
        )
        symbols = {row["symbol"] for row in payload["candidates"]}
        self.assertNotIn("NOT_IN_AUDITED_UNIVERSE", symbols)
        self.assertNotIn("SPAC", symbols)
        self.assertNotIn("LOW", symbols)
        self.assertTrue({"BIG", "MID", "MOVE"}.issubset(symbols))
        self.assertFalse(payload["governance"]["production_decision_influence"])
        scan.validate_shortlist(payload, CONFIG)

    def test_midcap_lane_preserves_midcap_discovery(self):
        payload = scan.build_shortlist(self.universe, SCREENER_ROWS, CONFIG)
        indexed = {row["symbol"]: row for row in payload["candidates"]}
        self.assertIn("MID", indexed)
        self.assertIn("midcap", indexed["MID"]["lanes"])
        self.assertIn("MOVE", indexed)
        self.assertIn("midcap", indexed["MOVE"]["lanes"])

    def test_mover_lane_can_surface_non_megacap_activity(self):
        payload = scan.build_shortlist(self.universe, SCREENER_ROWS, CONFIG)
        indexed = {row["symbol"]: row for row in payload["candidates"]}
        self.assertIn("movers", indexed["MOVE"]["lanes"])
        self.assertGreater(indexed["MOVE"]["pct_change"], indexed["BIG"]["pct_change"])

    def test_shortlist_is_capped_and_ranked(self):
        payload = scan.build_shortlist(self.universe, SCREENER_ROWS, CONFIG)
        self.assertLessEqual(payload["shortlist_size"], 4)
        ranks = [row["stage_zero_rank"] for row in payload["candidates"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_tamper_is_detected(self):
        payload = scan.build_shortlist(self.universe, SCREENER_ROWS, CONFIG)
        payload["candidates"][0]["price"] = 999999.0
        with self.assertRaises(Exception):
            scan.validate_shortlist(payload, CONFIG)


if __name__ == "__main__":
    unittest.main()
