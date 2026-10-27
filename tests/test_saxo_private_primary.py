"""Saxo mid is the paper fill *only* in private, off-repository Daily runtime."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import daily_eurusd_spot_v19 as v19
import execution_price_engine as epe


class SaxoPrivatePrimaryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 19, 10, 15, tzinfo=timezone.utc)

    def _run(self):
        q1 = epe.Quote(1.11360, self.now - timedelta(seconds=2), "Stooq:EURUSD:live")
        q2 = epe.Quote(1.11361, self.now - timedelta(seconds=3), "fxapi.app:EUR/USD:mid")
        with patch.object(epe, "fetch_stooq_eurusd_quote", return_value=q1), patch.object(
            epe, "fetch_fxapi_eurusd_quote", return_value=q2
        ), patch.object(epe, "fetch_currency_exchange_tool_eurusd_quote",
                        side_effect=OSError("vendor down")), patch.object(
            epe, "fetch_yahoo_eurusd_quote", side_effect=OSError("vendor down")
        ):
            return epe.daily_eurusd_market_fill("LONG", now=self.now)

    def test_private_mode_uses_saxo_mid_not_public_mid_as_paper_fill(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1",
            "SAXO_PRIVATE_QUALITY_GATE": "0"
        }), patch("saxo_private_feed.fetch_private_saxo_mid", return_value=1.11362):
            result = self._run()
        self.assertTrue(result["verified"])
        self.assertTrue(result["private_saxo_primary"])
        self.assertEqual(result["selected_mid_price"], 1.11362)
        self.assertEqual(result["fill_price"], 1.11377)
        self.assertEqual(result["selected_quote"]["price"], 1.11362)
        self.assertFalse(result["redistributable"])
        self.assertFalse(result["executable_bid_ask_available"])
        self.assertEqual(result["cross_feed_difference_pips"], 0.2)
        self.assertTrue(result["paper_trading_only"])

    def test_private_mode_fails_closed_without_explicit_private_runtime(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "0"
        }), patch("saxo_private_feed.fetch_private_saxo_mid") as fetch:
            result = self._run()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "saxo_private_runtime_required")
        fetch.assert_not_called()

    def test_private_mode_fails_closed_when_saxo_unavailable(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1"
        }), patch("saxo_private_feed.fetch_private_saxo_mid", side_effect=RuntimeError(
            "NEVER_LEAK_PRIVATE_WORKER_PASSWORD"
        )):
            result = self._run()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "private_saxo_quote_unavailable")
        self.assertNotIn("NEVER_LEAK", json.dumps(result))

    def test_private_mode_rejects_divergence(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1"
        }), patch("saxo_private_feed.fetch_private_saxo_mid", return_value=1.11402):
            result = self._run()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "private_saxo_public_consensus_mismatch")

    def test_public_default_never_uses_private_saxo(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_PRIMARY": "0",
                                     "SAXO_PRIVATE_QUALITY_GATE": "0"}), patch(
            "saxo_private_feed.fetch_private_saxo_mid",
            side_effect=AssertionError("must not read")
        ) as fetch:
            result = self._run()
        self.assertTrue(result["verified"])
        self.assertEqual(result["selected_mid_price"], 1.11360)
        self.assertEqual(result["fill_price"], 1.11375)
        fetch.assert_not_called()

    def test_local_private_history_can_run_outside_public_repo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            output = root / "eurusd_daily_spot.json"
            history = root / "eurusd_daily_history.json"
            with patch.dict(os.environ, {
                "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1",
                "BRIEFROOMS_PRIVATE_DATA_DIR": str(root),
            }):
                v19._assert_private_saxo_runtime_paths(output, history)
                with self.assertRaisesRegex(ValueError, "both be inside private directory"):
                    v19._assert_private_saxo_runtime_paths(Path(__file__).resolve(), history)
                with self.assertRaisesRegex(ValueError, "separate files"):
                    v19._assert_private_saxo_runtime_paths(output, output)

    def test_public_repo_paths_cannot_hold_saxo_primary_results(self):
        repo = Path(v19.__file__).resolve().parents[1]
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1",
            "BRIEFROOMS_PRIVATE_DATA_DIR": str(repo),
        }):
            with self.assertRaisesRegex(ValueError, "cannot be inside public repository"):
                v19._assert_private_saxo_runtime_paths(
                    repo / "data/investments/eurusd_daily_spot.json",
                    repo / "data/investments/eurusd_daily_history.json",
                )

    def test_no_private_path_means_no_private_authority(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY": "1", "BRIEFROOMS_PRIVATE_RUNTIME": "1",
            "BRIEFROOMS_PRIVATE_DATA_DIR": "",
        }):
            with self.assertRaisesRegex(ValueError, "absolute private output directory"):
                v19._assert_private_saxo_runtime_paths(
                    Path("/tmp/daily-spot.json"), Path("/tmp/daily-history.json")
                )


if __name__ == "__main__":
    unittest.main()
