from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.belief_core import BeliefCore
from scripts.belief_core_live import BELIEFS, _belief_ids_for_consumer, run_cycle
from scripts.belief_market_data_adapter import Bar
from scripts.belief_wes_assets_adapter import BTC_SYMBOL, EURUSD_SYMBOL, WES_ASSET_BELIEF_IDS

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


class FullClient:
    def __init__(self, now: datetime) -> None:
        starts = {
            "SPY":100.0,"RSP":50.0,"IWM":200.0,"^VIX":18.0,
            "HYG":80.0,"LQD":100.0,"TLT":90.0,"UUP":25.0,
            EURUSD_SYMBOL:1.16,BTC_SYMBOL:60000.0,
        }
        steps = {
            "SPY":.10,"RSP":.06,"IWM":.25,"^VIX":-.01,
            "HYG":.02,"LQD":.005,"TLT":.015,"UUP":-.002,
            EURUSD_SYMBOL:.00012,BTC_SYMBOL:25.0,
        }
        end = now.astimezone(NY).replace(minute=0, second=0, microsecond=0)
        self.rows = {}
        for symbol, start in starts.items():
            bars = []
            for i in range(90):
                ts = end - timedelta(minutes=30 * (89-i))
                close = start + steps[symbol] * i
                bars.append(Bar(ts.astimezone(UTC), close, open=close*.9999, high=close*1.0005, low=close*.9995, volume=100000+i*100))
            self.rows[symbol] = bars

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m"):
        return list(self.rows[symbol])


class DailyWESAssetShadowTests(unittest.TestCase):
    def test_consumer_selects_exactly_seven_non_spx_wes_beliefs(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = BeliefCore(Path(tmp))
            core.register_beliefs(BELIEFS)
            ids = _belief_ids_for_consumer(core, "WES-ASSET-SHADOW")
            self.assertEqual(ids, sorted(WES_ASSET_BELIEF_IDS))
            self.assertEqual(len(ids), 7)
            self.assertTrue(all(not belief_id.startswith("spx.") for belief_id in ids))

    def test_daily_slot_freezes_seven_asset_forecasts_without_expanding_brace(self):
        now = datetime(2026, 8, 20, 10, 7, tzinfo=NY)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "core"
            client = FullClient(now)
            status = run_cycle(state_dir, now, client)
            self.assertEqual(status["shared_forecasts_frozen"], 5)
            self.assertEqual(status["wes_asset_forecasts_frozen"], 7)
            self.assertEqual(status["wes_forecasts_frozen"], 0)
            self.assertEqual(status["evidence_ingested"], 16)
            self.assertEqual(status["observations_collected"], 135)

            core = BeliefCore(state_dir)
            self.assertEqual(len(core.forecasts), 12)
            shared = [f for f in core.forecasts.values() if f.metadata.get("consumer") == "BRACE+BRACE-SPX"]
            assets = [f for f in core.forecasts.values() if f.metadata.get("consumer") == "WES-ASSET-SHADOW"]
            self.assertEqual(len(shared), 5)
            self.assertTrue(all(f.belief_id.startswith("spx.") for f in shared))
            self.assertEqual(len(assets), 7)
            self.assertEqual({f.belief_id for f in assets}, set(WES_ASSET_BELIEF_IDS))
            self.assertTrue(all(f.horizon_hours < 24 for f in assets))

            retry = run_cycle(state_dir, now, client)
            self.assertEqual(retry["shared_forecasts_frozen"], 0)
            self.assertEqual(retry["wes_asset_forecasts_frozen"], 0)
            self.assertEqual(len(BeliefCore(state_dir).forecasts), 12)


if __name__ == "__main__":
    unittest.main()
