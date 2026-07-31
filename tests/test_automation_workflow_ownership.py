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

    def test_health_only_commits_do_not_trigger_a_pages_deploy(self) -> None:
        deploy = workflow_sources()["deploy-production.yml"]
        self.assertIn("paths-ignore:", deploy)
        self.assertIn('      - "data/system/**"', deploy)

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
