import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import automation_health_audit as health


NOW = datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc)


def run(
    run_id: int,
    when: datetime,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict:
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "created_at": health.isoformat(when - timedelta(minutes=2)),
        "updated_at": health.isoformat(when),
        "head_sha": f"sha-{run_id}",
        "html_url": f"https://github.com/example/actions/runs/{run_id}",
    }


class AutomationHealthAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.domain = health.DOMAINS[0]

    def test_fresh_success_is_healthy(self) -> None:
        result = health.build_domain_status(
            self.domain,
            [run(12, NOW - timedelta(minutes=10))],
            NOW,
            NOW - timedelta(minutes=20),
            None,
        )
        self.assertEqual("healthy", result["status"])
        self.assertEqual(20, result["data_age_minutes"])
        self.assertEqual("12", result["run_id"])

    def test_old_public_data_is_stale_even_after_success(self) -> None:
        result = health.build_domain_status(
            self.domain,
            [run(12, NOW - timedelta(minutes=10))],
            NOW,
            NOW - timedelta(minutes=self.domain.stale_after_minutes + 1),
            None,
        )
        self.assertEqual("stale", result["status"])
        self.assertEqual("stale_public_data", result["error_class"])

    def test_failed_attempt_does_not_advance_last_success(self) -> None:
        success_at = NOW - timedelta(hours=3)
        failure_at = NOW - timedelta(minutes=5)
        result = health.build_domain_status(
            self.domain,
            [
                run(1, success_at),
                run(2, failure_at, conclusion="failure"),
            ],
            NOW,
            NOW - timedelta(hours=1),
            None,
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual(health.isoformat(failure_at), result["last_attempt_at"])
        self.assertEqual(health.isoformat(success_at), result["last_success_at"])

    def test_active_run_is_running_without_fabricating_success(self) -> None:
        result = health.build_domain_status(
            self.domain,
            [run(3, NOW, status="in_progress", conclusion=None)],
            NOW,
            NOW - timedelta(minutes=30),
            None,
            {"last_success_at": "2026-07-31T12:00:00Z"},
        )
        self.assertEqual("running", result["status"])
        self.assertEqual("2026-07-31T12:00:00Z", result["last_success_at"])

    def test_api_failure_preserves_last_success_and_degrades(self) -> None:
        result = health.build_domain_status(
            self.domain,
            [],
            NOW,
            NOW - timedelta(minutes=20),
            None,
            {"last_success_at": "2026-07-31T12:00:00Z", "run_id": "44"},
            "GitHub API returned HTTP 503",
        )
        self.assertEqual("degraded", result["status"])
        self.assertEqual("2026-07-31T12:00:00Z", result["last_success_at"])
        self.assertEqual("44", result["run_id"])

    def test_missing_or_invalid_data_is_failed(self) -> None:
        result = health.build_domain_status(
            self.domain,
            [run(12, NOW)],
            NOW,
            None,
            "missing_or_invalid_public_data",
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("public_data_validation", result["failed_stage"])

    def test_weekend_threshold_does_not_mark_closed_market_stale(self) -> None:
        portfolio = next(item for item in health.DOMAINS if item.key == "portfolio_prices")
        saturday = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(4320, health.effective_stale_minutes(portfolio, saturday))

    def test_incident_fingerprint_ignores_age_and_run_id(self) -> None:
        first = {
            "status": "stale",
            "failed_stage": "published_data_freshness",
            "error_class": "stale_public_data",
            "data_age_minutes": 600,
            "run_id": "1",
        }
        second = {**first, "data_age_minutes": 660, "run_id": "2"}
        self.assertEqual(
            health.incident_fingerprint(first), health.incident_fingerprint(second)
        )

    def test_same_incident_does_not_comment_again(self) -> None:
        workflows = {}
        incidents = {}
        for domain in health.DOMAINS:
            state = {
                "status": "healthy",
                "failed_stage": None,
                "error_class": None,
                "last_attempt_at": None,
                "last_success_at": None,
                "data_updated_at": None,
                "run_url": None,
            }
            workflows[domain.key] = state
        workflows["news_pl_en"] = {
            **workflows["news_pl_en"],
            "status": "stale",
            "failed_stage": "published_data_freshness",
            "error_class": "stale_public_data",
        }
        fingerprint = health.incident_fingerprint(workflows["news_pl_en"])
        incidents["news_pl_en"] = {
            "fingerprint": fingerprint,
            "state": "open",
            "issue_number": 9,
            "first_seen_at": "2026-07-31T12:00:00Z",
        }
        with (
            mock.patch.object(
                health,
                "list_open_automation_issues",
                return_value={
                    "[AUTOMATION] PL and EN news publication": {"number": 9}
                },
            ),
            mock.patch.object(health, "github_json") as github_json,
        ):
            health.sync_incidents(
                {"workflows": workflows},
                {"incidents": incidents},
                "example/repo",
                "token",
                NOW,
            )
        github_json.assert_not_called()

    def test_atomic_write_and_validation_never_publish_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = {
                "workflows": {
                    domain.key: {
                        "status": "healthy",
                        "last_attempt_at": None,
                        "last_success_at": None,
                    }
                    for domain in health.DOMAINS
                }
            }
            health.validate_registry(registry)
            path = root / "nested" / "status.json"
            health.write_json_atomic(path, registry)
            self.assertEqual(registry, json.loads(path.read_text(encoding="utf-8")))
            registry["workflows"]["news_pl_en"]["error_summary"] = "Bearer ghp_secret"
            with self.assertRaises(ValueError):
                health.validate_registry(registry)


if __name__ == "__main__":
    unittest.main()
