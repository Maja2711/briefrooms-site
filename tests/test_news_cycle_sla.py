from __future__ import annotations
import unittest
import json
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from scripts.validate_news_cycle_sla import assess_runs, verify_production

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

    def test_production_feed_mismatch_fails(self):
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.data
        def fake_open(request, timeout=15):
            url = request.full_url
            if 'status.json' in url: return Response(json.dumps({'marker': 'expected'}).encode())
            if 'pl.json' in url: return Response(json.dumps({'marker': 'wrong'}).encode())
            if 'en.json' in url: return Response(json.dumps({'marker': 'expected'}).encode())
            return Response(b'<meta name="briefrooms-live-news-marker" content="expected">')
        with patch('scripts.validate_news_cycle_sla.urlopen', side_effect=fake_open):
            self.assertIn('production_feed_marker_mismatch:pl', verify_production())

    def test_production_marker_must_match_successful_run(self):
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.data
        def fake_open(request, timeout=15):
            url = request.full_url
            if 'status.json' in url: return Response(json.dumps({'marker': 'live-news-111-1'}).encode())
            if '.json' in url: return Response(json.dumps({'marker': 'live-news-111-1'}).encode())
            return Response(b'<meta name="briefrooms-live-news-marker" content="live-news-111-1">')
        with patch('scripts.validate_news_cycle_sla.urlopen', side_effect=fake_open):
            self.assertIn('production_not_at_last_successful_cycle', verify_production(expected_run_id=222))

    def test_new_success_resolves_prior_failure(self):
        self.assertEqual("healthy", assess_runs([run(10), run(40, "failure")], NOW)["status"])


if __name__ == "__main__":
    unittest.main()
