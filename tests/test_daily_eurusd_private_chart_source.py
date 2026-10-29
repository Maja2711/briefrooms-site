"""Isolated private Saxo Chart market authority for TR-03 (fail-closed)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_eurusd_spot_v19 as daily


class PrivateSaxoDataAuthorityTests(unittest.TestCase):
    def test_private_runtime_refuses_yahoo_when_chart_source_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with patch.dict(os.environ,{
                "SAXO_PRIVATE_PRIMARY":"1",
                "BRIEFROOMS_PRIVATE_RUNTIME":"1",
                "BRIEFROOMS_PRIVATE_DATA_DIR":str(root),
                "SAXO_CHART_PRIMARY":"0"
            }):
                with patch.object(daily.base,"YahooChartClient",
                                  side_effect=AssertionError("Yahoo forbidden")) as yahoo:
                    with self.assertRaisesRegex(ValueError,"private_saxo_chart_not_enabled"):
                        daily.run_cycle(root/"eurusd_daily_spot.json",root/"eurusd_daily_history.json")
                    yahoo.assert_not_called()

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
