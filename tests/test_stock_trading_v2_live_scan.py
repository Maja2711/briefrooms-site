from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_v2_fast_scan as fast_scan
from scripts import stock_trading_v2_live_scan as live_scan
from scripts import stock_trading_v2_universe as universe
from scripts import stock_trading_v2_us_screener_provider as provider


def snapshot(count: int = 12) -> dict:
    rows = [
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares"
    ]
    for index in range(count):
        rows.append(f"S{index:03d}|Synthetic {index} Inc. - Common Stock|Q|N|N|100|N|N")
    rows.append("File Creation Time: 0916202617:15|||||||")
    nasdaq = "\n".join(rows) + "\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nFile Creation Time: 0916202617:15|||||||\n"
    return universe.build_us_snapshot(nasdaq, other, generated_at="2026-09-16T16:00:00Z")


class StockTradingV2LiveScanTests(unittest.TestCase):
    def test_provider_failure_rotates_audited_universe_without_live_liquidity_claim(self):
        config = fast_scan.load_config()
        config = json.loads(json.dumps(config))
        config["provider_fallback"]["candidates_per_cycle"] = 4
        first = live_scan.build_rotation_fallback(
            snapshot(),
            config,
            error=provider.ProviderUnavailable("synthetic timeout"),
            now=datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
        )
        second = live_scan.build_rotation_fallback(
            snapshot(),
            config,
            error=provider.ProviderUnavailable("synthetic timeout"),
            now=datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(first["shortlist_size"], 4)
        self.assertTrue(first["source"]["degraded"])
        self.assertFalse(first["source"]["live_liquidity_claim"])
        self.assertTrue(first["governance"]["fallback_explicit"])
        self.assertNotEqual(
            [row["symbol"] for row in first["candidates"]],
            [row["symbol"] for row in second["candidates"]],
        )
        self.assertTrue(all(row["live_screener_metrics_available"] is False for row in first["candidates"]))
        fast_scan.validate_shortlist(first, config)

    def test_run_live_uses_fallback_when_nasdaq_provider_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe_path = root / "us.json"
            universe_path.write_text(json.dumps(snapshot()), encoding="utf-8")
            with patch.object(provider, "fetch_rows", side_effect=provider.ProviderUnavailable("timeout")):
                payload = live_scan.run_live(
                    universe_path=universe_path,
                    config_path=fast_scan.CONFIG_PATH,
                    now=datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
                )
        self.assertEqual(payload["source"]["mode"], "rotating_audited_universe_shard")
        self.assertTrue(payload["source"]["degraded"])
        self.assertFalse(payload["governance"]["production_decision_influence"])

    def test_live_provider_path_remains_preferred(self):
        rows = [
            {"symbol": "S000", "lastsale": "$10", "volume": "200000", "pctchange": "5", "marketCap": "1000000000", "name": "Synthetic 0 Inc."},
            {"symbol": "S001", "lastsale": "$20", "volume": "300000", "pctchange": "-2", "marketCap": "2000000000", "name": "Synthetic 1 Inc."},
        ]
        meta = {"provider": "Nasdaq", "mode": "paged_with_retry", "complete": True}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe_path = root / "us.json"
            universe_path.write_text(json.dumps(snapshot()), encoding="utf-8")
            with patch.object(provider, "fetch_rows", return_value=(rows, meta)):
                payload = live_scan.run_live(universe_path=universe_path, config_path=fast_scan.CONFIG_PATH)
        self.assertEqual(payload["source"]["mode"], "paged_with_retry")
        self.assertFalse(bool(payload["source"].get("degraded")))
        self.assertEqual(payload["shortlist_size"], 2)


if __name__ == "__main__":
    unittest.main()
