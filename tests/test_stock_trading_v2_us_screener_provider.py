from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import stock_trading_v2_us_screener_provider as provider


class StockTradingV2USScreenerProviderTests(unittest.TestCase):
    def test_paged_fetch_collects_complete_market(self):
        pages = {
            0: {"data": {"totalrecords": "3", "asOf": "now", "rows": [{"symbol": "AAA"}, {"symbol": "BBB"}]}},
            2: {"data": {"totalrecords": "3", "asOf": "now", "rows": [{"symbol": "CCC"}]}},
        }

        def fake_request(params, *, timeout):
            del timeout
            return pages[int(params.get("offset", 0))]

        with patch.object(provider, "_request_json", side_effect=fake_request):
            rows, meta = provider.fetch_rows(timeout=1, attempts=1, page_size=2, max_pages=3)

        self.assertEqual({row["symbol"] for row in rows}, {"AAA", "BBB", "CCC"})
        self.assertTrue(meta["complete"])
        self.assertEqual(meta["rows_received"], 3)
        self.assertEqual(meta["mode"], "paged_with_retry")

    def test_duplicate_symbol_does_not_fake_completeness(self):
        pages = {
            0: {"data": {"totalrecords": "3", "rows": [{"symbol": "AAA"}, {"symbol": "BBB"}]}},
            2: {"data": {"totalrecords": "3", "rows": [{"symbol": "BBB"}, {"symbol": "BBB"}]}},
        }

        def fake_request(params, *, timeout):
            del timeout
            return pages[int(params.get("offset", 0))]

        with patch.object(provider, "_request_json", side_effect=fake_request):
            with self.assertRaises(provider.ProviderUnavailable):
                provider.fetch_rows(timeout=1, attempts=1, page_size=2, max_pages=2)

    def test_first_page_failure_uses_full_download_fallback(self):
        def fake_request(params, *, timeout):
            del timeout
            if params.get("download") == "true":
                return {"data": {"totalrecords": "2", "rows": [{"symbol": "AAA"}, {"symbol": "BBB"}]}}
            raise TimeoutError("simulated timeout")

        with patch.object(provider, "_request_json", side_effect=fake_request):
            rows, meta = provider.fetch_rows(timeout=1, attempts=1, page_size=2, max_pages=2)

        self.assertEqual(len(rows), 2)
        self.assertEqual(meta["mode"], "full_download_fallback")
        self.assertTrue(meta["complete"])
        self.assertTrue(meta["failures"])


if __name__ == "__main__":
    unittest.main()
