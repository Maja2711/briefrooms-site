import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }


def owners(marker: str) -> list[str]:
    return sorted(
        name
        for name, source in workflow_sources().items()
        if marker in source and ("git add" in source or "git -C" in source)
    )


def push_path_owners(marker: str) -> list[str]:
    matching = []
    for name, source in workflow_sources().items():
        trigger_block = source.split("\npermissions:", 1)[0]
        paths = set(re.findall(r'^\s+- "([^"]+)"$', trigger_block, re.MULTILINE))
        if marker in paths:
            matching.append(name)
    return sorted(matching)


class AutomationWorkflowOwnershipTests(unittest.TestCase):
    def test_single_owners_for_publication_outputs(self) -> None:
        expected = {
            "data/news_publication_status.json": ["publish-news.yml"],
            "data/investments/daily_market_alert.json": ["daily-market-alert.yml"],
            "data/hot_tweets.json": ["hot-x-topics.yml"],
            "data/public/brace_spx_generation3_public.json": [
                "brace-spx-recovery-engine.yml"
            ],
        }
        for marker, expected_owners in expected.items():
            with self.subTest(marker=marker):
                self.assertEqual(expected_owners, owners(marker))

    def test_portfolio_writers_share_one_controlled_queue(self) -> None:
        expected = {
            "portfolio-10k-hourly-prices.yml",
            "portfolio-10k-live-entry.yml",
            "portfolio-10k-weekly.yml",
        }
        actual = set(owners("data/investments/portfolio_10k.json"))
        self.assertEqual(expected, actual)
        sources = workflow_sources()
        for owner in actual:
            self.assertIn("group: portfolio-market-data", sources[owner])
            self.assertIn("cancel-in-progress: false", sources[owner])
            self.assertIn("ref: main", sources[owner])

    def test_portfolio_push_paths_have_single_validation_owners(self) -> None:
        expected = {
            "scripts/portfolio_10k_material_reports.py": [
                "portfolio-10k-weekly.yml"
            ],
            "tests/test_portfolio_10k_staged_entry.py": [
                "portfolio-10k-live-entry.yml"
            ],
        }
        for path, workflows in expected.items():
            with self.subTest(path=path):
                self.assertEqual(workflows, push_path_owners(path))

    def test_domain_queues_are_isolated(self) -> None:
        sources = workflow_sources()
        expected_groups = {
            "publish-news.yml": "news-publication",
            "daily-market-alert.yml": "investment-alert",
            "hot-x-topics.yml": "social-content",
            "portfolio-10k-brace.yml": "brace-portfolio-research",
            "brace-spx-recovery-engine.yml": "brace-spx-research",
            "automation-health-audit.yml": "automation-health-audit",
        }
        for workflow, group in expected_groups.items():
            with self.subTest(workflow=workflow):
                self.assertIn(f"group: {group}", sources[workflow])
                self.assertIn("cancel-in-progress: false", sources[workflow])

    def test_health_audit_observes_but_never_retries_publishers(self) -> None:
        sources = workflow_sources()
        audit = sources["automation-health-audit.yml"]
        self.assertIn('cron: "42 * * * *"', audit)
        self.assertIn("actions: read", audit)
        self.assertIn("issues: write", audit)
        self.assertIn('"scripts/automation_health_audit.py"', audit)
        self.assertNotIn("workflow run", audit)
        self.assertFalse((WORKFLOWS / "content-update-watchdog.yml").exists())
        self.assertFalse((WORKFLOWS / "publish-news-recovery-now.yml").exists())

    def test_canonical_publishers_do_not_use_destructive_rebase_resolution(self) -> None:
        sources = workflow_sources()
        for workflow in (
            "publish-news.yml",
            "daily-market-alert.yml",
            "portfolio-10k-hourly-prices.yml",
            "portfolio-10k-live-entry.yml",
            "portfolio-10k-weekly.yml",
            "automation-health-audit.yml",
        ):
            with self.subTest(workflow=workflow):
                self.assertNotIn("-X theirs", sources[workflow])
                self.assertNotIn("git add -A", sources[workflow])

    def test_health_only_pushes_do_not_recursively_trigger_a_pages_deploy(self) -> None:
        deploy = workflow_sources()["deploy-production.yml"]
        self.assertIn("paths-ignore:", deploy)
        self.assertIn('      - "data/system/**"', deploy)

    def test_action_publishers_trigger_a_pages_deploy(self) -> None:
        deploy = workflow_sources()["deploy-production.yml"]
        for workflow_name in (
            "Publish PL and EN News",
            "Update Hot X Topics",
            "Daily Market Alert",
            "Refresh Portfolio 10K Hourly Prices",
            "Open Fresh 10K Positions",
            "Update 10K Model Portfolio",
            "Build Governed Investments Weekly Forecasts",
            "Governed Weekly Paper Exposure Watch",
            "Weekly Freshness Watchdog",
            "Update Investment Room Quotes",
            "Publish EN YouTube Recommendations",
            "Audit Automation Health",
            "BRACE Portfolio Daily Learning",
            "BRACE Portfolio Hourly Safety Monitor",
            "BRACE Portfolio Research and Promotion",
            "BRACE-SPX Research Engine",
            "BRACE-SPX Public Panel",
        ):
            with self.subTest(workflow_name=workflow_name):
                self.assertIn(f'      - "{workflow_name}"', deploy)

    def test_weekly_schedule_runs_full_lifecycle_and_blocks_weekend_exposure(self) -> None:
        weekly = workflow_sources()["investments-weekly.yml"]
        self.assertIn('default: "auto"', weekly)
        self.assertIn("inputs.mode || 'auto'", weekly)
        self.assertNotIn("inputs.mode || 'ensure-exposure'", weekly)
        self.assertIn("Verify weekly position lifecycle", weekly)
        self.assertIn("python scripts/verify_weekly_close_deadline.py", weekly)
        self.assertIn("cancel-in-progress: false", weekly)
        self.assertIn("tests.test_automation_workflow_ownership.AutomationWorkflowOwnershipTests.test_weekly_schedule_runs_full_lifecycle_and_blocks_weekend_exposure", weekly)

    def test_weekly_settlement_paths_are_independent_queued_and_fail_closed(self) -> None:
        sources = workflow_sources()
        expected_owners = {
            "investments-exposure-watch.yml",
            "investments-weekly.yml",
            "investments-wes.yml",
            "investments-weekly-freshness-watchdog.yml",
        }
        actual_owners = set(owners("git add data/investments/weekly \\"))
        self.assertEqual(expected_owners, actual_owners)
        self.assertFalse((WORKFLOWS / "investments-w32-emergency.yml").exists())

        for workflow_name in expected_owners:
            source = sources[workflow_name]
            with self.subTest(workflow=workflow_name):
                self.assertIn("group: investment-weekly-positions", source)
                self.assertIn("cancel-in-progress: false", source)
                self.assertIn("ref: main", source)
                self.assertIn("python scripts/verify_weekly_close_deadline.py", source)

        exposure = sources["investments-exposure-watch.yml"]
        self.assertIn('cron: "*/5 * * * *"', exposure)
        self.assertIn("Evaluate frozen SL/TP from canonical market evidence", exposure)
        self.assertIn("python scripts/audit_intraday_risk_exits.py --persist-report on_close", exposure)
        self.assertIn("Govern re-entry state after a risk exit", exposure)
        self.assertIn("Persist risk exit atomically through NO RETROACTIVE airlock", exposure)
        self.assertNotIn("pip install", exposure)
        self.assertNotIn("ensure-exposure", exposure)

    def test_wes_1_1_directional_admission_is_mandatory_and_single_path(self) -> None:
        sources = workflow_sources()
        wes = sources["investments-wes.yml"]
        self.assertLess(
            wes.index("WES preflight trigger gate"),
            wes.index("Execute governed v5 decision after WES admission gate"),
        )
        v5 = (ROOT / "scripts" / "investments_weekly_v5.py").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "investments_wes_runner.py").read_text(encoding="utf-8")
        policy = (ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json").read_text(encoding="utf-8")
        self.assertIn("choose_governed", v5)
        self.assertIn("wes_authorization_matches", v5)
        self.assertIn("directional_admission_passed", runner)
        self.assertIn('"version": "WES-1.2.0"', policy)
        self.assertIn('"challenger_shadow_methods"', policy)
        self.assertIn('"inverse_v2"', policy)

    def test_presentation_and_live_price_changes_never_trigger_position_execution(self) -> None:
        sources = workflow_sources()
        execution_workflows = (
            "investments-weekly.yml",
            "investments-wes.yml",
            "investments-exposure-watch.yml",
        )
        forbidden_push_paths = (
            "scripts/render_weekly_public_pages.py",
            "scripts/render_simple_investments.py",
            "scripts/investments-weekly-public.js",
            "scripts/investments-weekly-governance.js",
            "scripts/investments-weekly-browser-live.js",
            "scripts/update_weekly_live_prices_fast.py",
            "tests/test_investments_weekly_public.js",
            "tests/test_weekly_browser_live_contract.py",
            "docs/weekly_trading_methodology_v4.md",
        )
        for workflow_name in execution_workflows:
            trigger_block = sources[workflow_name].split("\npermissions:", 1)[0]
            for path in forbidden_push_paths:
                with self.subTest(workflow=workflow_name, path=path):
                    self.assertNotIn(f'- "{path}"', trigger_block)

    def test_weekly_publisher_stages_every_page_it_renders(self) -> None:
        weekly = workflow_sources()["investments-weekly.yml"]
        for path in (
            "pl/inwestycje.html",
            "pl/inwestycje/pozycje-tygodniowe.html",
            "pl/inwestycje/prognozy-tygodniowe.html",
            "en/investing.html",
            "en/investing/open-weekly-positions.html",
            "en/investing/weekly-forecasts.html",
        ):
            with self.subTest(path=path):
                self.assertIn(path, weekly)

    def test_risk_exit_publisher_is_narrow_and_does_not_mutate_decision_state(self) -> None:
        exposure = workflow_sources()["investments-exposure-watch.yml"]
        self.assertIn("data/investments/weekly", exposure)
        self.assertIn("data/investments/intraday_risk_audit.json", exposure)
        self.assertNotIn("data/investments/multi_instrument_exposure_state_v5.json", exposure)
        self.assertNotIn("data/investments/multi_instrument_exposure_report_v5.json", exposure)

    def test_portfolio_frontends_fail_open_with_validated_cache_and_retry(self) -> None:
        pl = (ROOT / "scripts" / "portfolio-10k-dashboard.js").read_text(
            encoding="utf-8"
        )
        en = (ROOT / "scripts" / "portfolio-10k-dashboard-en.js").read_text(
            encoding="utf-8"
        )
        for source in (pl, en):
            self.assertIn("const CONTROLLER_VERSION = 'resilient-v9'", source)
            self.assertIn("readCache('portfolio', validPortfolio)", source)
            self.assertIn("fetchJsonResilient", source)
            self.assertIn("cache: cacheBust ? 'no-store' : 'default'", source)
            self.assertIn("loadBrace();\n    return loadPortfolio();", source)
            self.assertNotIn("Promise.allSettled", source)
        pl_page = (ROOT / "pl/inwestycje/portfel-10k.html").read_text(encoding="utf-8")
        en_page = (ROOT / "en/investing/portfolio-10k.html").read_text(encoding="utf-8")
        self.assertIn("SPRAWDZANIE", pl_page)
        self.assertIn("CHECKING", en_page)
        self.assertIn("portfolio-10k-dashboard.js?v=9", pl_page)
        self.assertIn("portfolio-10k-dashboard-en.js?v=9", en_page)


if __name__ == "__main__":
    unittest.main()
