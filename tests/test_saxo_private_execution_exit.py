"""Prospective private Saxo SIM entry/exit settlement; no Yahoo paper fills."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from belief_market_data_adapter import Bar
import daily_eurusd_fast_lifecycle as fast
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot_v18 as v18
import saxo_private_feed as feed


class PrivateSaxoExitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 19, 10, 15, tzinfo=timezone.utc)
        self.position = {
            "trade_id": "eurusd:test:LONG", "direction": "LONG",
            "opened_at": (self.now - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "expires_at": (self.now + timedelta(hours=10)).isoformat().replace("+00:00", "Z"),
            "entry": 1.11372, "stop": 1.11200, "target": 1.11800,
            "execution_price_engine": {
                "private_saxo_primary": True,
                "price_type": "SAXO_SIM_BID_ASK_INDICATIVE_PRIVATE",
                "execution_cost_contract": lifecycle.EXECUTION_COST_CONTRACT,
                "synthetic_spread_pips": 3.0,
                "synthetic_half_spread_pips": 1.5,
            },
        }
        self.quote = {
            "bid": 1.11352, "ask": 1.11372, "mid": 1.11362,
            "observed_at": self.now - timedelta(seconds=1),
            "redistributable": False,
        }
        self.trigger = {
            "exit_reason": "DYNAMIC_RISK_EXIT",
            "closed_at": (self.now - timedelta(minutes=5)).isoformat(),
            "exit_price": 1.11000,
            "monitor": {"dynamic_exit": {"policy": "R_PACE_V1"}},
        }
        self.env = {"SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1"}

    def test_long_sells_at_saxo_bid_not_yahoo_stop_or_mid(self):
        with patch.dict(os.environ, self.env), patch(
            "saxo_private_feed.fetch_private_saxo_quote", return_value=self.quote
        ):
            trade = feed.private_saxo_exit_record(self.position, self.trigger, now=self.now)
        self.assertEqual(trade["exit_price"], 1.11352)
        self.assertEqual(trade["monitor"]["exit_price_basis"],
                         "SAXO_SIM_BID_FOR_LONG_ASK_FOR_SHORT")
        self.assertEqual(trade["monitor"]["source"],
                         "Saxo OpenAPI SIM:EUR/USD (private indicative quote)")
        self.assertEqual(trade["monitor"]["dynamic_exit"]["policy"], "R_PACE_V1")
        self.assertNotEqual(trade["closed_at"], self.trigger["closed_at"])
        self.assertFalse(trade["monitor"]["redistributable"])

    def test_short_buys_at_saxo_ask_not_yahoo_target(self):
        position = {**self.position, "direction": "SHORT", "entry": 1.11352,
                    "stop": 1.11600, "target": 1.11000}
        with patch.dict(os.environ, self.env), patch(
            "saxo_private_feed.fetch_private_saxo_quote", return_value=self.quote
        ):
            trade = feed.private_saxo_exit_record(position, self.trigger, now=self.now)
        self.assertEqual(trade["exit_price"], 1.11372)

    def test_normal_daily_and_fast_use_same_saxo_exit_settlement(self):
        # The runtime's close quote must be fresh against the actual wall clock;
        # the other deterministic tests explicitly inject a frozen clock.
        live_quote = {**self.quote, "observed_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        with patch.dict(os.environ, self.env), patch(
            "daily_eurusd_spot_v18._original_evaluate_position", return_value=self.trigger
        ), patch("saxo_private_feed.fetch_private_saxo_quote", return_value=live_quote):
            trade = v18._evaluate_position(self.position, [], self.now)
        self.assertEqual(trade["exit_price"], self.quote["bid"])
        self.assertIs(fast.v18, v18)

    def test_missing_saxo_never_closes_at_yahoo_price(self):
        with patch.dict(os.environ, self.env), patch(
            "daily_eurusd_spot_v18._original_evaluate_position", return_value=self.trigger
        ), patch("saxo_private_feed.fetch_private_saxo_quote",
                 side_effect=feed.PrivateSaxoFeedError("private_saxo_quote_unavailable")):
            with self.assertRaises(feed.PrivateSaxoFeedError):
                v18._evaluate_position(self.position, [], self.now)

    def test_stale_quote_never_backdates_or_closes(self):
        quote = {**self.quote, "observed_at": self.now - timedelta(seconds=40)}
        with patch.dict(os.environ, self.env), patch(
            "saxo_private_feed.fetch_private_saxo_quote", return_value=quote
        ):
            with self.assertRaises(feed.PrivateSaxoFeedError):
                feed.private_saxo_exit_record(self.position, self.trigger, now=self.now)

    def test_private_position_requires_explicit_runtime(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_PRIMARY": "0",
                                     "BRIEFROOMS_PRIVATE_RUNTIME": "0"}), patch(
            "saxo_private_feed.fetch_private_saxo_quote"
        ) as fetch:
            with self.assertRaises(feed.PrivateSaxoFeedError):
                feed.private_saxo_exit_record(self.position, self.trigger, now=self.now)
            fetch.assert_not_called()

    def test_public_position_keeps_original_exit_without_saxo(self):
        public = {**self.position, "execution_price_engine": {"price_type": "PUBLIC"}}
        with patch.dict(os.environ, {"SAXO_PRIVATE_PRIMARY": "0"}), patch(
            "daily_eurusd_spot_v18._original_evaluate_position", return_value=self.trigger
        ), patch("saxo_private_feed.fetch_private_saxo_quote",
                 side_effect=AssertionError("Saxo cannot run in public")) as fetch:
            trade = v18._evaluate_position(public, [], self.now)
        self.assertEqual(trade["exit_price"], 1.11000)
        fetch.assert_not_called()

    def test_fast_watcher_denies_public_paths_for_private_saxo(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder).resolve()
            private = base / "private"
            private.mkdir()
            with patch.dict(os.environ, {**self.env, "BRIEFROOMS_PRIVATE_DATA_DIR": str(private)}):
                feed.assert_private_state_destination(
                    self.position, private / "spot.json", private / "history.json")
                with self.assertRaisesRegex(ValueError, "private directory"):
                    feed.assert_private_state_destination(
                        self.position, Path(__file__).resolve(), private / "history.json")

    def test_quote_validator_recovers_bid_and_ask(self):
        raw = {
            "source": "SAXO_SIM", "instrument": "EUR/USD",
            "bid": 1.11351, "ask": 1.11371, "mid": 1.11361,
            "last_updated": "2026-10-27T19:10:14.500Z",
            "delayed_by_minutes": 0, "market_state": "Open",
            "trading_enabled": False, "redistributable": False,
        }
        q = feed.validate_private_saxo_snapshot(raw, now=self.now)
        self.assertEqual(q["bid"], 1.11351)
        self.assertEqual(q["ask"], 1.11371)
        self.assertEqual(q["mid"], 1.11361)


if __name__ == "__main__":
    unittest.main()
