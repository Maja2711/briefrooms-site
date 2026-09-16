from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "render_weekly_public_pages.py"
LIVE_SCRIPT = ROOT / "scripts" / "investments-weekly-browser-live.js"
PAGES = [
    ROOT / "pl" / "inwestycje" / "pozycje-tygodniowe.html",
    ROOT / "pl" / "inwestycje" / "prognozy-tygodniowe.html",
    ROOT / "en" / "investing" / "open-weekly-positions.html",
    ROOT / "en" / "investing" / "weekly-forecasts.html",
]
SCRIPT_REF = "/scripts/investments-weekly-browser-live.js?v=20260916-1"


class WeeklyBrowserLiveContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT, check=True)

    def test_live_runtime_is_rendered_exactly_once_on_every_weekly_page(self) -> None:
        for path in PAGES:
            html = path.read_text(encoding="utf-8")
            self.assertEqual(
                html.count(SCRIPT_REF),
                1,
                f"{path.relative_to(ROOT)} must include Weekly browser LIVE runtime exactly once",
            )
            self.assertLess(
                html.index("investments-weekly-public.js"),
                html.index("investments-weekly-browser-live.js"),
                f"{path.relative_to(ROOT)} must start LIVE runtime after the base renderer",
            )

    def test_btc_live_feed_has_primary_fallback_polling_and_freshness_guard(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Coinbase BTC-USD", source)
        self.assertIn("CoinGecko BTC/USD", source)
        self.assertIn("pollMs: 15_000", source)
        self.assertIn("maxAgeMs: 2 * 60_000", source)
        self.assertIn("br:weekly-rendered", source)
        self.assertIn("cache: 'no-store'", source)

    def test_sp500_live_feed_prefers_direct_yahoo_before_proxy_fallbacks(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        direct1 = source.index("fetchYahooQuote('ES=F', 'direct1')")
        direct2 = source.index("fetchYahooQuote('ES=F', 'direct2')")
        proxy1 = source.index("fetchYahooQuote('ES=F', 'codetabs')")
        proxy2 = source.index("fetchYahooQuote('ES=F', 'allorigins')")
        self.assertLess(direct1, direct2)
        self.assertLess(direct2, proxy1)
        self.assertLess(proxy1, proxy2)
        self.assertIn("query1.finance.yahoo.com", source)
        self.assertIn("query2.finance.yahoo.com", source)
        self.assertIn("sp500_futures: {\n      pollMs: 15_000,\n      maxAgeMs: 2 * 60_000", source)
        self.assertIn("briefrooms:weekly-market-feed:v3:", source)


if __name__ == "__main__":
    unittest.main()
