from __future__ import annotations
import unittest
from datetime import datetime, timedelta, timezone
from scripts.validate_news_cycle_sla import assess_runs

NOW = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)


def run(minutes_ago, conclusion="success", event="schedule"):
    timestamp = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"status": "completed", "conclusion": conclusion, "event": event,
            "run_started_at": timestamp, "updated_at": timestamp,
            "html_url": "https://github.com/example/actions/runs/1"}


class NewsCycleSlaTests(unittest.TestCase):
    def test_night_without_new_articles_is_healthy_after_successful_cycle(self):
        self.assertEqual("healthy", assess_runs([run(15)], NOW)["status"])

    def test_missing_cycle_over_90_minutes_fails(self):
        result = assess_runs([run(91)], NOW)
        self.assertEqual("failed", result["status"])
        self.assertIn("successful_cycle_missing_or_stale", result["reasons"])

    def test_failure_after_last_success_is_incident(self):
        result = assess_runs([run(40), run(10, "failure")], NOW)
        self.assertEqual("failed", result["status"])
        self.assertIn("publisher_failed_without_later_success", result["reasons"])

    def test_new_success_resolves_prior_failure(self):
        self.assertEqual("healthy", assess_runs([run(10), run(40, "failure")], NOW)["status"])


if __name__ == "__main__":
    unittest.main()
