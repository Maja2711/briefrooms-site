from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "pl": ROOT / "pl" / "inwestycje" / "portfel-10k.html",
    "en": ROOT / "en" / "investing" / "portfolio-10k.html",
}
EXPECTED_LABELS = {
    "pl": "Analiza BRACE",
    "en": "BRACE Analytics",
}
EXPECTED_ORDER = [
    "overview",
    "portfolio",
    "benchmark",
    "agents",
    "analytics",
    "history",
    "rules",
]


class NavigationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nav_stack: list[str | None] = []
        self.buttons: dict[str, list[tuple[str, str]]] = {
            "i10k-side-nav": [],
            "i10k-tabs": [],
        }
        self.current_button: tuple[str, str] | None = None
        self.button_text: list[str] = []
        self.panels: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "nav":
            nav_class = next((name for name in self.buttons if name in classes), None)
            self.nav_stack.append(nav_class)
        elif tag == "button" and self.nav_stack and self.nav_stack[-1]:
            tab = values.get("data-tab")
            if tab:
                self.current_button = (self.nav_stack[-1] or "", tab)
                self.button_text = []
        elif tag == "section" and "i10k-panel" in classes:
            panel = values.get("data-panel")
            if panel:
                self.panels.append(panel)

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self.current_button:
            nav_class, tab = self.current_button
            label = " ".join("".join(self.button_text).split())
            self.buttons[nav_class].append((tab, label))
            self.current_button = None
            self.button_text = []
        elif tag == "nav" and self.nav_stack:
            self.nav_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.current_button:
            self.button_text.append(data)


class PortfolioNavigationTests(unittest.TestCase):
    def test_pl_and_en_share_the_simplified_navigation(self) -> None:
        for language, path in PAGES.items():
            with self.subTest(language=language):
                html = path.read_text(encoding="utf-8")
                parser = NavigationParser()
                parser.feed(html)

                for nav_class in ("i10k-side-nav", "i10k-tabs"):
                    tabs = parser.buttons[nav_class]
                    self.assertEqual([tab for tab, _ in tabs], EXPECTED_ORDER)
                    analytics = [label for tab, label in tabs if tab == "analytics"]
                    self.assertEqual(len(analytics), 1)
                    self.assertIn(EXPECTED_LABELS[language], analytics[0])

                self.assertEqual(parser.panels, EXPECTED_ORDER)
                self.assertNotIn('data-tab="brace"', html)
                self.assertNotIn('data-panel="brace"', html)

    def test_brace_and_portfolio_analytics_share_one_panel(self) -> None:
        for language, path in PAGES.items():
            with self.subTest(language=language):
                html = path.read_text(encoding="utf-8")
                analytics = html.index('data-panel="analytics"')
                brace = html.index('id="brace-control-root"')
                portfolio_kpis = html.index('id="kpis"')
                history = html.index('data-panel="history"')
                rules = html.index('data-panel="rules"')
                self.assertLess(analytics, brace)
                self.assertLess(brace, portfolio_kpis)
                self.assertLess(portfolio_kpis, history)
                self.assertLess(history, rules)

    def test_legacy_brace_hash_is_redirected_to_analytics(self) -> None:
        guard = (ROOT / "scripts" / "portfolio-10k-navigation-guard.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const TAB_ALIASES = Object.freeze({ brace: 'analytics' });", guard)
        self.assertIn("name = normalizeTab(name);", guard)

    def test_misleading_brace_activity_metrics_are_not_rendered(self) -> None:
        control = (ROOT / "scripts" / "portfolio-10k-control-public.js").read_text(
            encoding="utf-8"
        )
        for hidden_copy in (
            "Okres działania",
            "Operating period",
            "Automatyczne bramki jakości",
            "Automatic quality gates",
            "BRACE paper",
            "completed paper trades",
        ):
            self.assertNotIn(hidden_copy, control)
        self.assertNotIn("metric(T.period", control)
        self.assertNotIn('<section class="control-progress">', control)

        metrics = re.search(
            r'<div class="control-metrics">(.*?)</div>', control, flags=re.DOTALL
        )
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.group(1).count("${metric("), 4)

        css = (ROOT / "assets" / "portfolio-10k.css").read_text(encoding="utf-8")
        self.assertIn(
            ".control-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))",
            css,
        )
        for path in PAGES.values():
            html = path.read_text(encoding="utf-8")
            self.assertIn("portfolio-10k.css?v=5", html)
            self.assertIn("portfolio-10k-control-public.js?v=4", html)


if __name__ == "__main__":
    unittest.main()
