from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GpwDailyPickUiTests(unittest.TestCase):
    def test_market_anchor_exists_on_each_localized_portfolio_page(self):
        polish = (ROOT / "pl/inwestycje/portfel-10k.html").read_text(encoding="utf-8")
        english = (ROOT / "en/investing/portfolio-10k.html").read_text(encoding="utf-8")
        self.assertEqual(polish.count('id="gpw-daily-pick-root"'), 1)
        self.assertIn("/scripts/gpw-daily-pick-public.js", polish)
        self.assertEqual(english.count('id="us-daily-stock-root"'), 1)
        self.assertIn("/scripts/us-daily-stock-public.js", english)

    def test_legacy_market_clients_boot_shared_renderer(self):
        gpw = (ROOT / "scripts/gpw-daily-pick-public.js").read_text(encoding="utf-8")
        us = (ROOT / "scripts/us-daily-stock-public.js").read_text(encoding="utf-8")
        for script in (gpw, us):
            self.assertIn("daily-stock-markets-public.js", script)
            self.assertIn("daily-stock-markets.css", script)
            self.assertIn("__BR_DAILY_STOCK_MARKETS_BOOTSTRAP__", script)

    def test_shared_client_loads_both_feeds_and_localizes_market_visibility(self):
        script = (ROOT / "scripts/daily-stock-markets-public.js").read_text(encoding="utf-8")
        self.assertIn("/data/investments/gpw_daily_pick.json", script)
        self.assertIn("/data/investments/us_daily_stock.json", script)
        self.assertIn("/data/investments/gpw_daily_pick_history_index.json", script)
        self.assertIn("/data/investments/us_daily_stock_history/index.json", script)
        self.assertIn("DAILY TRADE — GPW + USA", script)
        self.assertIn('title: "US DAILY STOCK"', script)
        self.assertIn("Rynek polski (GPW)", script)
        self.assertIn("US market", script)
        self.assertIn('if (lang === "en")', script)
        self.assertIn('cache: "no-store"', script)

    def test_active_position_overlay_keeps_open_gpw_visible_and_history_closed_only(self):
        script = (ROOT / "scripts/daily-stock-position-ux.js").read_text(encoding="utf-8")
        polish = (ROOT / "pl/inwestycje/daily-trading.html").read_text(encoding="utf-8")
        english = (ROOT / "en/investing/daily-trading.html").read_text(encoding="utf-8")
        css = (ROOT / "assets/daily-trading-contrast.css").read_text(encoding="utf-8")

        self.assertIn("/data/investments/gpw_daily_pick_history_index.json", script)
        self.assertIn('status: "W TOKU"', script)
        self.assertIn("row?.outcome?.activated === true", script)
        self.assertIn("trade?.outcome?.entry_price", script)
        self.assertIn("rows.filter(isResolved)", script)
        self.assertIn("outcome.entry_price", script)
        self.assertIn("outcome.exit_price", script)
        self.assertIn("Historia zamkniętych transakcji", script)
        self.assertIn("daily-stock-position-ux.js?v=1", polish)
        self.assertIn("daily-stock-position-ux.js?v=1", english)
        self.assertNotIn("daily-us-open-position-public.js", polish)
        self.assertNotIn("daily-us-open-position-public.js", english)
        self.assertIn(".dsm-history-row.dsm-history-row-closed", css)
        self.assertIn("font-size:11px", css)

    def test_shared_client_keeps_market_specific_currency_and_session_context(self):
        script = (ROOT / "scripts/daily-stock-markets-public.js").read_text(encoding="utf-8")
        self.assertIn('currency: market === "gpw" ? "PLN" : "USD"', script)
        self.assertIn("09:05 Warszawa", script)
        self.assertIn("09:35 ET", script)
        self.assertIn("ESPI/EBI", script)
        self.assertIn("SEC/company releases", script)

    def test_polish_view_prefers_localized_us_thesis_and_news_summaries(self):
        script = (ROOT / "scripts/daily-stock-markets-public.js").read_text(encoding="utf-8")
        self.assertIn('lang === "pl" && market === "us"', script)
        self.assertIn("selection?.localized?.pl", script)
        self.assertIn("localization?.thesis", script)
        self.assertIn("localization?.why_now", script)
        self.assertIn("localization?.activation", script)
        self.assertIn("localization?.source_summaries", script)

        adapter = (ROOT / "scripts/daily_stock_us_adapter.py").read_text(encoding="utf-8")
        self.assertIn("_polish_localization", adapter)
        self.assertIn("source_summaries", adapter)
        self.assertIn("financial translator", adapter)
        self.assertIn("us.publish = _publish", adapter)

    def test_gpw_runtime_installs_shared_core_before_preserved_event_layers(self):
        workflow = (ROOT / ".github/workflows/gpw-daily-pick-pl.yml").read_text(encoding="utf-8")
        self.assertIn('GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}', workflow)
        self.assertNotIn("OPENAI_API_KEY", workflow)
        self.assertIn("scripts/gpw_event_driven_loop.py", workflow)
        self.assertIn("data/investments/gpw_daily_pick_learning.json", workflow)
        self.assertIn("data/investments/gpw_daily_pick_history", workflow)
        self.assertNotIn("git add .", workflow)

        runtime = (ROOT / "scripts/gpw_event_driven_loop.py").read_text(encoding="utf-8")
        self.assertLess(runtime.index("core_adapter.install()"), runtime.index("_ORIGINAL_BUILD_QUANT_CANDIDATE"))

        adapter = (ROOT / "scripts/daily_stock_gpw_adapter.py").read_text(encoding="utf-8")
        self.assertIn("legacy_learning_preserved", adapter)
        self.assertIn("event_learning_preserved", adapter)
        self.assertIn("gpw.history_expectancy_score", adapter)
        self.assertIn("gpw_event_driven_loop", adapter)

    def test_us_runtime_installs_shared_core_and_publishes_history_index(self):
        runtime = (ROOT / "scripts/us_daily_stock_runtime.py").read_text(encoding="utf-8")
        self.assertIn("core_adapter.install()", runtime)
        self.assertIn('us.HISTORY_DIR / "index.json"', runtime)
        adapter = (ROOT / "scripts/daily_stock_us_adapter.py").read_text(encoding="utf-8")
        self.assertIn("SEC 8-K", adapter)
        self.assertIn("company_release", adapter)
        self.assertIn("core.bayesian_history_expectancy_score", adapter)


if __name__ == "__main__":
    unittest.main()
