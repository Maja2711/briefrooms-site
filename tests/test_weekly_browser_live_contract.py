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
SCRIPT_REF = "/scripts/investments-weekly-browser-live.js?v=20260916-6"


class WeeklyBrowserLiveContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT, check=True)

    def test_one_authoritative_price_runtime_is_rendered_on_every_weekly_page(self) -> None:
        for path in PAGES:
            html = path.read_text(encoding="utf-8")
            self.assertEqual(html.count(SCRIPT_REF), 1)
            self.assertNotIn("investments-weekly-sp500-minute-live.js", html)
            self.assertNotIn("investments-weekly-es-delayed.js", html)
            self.assertLess(html.index("investments-weekly-public.js"), html.index("investments-weekly-browser-live.js"))
            self.assertLess(html.index("investments-weekly-browser-live.js"), html.index("investments-weekly-trade-times.js"))

    def test_runtime_keeps_last_known_price_instead_of_emitting_stale_state(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/data/investments/live_prices.json", source)
        self.assertIn("CACHE_PREFIX = 'briefrooms:weekly-market-feed:v3:'", source)
        self.assertIn("lastPrice: 'ostatni kurs'", source)
        self.assertIn("delayed: 'OPÓŹNIONY'", source)
        self.assertIn("mode: 'delayed'", source)
        self.assertIn("nowBox.dataset.feedStatus = 'delayed'", source)
        self.assertIn("saveCache(instrumentId, quote)", source)
        self.assertIn("newestQuote(bestDelayed, backend, cached, previous)", source)
        self.assertNotIn("stale: 'STALE'", source)
        self.assertNotIn("feedStatus = 'stale'", source)

    def test_all_three_instruments_have_live_sources_and_same_origin_backend(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("FX mid-market", source)
        self.assertIn("Yahoo EURUSD=X", source)
        self.assertIn("Yahoo ES=F", source)
        self.assertIn("Coinbase BTC-USD", source)
        self.assertIn("CoinGecko BTC/USD", source)
        self.assertIn("pollMs: 15_000", source)
        self.assertIn("pollMs: 60_000", source)
        self.assertIn("maxAgeMs: 2 * 60_000", source)
        self.assertIn("maxAgeMs: 5 * 60_000", source)
        self.assertIn("backendQuotes", source)
        self.assertIn("backendFresh", source)
        self.assertIn("br:weekly-rendered", source)
        self.assertIn("cache: 'no-store'", source)


if __name__ == "__main__":
    unittest.main()
