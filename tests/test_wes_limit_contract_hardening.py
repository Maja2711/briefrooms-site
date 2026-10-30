"""Regression tests for canonical WES LIMIT execution contracts."""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import investments_weekly_v5 as v5

class CanonicalLimitContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=v5.legacy.TZ)

    def pending(self, iid="btcusd", expires=None):
        return {"decision": {"direction": "long"}, "entry_price_plan": {
            "instrument_id": iid, "direction": "long", "execution_mode": "limit_pullback",
            "target_price": 100.0,
            "entry_not_before": (self.now-timedelta(minutes=5)).isoformat(),
            "expires_at": (expires or (self.now+timedelta(minutes=5))).isoformat()}}

    def test_verified_fresh_canonical_market_touch_for_btc_and_spx(self):
        for iid in ("btcusd", "sp500_futures"):
            with self.subTest(iid=iid):
                pending = self.pending(iid)
                stamp = (self.now-timedelta(seconds=4)).isoformat()
                live = {"prices": {iid: {"price":99.0, "timestamp":stamp, "source":"test"}}}
                with patch.object(v5,"read",return_value=live):
                    point=v5._canonical_live_entry_point(pending,self.now)
                self.assertIsNotNone(point)
                verified, audit=v5.epe_verified_entry_point(pending,point,self.now)
                self.assertTrue(audit["verified"])
                self.assertEqual(100.0,verified["price"])

    def test_historical_bar_touch_is_not_an_executable_quote(self):
        pending=self.pending()
        point={"price":100.0,"timestamp":self.now.isoformat(),
               "source":"Coinbase Exchange:BTC-USD:5m:frozen_entry_target_touch",
               "observed_low":98.0,"observed_high":102.0}
        verified,audit=v5.epe_verified_entry_point(pending,point,self.now)
        self.assertIsNone(verified)
        self.assertEqual("historical_target_touch_not_live_executable_quote",audit["reason"])

    def test_stale_or_expired_live_point_rejected(self):
        for iid in ("btcusd","sp500_futures"):
            pending=self.pending(iid,expires=self.now-timedelta(minutes=2))
            live={"prices":{iid:{"price":99.0,"timestamp":self.now.isoformat(),"source":"test"}}}
            with patch.object(v5,"read",return_value=live):
                self.assertIsNone(v5._canonical_live_entry_point(pending,self.now))
            point={"price":100.0,"canonical_live_price":99.0,
                   "timestamp":(self.now-timedelta(minutes=8)).isoformat()}
            verified,audit=v5.epe_verified_entry_point(pending,point,self.now)
            self.assertIsNone(verified)
            self.assertFalse(audit["verified"])

if __name__=="__main__":
    unittest.main()
