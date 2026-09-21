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
SCRIPT_REF = "/scripts/investments-weekly-browser-live.js?v=20260921-5"
COMPACT_REF = "/scripts/investments-weekly-price-compact.js?v=20260916-4"


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

    def test_browser_runtime_keeps_s_and_p_delayed_quote_visible_and_labeled(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/data/investments/live_prices.json", source)
        self.assertIn("CACHE_PREFIX = 'briefrooms:weekly-market-feed:v6:'", source)
        self.assertIn("mode: 'delayed'", source)
        self.assertIn("saveCache(instrumentId, finalQuote)", source)
        self.assertIn("opóźniony ~10 min", source)
        self.assertIn("delayed ~10 min", source)
        self.assertIn("maxAgeMs: 15 * 60_000", source)
        self.assertIn("backendMaxAgeMs: 15 * 60_000", source)

    def test_btc_sources_are_requested_in_parallel_and_freshest_quote_wins(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Coinbase BTC-USD", source)
        self.assertIn("CoinGecko BTC/USD", source)
        self.assertIn("Promise.allSettled(cfg.sources.map", source)
        self.assertIn("const preferredFreshDirect =", source)
        self.assertIn("pollMs: 15_000", source)
        self.assertIn("REQUEST_TIMEOUT_MS = 6_000", source)
        self.assertIn("backendMaxAgeMs: 15 * 60_000", source)

    def test_backend_refresh_does_not_block_direct_market_round(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("BACKEND_POLL_MS = 60_000", source)
        self.assertIn("void refreshBackend()", source)
        self.assertIn("feedRoundInFlight", source)
        self.assertIn("backendInFlight", source)
        self.assertIn("cache: 'no-store'", source)
        self.assertIn("br:weekly-rendered", source)

    def test_compact_presenter_only_shows_publication_timestamp(self) -> None:
        source = COMPACT_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("function compactTime", source)
        self.assertIn("fmtStamp(liveAt)", source)
        self.assertIn("replace(',', ' ·')", source)
        self.assertIn("MutationObserver", source)
        self.assertNotIn("opóźniony", source.lower())
        self.assertNotIn("delayed", source.lower())
        self.assertNotIn("Coinbase", source)
        self.assertNotIn("CoinGecko", source)
        self.assertNotIn("fetch(", source)

    def test_all_three_instruments_keep_live_sources_and_same_origin_backend(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("https://fxapi.app/api/EUR/USD.json", source)
        self.assertIn("fxapi.app", source)
        self.assertIn("Currency Exchange Tool", source)
        self.assertNotIn("Yahoo EURUSD=X", source)
        self.assertLess(source.index("{ name: 'fxapi.app'"), source.index("{ name: 'Currency Exchange Tool'"))
        self.assertIn("directPriority: 'first-fresh'", source)
        self.assertIn("directAuthoritativeWhenFresh: true", source)
        self.assertIn("for (let index = 0; index < cfg.sources.length; index += 1)", source)
        self.assertIn("if (quoteFresh(quote, cfg.maxAgeMs)) return attempts;", source)
        self.assertIn("['live', 'fallback'].includes(state?.mode)", source)
        self.assertIn("Stooq ES.F", source)
        self.assertIn("Yahoo ES=F", source)
        self.assertIn("fetchStooqEs", source)
        self.assertIn("Coinbase BTC-USD", source)
        self.assertIn("CoinGecko BTC/USD", source)
        self.assertIn("pollMs: 60_000", source)
        self.assertIn("maxAgeMs: 10 * 60_000", source)
        self.assertIn("maxAgeMs: 2 * 60_000", source)
        self.assertIn("maxAgeMs: 15 * 60_000", source)
        self.assertIn("backendMaxAgeMs: 15 * 60_000", source)
        self.assertNotIn("backendMaxAgeMs: 45 * 60_000", source)
        self.assertIn("backendQuotes", source)
        self.assertIn("backendFresh", source)

    def test_sp500_browser_feed_busts_upstream_cache_and_rejects_stale_quotes(self) -> None:
        source = LIVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("https://stooq.com/q/l/?s=es.f", source)
        self.assertIn("range=1d&_=${Date.now()}", source)
        self.assertIn("No fresh market quote", source)
        self.assertIn("Brak świeżej ceny rynkowej", source)
        self.assertIn("opóźniony ~10 min", source)
        self.assertIn("priceNode.textContent", source)

    def test_server_snapshot_uses_same_eurusd_provider_chain_as_daily(self) -> None:
        source = FAST_UPDATER.read_text(encoding="utf-8")
        self.assertIn("def fxapi_eurusd_quote()", source)
        self.assertIn("https://fxapi.app/api/EUR/USD.json", source)
        self.assertIn("def currency_exchange_tool_eurusd_quote()", source)
        self.assertIn("https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD", source)
        self.assertIn('if instrument_id == "eurusd":', source)
        self.assertIn("fxapi_eurusd_quote", source)
        self.assertIn("currency_exchange_tool_eurusd_quote", source)
        self.assertIn('if instrument_id == "eurusd":\n                    age = quote_age(quote)', source)
        self.assertIn('if instrument_id == "eurusd" and candidate_fresh:', source)

    def test_server_snapshot_chooses_newest_es_provider_by_timestamp(self) -> None:
        source = FAST_UPDATER.read_text(encoding="utf-8")
        self.assertIn("active_es_contract", source)
        self.assertIn("active_es_yahoo_symbol", source)
        self.assertIn("esignal_quote", source)
        self.assertIn("eSignal delayed", source)
        self.assertNotIn("lambda: yahoo_quote(instrument_id, active_es_yahoo_symbol())", source)
        self.assertIn("lambda: yahoo_quote(instrument_id)", source)
        self.assertIn("lambda: stooq_quote(instrument_id)", source)
        self.assertIn("candidates.sort", source)
        self.assertIn("reverse=True", source)
        self.assertIn("timedelta(minutes=15)", source)
        self.assertNotIn("timedelta(minutes=45)", source)


if __name__ == "__main__":
    unittest.main()
