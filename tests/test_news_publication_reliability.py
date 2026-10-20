from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import validate_live_news_health as health
from tests.test_validate_live_news_health import NOW, MARKER, write_fixture


class NewsPublicationReliabilityTests(unittest.TestCase):
    def test_current_release_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            report = health.assess(root, now=NOW, max_age_minutes=90)
            self.assertNotEqual("failed", report["status"], report["reasons"])

    def test_publication_over_90_minutes_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root, NOW - timedelta(minutes=91))
            report = health.assess(root, now=NOW, max_age_minutes=90)
            self.assertEqual("failed", report["status"])
            self.assertIn("status_stale", report["reasons"])

    def test_production_marker_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            with patch.object(health, "fetch", return_value=json.dumps({"marker": "wrong"}).encode()):
                report = health.assess(root, now=NOW, max_age_minutes=90,
                                       base_url="https://example.invalid", attempts=1)
            self.assertEqual("failed", report["status"])
            self.assertTrue(any("production_marker_mismatch" in reason for reason in report["reasons"]))

    def test_publisher_preserves_active_run(self):
        workflow = Path(".github/workflows/publish-news.yml").read_text(encoding="utf-8")
        self.assertIn("group: news-publication", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("cancel-in-progress: true", workflow)

    def test_watchdog_has_independent_schedule_and_strict_threshold(self):
        workflow = Path(".github/workflows/news-publication-sla-watchdog.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "43 * * * *"', workflow)
        self.assertIn("--max-age-minutes 90", workflow)
        self.assertIn("scripts/validate_news_cycle_sla.py", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("Mark SLA breach as failed", workflow)
        self.assertIn("if: github.event_name != 'pull_request'", workflow)


if __name__ == "__main__":
    unittest.main()
