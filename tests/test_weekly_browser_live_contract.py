from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "render_weekly_public_pages.py"
LIVE_SCRIPT = ROOT / "scripts" / "investments-weekly-browser-live.js"
SP500_MINUTE_SCRIPT = ROOT / "scripts" / "investments-weekly-sp500-minute-live.js"
PAGES = [
    ROOT / "pl" / "inwestycje" / "pozycje-tygodniowe.html",
    ROOT / "pl" / "inwestycje" / "prognozy-tygodniowe.html",
    ROOT / "en" / "investing" / "open-weekly-positions.html",
    ROOT / "en" / "investing" / "weekly-forecasts.html",
]
SCRIPT_REF = "/scripts/investments-weekly-browser-live.js?v=20260915-3"
SP500_MINUTE_REF = "/scripts/investments-weekly-sp500-minute-live.js?v=20260916-4"


class WeeklyBrowserLiveContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT, check=True)

    def test_live_runtimes_are_rendered_exactly_once_on_every_weekly_page(self) -> None:
        for path in PAGES:
            html = path.read_text(encoding="utf-8")
            self.assertEqual(html.count(SCRIPT_REF), 1)
            self.assertEqual(html.count(SP500_MINUTE_REF), 1)
            self.assertLess(html.index("investments-weekly-public.js"), html.index("investments-weekly-browser-live.js"))
            self.assertLess(html.index("investments-weekly-browser-live.js"), html.index("investments-weekly-sp500-minute-live.js"))

    def test_btc_live_feed_has_primary_fallback_polling_and_freshness_guard(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Coinbase BTC-USD", source)
        self.assertIn("CoinGecko BTC/USD", source)
        self.assertIn("pollMs: 15_000", source)
        self.assertIn("maxAgeMs: 2 * 60_000", source)
        self.assertIn("br:weekly-rendered", source)
        self.assertIn("cache: 'no-store'", source)

    def test_minute_overlay_uses_same_origin_backend_and_external_fallbacks(self) -> None:
        source = SP500_MINUTE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/data/investments/live_prices.json", source)
        self.assertIn("BriefRooms backend", source)
        self.assertIn("symbol: 'eurusd'", source)
        self.assertIn("Stooq EUR/USD", source)
        self.assertIn("symbol: 'es.f'", source)
        self.assertIn("Stooq ES.F", source)
        self.assertIn("symbol: 'BTC-USD'", source)
        self.assertIn("Coinbase BTC-USD", source)
        self.assertIn("CoinGecko BTC/USD", source)
        self.assertIn("POLL_MS = 30_000", source)
        self.assertIn("BACKEND_MAX_AGE_MS = 10 * 60_000", source)
        self.assertIn("EXTERNAL_MAX_AGE_MS = 5 * 60_000", source)
        self.assertIn("WARSAW_TZ = 'Europe/Warsaw'", source)
        self.assertIn("warsawLocalToUtc", source)
        self.assertNotIn("T${clock}+01:00", source)
        self.assertIn("['direct', 'codetabs', 'allorigins']", source)
        self.assertIn("MutationObserver", source)
        self.assertIn("· LIVE${fallbackLabel} ·", source)
        self.assertIn("currentStatus === 'live' || currentStatus === 'fallback'", source)
        self.assertIn("Same-origin JSON is the primary path", source)


if __name__ == "__main__":
    unittest.main()
