from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from instrument_registry import DEFAULT_REGISTRY, InstrumentRegistryError  # noqa: E402
import investments_weekly_v2 as v2  # noqa: E402
import investments_weekly_v3 as v3  # noqa: E402
import investments_weekly_v4 as v4  # noqa: E402
import investments_weekly_macro as macro  # noqa: E402
import investments_weekly_ma_structure as ma_structure  # noqa: E402


class InstrumentRegistryWesIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.method = json.loads(
            (ROOT / "data" / "investments" / "methodology.json").read_text(encoding="utf-8")
        )
        cls.policy = json.loads(
            (ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json").read_text(encoding="utf-8")
        )

    def cfg(self, instrument_id: str) -> dict:
        return next(row for row in self.method["instruments"] if row["id"] == instrument_id)

    def test_current_wes_symbols_remain_identical_after_registry_routing(self):
        for instrument_id in ("eurusd", "sp500_futures", "btcusd"):
            cfg = self.cfg(instrument_id)
            self.assertEqual(
                v2.canonical_yahoo_symbol(instrument_id, cfg["symbol"]),
                cfg["symbol"],
            )

    def test_v2_model_signal_requests_registry_symbol(self):
        cfg = self.cfg("sp500_futures")
        with patch.object(v2, "download_daily", return_value=None) as download:
            result = v2.model_signal(cfg, self.method, "2026-W35", datetime(2026, 8, 26, 12, 0))
        download.assert_called_once_with("ES=F")
        self.assertEqual(result["data_quality"], "failed")

    def test_v2_model_signal_rejects_symbol_drift_before_download(self):
        cfg = dict(self.cfg("sp500_futures"))
        cfg["symbol"] = "SPY"
        with patch.object(v2, "download_daily", return_value=None) as download:
            with self.assertRaises(InstrumentRegistryError):
                v2.model_signal(cfg, self.method, "2026-W35", datetime(2026, 8, 26, 12, 0))
        download.assert_not_called()

    def test_v3_weekly_signal_uses_registry_symbol(self):
        cfg = self.cfg("btcusd")
        with patch.object(v3, "_weekly_frame", return_value=None) as weekly_frame:
            result = v3.weekly_candle_signal(cfg, self.policy)
        weekly_frame.assert_called_once_with("BTC-USD")
        self.assertEqual(result["data_quality"], "failed")

    def test_v4_config_lookup_is_registry_guarded(self):
        cfg = v4.instrument_cfg(self.method, "eurusd")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["symbol"], "EURUSD=X")

        drifted = json.loads(json.dumps(self.method))
        next(row for row in drifted["instruments"] if row["id"] == "eurusd")["symbol"] = "EURUSD"
        with self.assertRaises(InstrumentRegistryError):
            v4.instrument_cfg(drifted, "eurusd")

    def test_v4_latest_mark_uses_registry_symbol(self):
        cfg = self.cfg("sp500_futures")
        now = datetime(2026, 8, 26, 12, 0)
        with patch.object(v3, "last_completed_5m_bar", return_value=None) as mark:
            v4.latest_mark(cfg, now)
        mark.assert_called_once_with("ES=F", now)

    def test_macro_references_are_resolved_by_canonical_ids(self):
        cfg = dict(self.policy["macro_context"])
        self.assertEqual(macro._canonical_macro_symbols(cfg), ("CL=F", "^TNX"))
        cfg["oil_symbol"] = "BZ=F"
        with self.assertRaises(InstrumentRegistryError):
            macro._canonical_macro_symbols(cfg)

    def test_ma_structure_uses_registry_eurusd_symbol(self):
        self.assertEqual(ma_structure.SYMBOL, DEFAULT_REGISTRY.get_vendor_symbol("eurusd", "yahoo"))


if __name__ == "__main__":
    unittest.main()
