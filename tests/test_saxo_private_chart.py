"""No-network contract tests for private Saxo Chart observations."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from saxo_private_chart import SaxoPrivateChartClient, SaxoChartUnavailable, _bar, CHART_URL


class ChartTests(unittest.TestCase):
    def test_bid_ask_mid_bar_and_timestamp(self):
        d = {"Time":"2026-10-29T20:10:00Z"}
        for key, a, b in (("Open",1.12,1.1202),("High",1.121,1.1212),
                          ("Low",1.119,1.1192),("Close",1.1205,1.1207)):
            d[key+"Bid"]=a
            d[key+"Ask"]=b
        b=_bar(d)
        self.assertAlmostEqual(b.close,1.1206)
        self.assertEqual(b.timestamp.isoformat(),"2026-10-29T20:10:00+00:00")

    def test_extrema_are_not_misclassified_as_a_live_spread(self):
        d={"Time":"2026-10-29T20:10:00Z",
           "OpenBid":1.12,"OpenAsk":1.1202,
           "HighBid":1.122,"HighAsk":1.1245,
           "LowBid":1.117,"LowAsk":1.1195,
           "CloseBid":1.121,"CloseAsk":1.1212}
        bar=_bar(d)
        self.assertTrue(bar.high > bar.close > bar.low)
        self.assertAlmostEqual(bar.close,1.1211)

    def test_historical_close_side_delta_does_not_represent_execution_spread(self):
        d={"Time":"2026-10-29T20:10:00Z",
           "OpenBid":1.12,"OpenAsk":1.1202,
           "HighBid":1.123,"HighAsk":1.1232,
           "LowBid":1.119,"LowAsk":1.1192,
           "CloseBid":1.120,"CloseAsk":1.122}
        bar=_bar(d)
        self.assertAlmostEqual(bar.close,1.121)
        # EPE live BID/ASK spread rules are validated separately in
        # tests.test_saxo_private_primary and tests.test_saxo_private_feed.

    def test_historical_sides_still_must_be_positive_and_ordered(self):
        d={"Time":"2026-10-29T20:10:00Z",
           "OpenBid":1.12,"OpenAsk":1.1202,
           "HighBid":1.123,"HighAsk":1.1232,
           "LowBid":1.119,"LowAsk":1.1192,
           "CloseBid":1.122,"CloseAsk":1.120}
        with self.assertRaisesRegex(SaxoChartUnavailable,"invalid_bid_ask_ohlc"):
            _bar(d)

    def test_rejects_absent_ask_and_corrupt_ohlc(self):
        d={"Time":"2026-10-29T20:10:00Z","OpenBid":1.12,"OpenAsk":1.1202}
        with self.assertRaises(SaxoChartUnavailable):
            _bar(d)

    def test_only_private_enabled_runtime(self):
        with patch.dict(os.environ,{"BRIEFROOMS_PRIVATE_RUNTIME":"0",
            "SAXO_PRIVATE_PRIMARY":"1","SAXO_STREAM_PRIMARY":"1",
            "SAXO_CHART_PRIMARY":"1","SAXO_PRIVATE_BASIC_PASSWORD":"test"}):
            with self.assertRaises(SaxoChartUnavailable):
                SaxoPrivateChartClient()

    def test_pages_are_private_and_pinned(self):
        with patch.dict(os.environ,{"BRIEFROOMS_PRIVATE_RUNTIME":"1",
            "SAXO_PRIVATE_PRIMARY":"1","SAXO_STREAM_PRIMARY":"1",
            "SAXO_CHART_PRIMARY":"1","SAXO_PRIVATE_BASIC_PASSWORD":"test"}):
            obj=SaxoPrivateChartClient()
            payload={"source":"SAXO_SIM_CHART","instrument":"EUR/USD",
                "redistributable":False,"trading_enabled":False,
                "delayed_by_minutes":0,"horizon_minutes":30,"data_version":1,"data":[{"Time":"x"}]}
            response=MagicMock()
            response.status=200
            response.read.return_value=json.dumps(payload).encode()
            response.__enter__.return_value=response
            with patch("saxo_private_chart.urllib.request.urlopen",return_value=response) as fetch:
                version,data,count=obj._page(30,1200,None)
            self.assertEqual(version,1)
            self.assertEqual(count,1)
            req=fetch.call_args.args[0]
            self.assertTrue(req.full_url.startswith(CHART_URL+"?"))
            self.assertIn("Authorization",req.headers)
            self.assertNotIn("test",req.full_url)

if __name__=="__main__":
    unittest.main()
