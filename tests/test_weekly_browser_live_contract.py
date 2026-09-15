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
SCRIPT_REF = "/scripts/investments-weekly-browser-live.js?v=20260915-3"


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


if __name__ == "__main__":
    unittest.main()
