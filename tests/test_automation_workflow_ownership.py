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
        self.assertIn("inputs.mode || 'auto'", weekly)
        self.assertNotIn("inputs.mode || 'ensure-exposure'", weekly)
        self.assertIn("Verify weekly position lifecycle", weekly)
        self.assertIn("weekly position remains open after", weekly)
        self.assertIn("tests.test_automation_workflow_ownership.AutomationWorkflowOwnershipTests.test_weekly_schedule_runs_full_lifecycle_and_blocks_weekend_exposure", weekly)

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

    def test_risk_exit_publisher_stages_immediate_exposure_state(self) -> None:
        exposure = workflow_sources()["investments-exposure-watch.yml"]
        for path in (
            "data/investments/multi_instrument_exposure_state_v5.json",
            "data/investments/multi_instrument_exposure_report_v5.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(2, exposure.count(path))

    def test_portfolio_frontends_use_registry_and_cache_busting(self) -> None:
        pl = (ROOT / "scripts" / "portfolio-10k-dashboard.js").read_text(
            encoding="utf-8"
        )
        en = (ROOT / "scripts" / "portfolio-10k-dashboard-en.js").read_text(
            encoding="utf-8"
        )
        for source in (pl, en):
            self.assertIn("/data/system/automation_status.json?v=${Date.now()}", source)
            self.assertIn("dataset.automationStatus=state", source)
            self.assertIn("cache:'no-store'", source)
        pl_page = (ROOT / "pl/inwestycje/portfel-10k.html").read_text(encoding="utf-8")
        en_page = (ROOT / "en/investing/portfolio-10k.html").read_text(encoding="utf-8")
        self.assertIn("SPRAWDZANIE", pl_page)
        self.assertIn("CHECKING", en_page)
        self.assertIn("portfolio-10k-dashboard.js?v=5", pl_page)
        self.assertIn("portfolio-10k-dashboard-en.js?v=5", en_page)


if __name__ == "__main__":
    unittest.main()
