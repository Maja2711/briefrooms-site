from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InvestmentRoomResilienceTests(unittest.TestCase):
    def setUp(self):
        self.pl_controller = (ROOT / "scripts" / "portfolio-10k-dashboard.js").read_text(encoding="utf-8")
        self.en_controller = (ROOT / "scripts" / "portfolio-10k-dashboard-en.js").read_text(encoding="utf-8")
        self.pl_page = (ROOT / "pl" / "inwestycje" / "portfel-10k.html").read_text(encoding="utf-8")
        self.en_page = (ROOT / "en" / "investing" / "portfolio-10k.html").read_text(encoding="utf-8")

    def test_bilingual_controllers_remain_identical(self):
        self.assertEqual(self.pl_controller, self.en_controller)

    def test_portfolio_is_not_blocked_by_brace(self):
        self.assertIn("const CONTROLLER_VERSION = 'resilient-v9'", self.pl_controller)
        self.assertNotIn("Promise.allSettled", self.pl_controller)
        self.assertIn("loadBrace();\n    return loadPortfolio();", self.pl_controller)
        self.assertIn("renderPortfolio(portfolio, 'network')", self.pl_controller)

    def test_loading_has_cache_validation_retry_and_no_per_visit_cache_bust(self):
        self.assertIn("readCache('portfolio', validPortfolio)", self.pl_controller)
        self.assertIn("writeCache('portfolio', portfolio)", self.pl_controller)
        self.assertIn("fetchJsonResilient", self.pl_controller)
        self.assertIn("RETRY_DELAYS_MS", self.pl_controller)
        self.assertIn("scheduleBraceRetry", self.pl_controller)
        self.assertIn("data-investment-retry", self.pl_controller)
        self.assertNotIn("stable=${Date.now()}", self.pl_controller)

    def test_pages_pin_the_resilient_controller(self):
        self.assertEqual(self.pl_page.count("portfolio-10k-dashboard.js?v=9"), 1)
        self.assertEqual(self.en_page.count("portfolio-10k-dashboard-en.js?v=9"), 1)
        self.assertLess(
            self.pl_page.index("portfolio-10k-dashboard.js?v=9"),
            self.pl_page.index("portfolio-10k-material-reports-public.js"),
        )
        self.assertLess(
            self.en_page.index("portfolio-10k-dashboard-en.js?v=9"),
            self.en_page.index("portfolio-10k-material-reports-public.js"),
        )

    def test_production_audit_requires_two_complete_passes(self):
        workflow = (ROOT / ".github" / "workflows" / "investment-room-production-audit.yml").read_text(encoding="utf-8")
        audit = (ROOT / "scripts" / "audit_investment_rooms_probe2.py").read_text(encoding="utf-8")
        self.assertIn('cron: "17 */2 * * *"', workflow)
        self.assertIn("consecutive=$((consecutive + 1))", workflow)
        self.assertIn('if [ "$consecutive" -ge 2 ]', workflow)
        self.assertIn('"tabs": [item.get("top"', audit)
        self.assertIn('"sidebar_tabs": [', audit)
        self.assertIn('"language_switch": overview.get', audit)
        self.assertIn("os._exit(code)", audit)
        self.assertIn("EXPECTED_CONTROLLER", audit)
        self.assertIn("ThreadPoolExecutor", audit)
        self.assertIn("subprocess.run", audit)
        self.assertIn("OUTPUT.unlink(missing_ok=True)", audit)

    def test_pr_audit_cannot_reuse_a_stale_report(self):
        workflow = (ROOT / ".github" / "workflows" / "investment-room-pr-browser-audit.yml").read_text(encoding="utf-8")
        self.assertIn("rm -f data/portfolio10k/investment_room_full_audit.json", workflow)
        self.assertIn("investment-room-isolated-audit-v10", workflow)
        self.assertIn("Fresh controlled browser report is missing", workflow)
        self.assertNotIn("continue-on-error: true", workflow)

    def test_browser_audit_uses_real_pointer_clicks_in_isolated_workers(self):
        audit = (ROOT / "scripts" / "audit_investment_rooms_probe2.py").read_text(encoding="utf-8")
        self.assertIn("page.mouse.click(x, y)", audit)
        self.assertIn('f".i10k-tabs [data-tab=\'{tab}\']"', audit)
        self.assertIn('f".i10k-side-nav [data-tab=\'{tab}\']"', audit)
        self.assertIn('"--worker", language, tab', audit)
        self.assertIn('"tournament_cta": agents.get', audit)

    def test_ai_tournament_enhancements_are_event_driven_without_observer_loops(self):
        public = (ROOT / "scripts" / "ai-tournament-public.js").read_text(encoding="utf-8")
        profiles = (ROOT / "scripts" / "ai-tournament-company-profiles.js").read_text(encoding="utf-8")
        summary = (ROOT / "scripts" / "ai-tournament-summary.js").read_text(encoding="utf-8")
        readiness = (ROOT / "scripts" / "ai-tournament-readiness.js").read_text(encoding="utf-8")
        self.assertIn("briefrooms:ai-tournament-rendered", public)
        self.assertIn("BR_AI_TOURNAMENT_DATA", public)
        self.assertIn("briefrooms:ai-tournament-rendered", profiles)
        self.assertIn("briefrooms:ai-tournament-rendered", summary)
        self.assertIn("briefrooms:ai-tournament-rendered", readiness)
        self.assertNotIn("MutationObserver", profiles)
        self.assertNotIn("MutationObserver", summary)
        self.assertEqual(self.pl_page.count("ai-tournament-public.js?v=6"), 1)
        self.assertEqual(self.en_page.count("ai-tournament-public.js?v=6"), 1)


if __name__ == "__main__":
    unittest.main()
