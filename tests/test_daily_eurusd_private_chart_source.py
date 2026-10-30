"""Isolated private Saxo Chart market authority for TR-03 (fail-closed)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_eurusd_spot_v19 as daily


class PrivateSaxoDataAuthorityTests(unittest.TestCase):
    def test_chart_opt_in_makes_private_daily_never_read_yahoo(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            with patch.dict(os.environ,{
                "SAXO_PRIVATE_PRIMARY":"1",
                "SAXO_STREAM_PRIMARY":"1",
                "BRIEFROOMS_PRIVATE_RUNTIME":"1",
                "BRIEFROOMS_PRIVATE_DATA_DIR":str(root),
                "SAXO_CHART_PRIMARY":"1",
                "SAXO_PRIVATE_BASIC_PASSWORD":"test"
            }):
                with patch.object(daily.base,"YahooChartClient",
                                  side_effect=AssertionError("Yahoo forbidden")) as yahoo:
                    with patch("saxo_private_chart.SaxoPrivateChartClient",
                               side_effect=RuntimeError("private Saxo chart invoked")) as source:
                        with self.assertRaisesRegex(RuntimeError,"private Saxo chart invoked"):
                            daily.run_cycle(root/"eurusd_daily_spot.json",root/"eurusd_daily_history.json")
                        source.assert_called_once()
                        yahoo.assert_not_called()

    def test_private_chart_disabled_preserves_existing_staging_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            with patch.dict(os.environ,{
                "SAXO_PRIVATE_PRIMARY":"1",
                "BRIEFROOMS_PRIVATE_RUNTIME":"1",
                "BRIEFROOMS_PRIVATE_DATA_DIR":str(root),
                "SAXO_CHART_PRIMARY":"0"
            }):
                with patch.object(daily.base,"YahooChartClient",
                                  side_effect=RuntimeError("legacy staging price invoked")):
                    with self.assertRaisesRegex(RuntimeError,"legacy staging price invoked"):
                        daily.run_cycle(root/"eurusd_daily_spot.json",root/"eurusd_daily_history.json")

    def test_private_h1_history_uses_verified_completed_10d_30m(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY":"1","SAXO_CHART_PRIMARY":"1"
        }):
            self.assertEqual(daily._h1_market_history_range(),"10d")
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_PRIMARY":"0","SAXO_CHART_PRIMARY":"1"
        }):
            self.assertEqual(daily._h1_market_history_range(),"60d")

    def test_public_client_path_does_not_change_on_private_chart_flag(self):
        with patch.dict(os.environ,{
            "SAXO_PRIVATE_PRIMARY":"0",
            "SAXO_CHART_PRIMARY":"1",
        }):
            with patch.object(daily.base,"YahooChartClient",
                              side_effect=RuntimeError("public Yahoo invoked")):
                with self.assertRaisesRegex(RuntimeError,"public Yahoo invoked"):
                    daily.run_cycle(Path("unused-state"),Path("unused-history"))

if __name__=="__main__":
    unittest.main()
