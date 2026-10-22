"""Static regression checks for the Stock Trading market-selector UI.

These checks require no live portfolio data or browser and are safe in CI.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "assets/stock-market-tabs-glass-3d.css"
PAGES = [
    ROOT / "pl/inwestycje/stock-trading.html",
    ROOT / "en/investing/stock-trading.html",
]
RENDERER = ROOT / "scripts/stock-trading-public.js"
VERSION = "stock-market-tabs-glass-3d.css?v=20261022-1"


def css_block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", css)
    if match is None:
        raise AssertionError(f"Missing CSS selector: {selector}")
    return match.group(1)


class StockMarketSelectorUITests(unittest.TestCase):
    def test_market_buttons_are_independent(self):
        css = CSS.read_text(encoding="utf-8")
        frame = css_block(css, ".stock-trading-page .str-market-tabs")
        self.assertIn("gap: 14px", frame)
        self.assertIn("overflow: visible", frame)
        self.assertIn("border: 0", frame)
        self.assertIn("background: transparent", frame)
        self.assertIn("border-radius: 14px", css)
        self.assertIn(".str-market-tab.is-active", css)
        self.assertIn(".str-market-tab:hover", css)
        self.assertIn(".str-market-tab:focus-visible", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("max-width: 620px", css)
        self.assertIn("gap: 12px", css)

    def test_both_languages_load_same_new_stylesheet(self):
        for page in PAGES:
            with self.subTest(page=page.name, lang=page.parent.name):
                html = page.read_text(encoding="utf-8")
                self.assertIn(VERSION, html)

    def test_existing_market_navigation_stays_semantic(self):
        js = RENDERER.read_text(encoding="utf-8")
        self.assertIn('data-market-jump="${market}"', js)
        self.assertIn("aria-pressed=", js)
        self.assertIn("state.selectedMarket = btn.dataset.marketJump", js)
        self.assertIn("marketTab('GPW')", js)
        self.assertIn("marketTab('US')", js)


if __name__ == "__main__":
    unittest.main()
