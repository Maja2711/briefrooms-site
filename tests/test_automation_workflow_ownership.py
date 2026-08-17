from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow_sources():
    return {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml")}


def owners(needle: str):
    return [name for name, source in workflow_sources().items() if needle in source]


class AutomationWorkflowOwnershipTests(unittest.TestCase):
    def test_news_and_home_workflow_ownership_is_explicit(self) -> None:
        sources = workflow_sources()
        self.assertIn("publish-section-news.yml", sources)
        self.assertIn("publish-homepage.yml", sources)
        self.assertIn("publish-homepage-watchdog.yml", sources)
        self.assertIn("publish-homepage-fast.yml", sources)
        self.assertIn("publish-news-atomic.yml", sources)

        section_source = sources["publish-section-news.yml"]
        self.assertIn("group: section-news-publication", section_source)
        self.assertIn("cancel-in-progress: false", section_source)

        homepage_source = sources["publish-homepage.yml"]
        self.assertIn("group: homepage-publication", homepage_source)
        self.assertIn("cancel-in-progress: false", homepage_source)

        watchdog_source = sources["publish-homepage-watchdog.yml"]
        self.assertIn("group: homepage-publication", watchdog_source)
        self.assertIn("cancel-in-progress: false", watchdog_source)

        fast_source = sources["publish-homepage-fast.yml"]
        self.assertIn("group: homepage-publication", fast_source)
        self.assertIn("cancel-in-progress: false", fast_source)

        atomic_source = sources["publish-news-atomic.yml"]
        self.assertIn("group: atomic-news-publication", atomic_source)
        self.assertIn("cancel-in-progress: false", atomic_source)

    def test_publisher_workflows_use_main_and_fail_closed(self) -> None:
        for workflow_name in (
            "publish-section-news.yml",
            "publish-homepage.yml",
            "publish-homepage-watchdog.yml",
            "publish-homepage-fast.yml",
            "publish-news-atomic.yml",
        ):
            source = workflow_sources()[workflow_name]
            with self.subTest(workflow=workflow_name):
                self.assertIn("ref: main", source)
                self.assertNotIn("continue-on-error: true", source)

    def test_atomic_news_has_single_active_section_page_owner(self) -> None:
        owners_by_page = {
            "pl/aktualnosci.html": set(owners('pl/aktualnosci.html')),
            "en/news.html": set(owners('en/news.html')),
        }
        for page, page_owners in owners_by_page.items():
            with self.subTest(page=page):
                self.assertIn("publish-news-atomic.yml", page_owners)
                self.assertNotIn("publish-section-news.yml", page_owners)

    def test_section_news_builder_has_no_direct_homepage_write(self) -> None:
        source = workflow_sources()["publish-section-news.yml"]
        self.assertNotIn("pl/index.html", source)
        self.assertNotIn("en/index.html", source)

    def test_homepage_builders_do_not_write_section_pages(self) -> None:
        for workflow_name in ("publish-homepage.yml", "publish-homepage-watchdog.yml", "publish-homepage-fast.yml"):
            source = workflow_sources()[workflow_name]
            with self.subTest(workflow=workflow_name):
                self.assertNotIn("pl/aktualnosci.html", source)
                self.assertNotIn("en/news.html", source)

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
        self.assertIn('cron: "7,37 0-6 * * 6"', exposure)
        settle = exposure.index("Settle due weekly positions before downstream work")
        verify = exposure.index("Verify no weekly exposure survives its deadline")
        persist = exposure.index("Persist weekly exits before downstream audits")
        broad_validation = exposure.index("Validate publication integrity code")
        broad_audit = exposure.index("Audit ledger integrity before publication")
        self.assertLess(settle, verify)
        self.assertLess(verify, persist)
        self.assertLess(persist, broad_validation)
        self.assertLess(persist, broad_audit)

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

    def test_no_workflow_uses_removed_w32_emergency(self) -> None:
        for name, source in workflow_sources().items():
            with self.subTest(workflow=name):
                self.assertNotIn("investments-w32-emergency", source)

    def test_workflow_run_dependencies_reference_existing_names(self) -> None:
        names = set()
        for source in workflow_sources().values():
            match = re.search(r"(?m)^name:\s*(.+?)\s*$", source)
            if match:
                names.add(match.group(1).strip('"\''))
        referenced = []
        for source in workflow_sources().values():
            for block in re.findall(r"workflows:\s*\[([^\]]+)\]", source):
                referenced.extend(x.strip().strip('"\'') for x in block.split(","))
        missing = sorted(set(referenced) - names)
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
