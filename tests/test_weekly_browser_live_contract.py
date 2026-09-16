from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "render_weekly_public_pages.py"
LIVE_SCRIPT = ROOT / "scripts" / "investments-weekly-browser-live.js"
COMPACT_SCRIPT = ROOT / "scripts" / "investments-weekly-price-compact.js"
FAST_UPDATER = ROOT / "scripts" / "update_weekly_live_prices_fast.py"
PAGES = [
    ROOT / "pl" / "inwestycje" / "pozycje-tygodniowe.html",
    ROOT / "pl" / "inwestycje" / "prognozy-tygodniowe.html",
    ROOT / "en" / "investing" / "open-weekly-positions.html",
    ROOT / "en" / "investing" / "weekly-forecasts.html",
]
SCRIPT_REF = "/scripts/investments-weekly-browser-live.js?v=20260916-6"
COMPACT_REF = "/scripts/investments-weekly-price-compact.js?v=20260916-3"


class WeeklyBrowserLiveContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([sys.executable, str(GENERATOR)], cwd=ROOT, check=True)

    def test_authoritative_price_runtime_and_compact_presenter_are_rendered(self) -> None:
        for path in PAGES:
            html = path.read_text(encoding="utf-8")
            self.assertEqual(html.count(SCRIPT_REF), 1)
            self.assertEqual(html.count(COMPACT_REF), 1)
            self.assertNotIn("investments-weekly-sp500-minute-live.js", html)
            self.assertNotIn("investments-weekly-es-delayed.js", html)
            self.assertLess(html.index("investments-weekly-public.js"), html.index("investments-weekly-browser-live.js"))
            self.assertLess(html.index("investments-weekly-browser-live.js"), html.index("investments-weekly-price-compact.js"))
            self.assertLess(html.index("investments-weekly-price-compact.js"), html.index("investments-weekly-trade-times.js"))

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

    def test_compact_presenter_reuses_daily_eurusd_source_and_short_metadata(self) -> None:
        source = COMPACT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD", source)
        self.assertIn("EUR_REFRESH_MS = 60_000", source)
        self.assertIn("EUR_LIVE_MAX_AGE_MS = 5 * 60_000", source)
        self.assertIn("data.updatedAt ? new Date(data.updatedAt) : new Date()", source)
        self.assertIn("replace(',', ' ·')", source)
        self.assertIn("opóźniony", source)
        self.assertNotIn("ostatni kurs", source)
        self.assertNotIn("STALE", source)

    def test_sp500_overlay_selects_freshest_quote_instead_of_forcing_active_yahoo(self) -> None:
        source = COMPACT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("ES_REFRESH_MS = 15_000", source)
        self.assertIn("activeEsContract", source)
        self.assertIn("thirdFridayUtc", source)
        self.assertIn("8 * 24 * 60 * 60 * 1000", source)
        self.assertIn("eSignal", source)
        self.assertIn("fetchEsignalEs", source)
        self.assertIn("fetchYahooEsSymbol(contract.yahoo)", source)
        self.assertIn("fetchYahooEsSymbol('ES=F')", source)
        self.assertIn("fetchBackendEs()", source)
        self.assertIn("newestQuote(...quotes)", source)
        self.assertIn("ES_USABLE_MAX_AGE_MS = 45 * 60_000", source)
        self.assertIn("ES_DISPLAY_DELAY_MS = 5 * 60_000", source)
        self.assertIn("if (existingAt > quoteAt) return", source)
        self.assertNotIn("force = false", source)
        self.assertNotIn("true);\n    } catch (error)", source)

    def test_server_snapshot_chooses_newest_es_provider_by_timestamp(self) -> None:
        source = FAST_UPDATER.read_text(encoding="utf-8")
        self.assertIn("active_es_contract", source)
        self.assertIn("active_es_yahoo_symbol", source)
        self.assertIn("esignal_quote", source)
        self.assertIn("eSignal delayed", source)
        self.assertIn("lambda: yahoo_quote(instrument_id, active_es_yahoo_symbol())", source)
        self.assertIn("lambda: yahoo_quote(instrument_id)", source)
        self.assertIn("lambda: stooq_quote(instrument_id)", source)
        self.assertIn("candidates.sort", source)
        self.assertIn("reverse=True", source)
        self.assertNotIn("candidate_is_active_es", source)
        self.assertNotIn("return active_quote, errors", source)


if __name__ == "__main__":
    unittest.main()
