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

    def test_missing_openai_secret_is_reported_as_configuration_blocker(self) -> None:
        result = {"status": "healthy", "failed_stage": None, "error_class": None}
        with mock.patch.dict(
            "os.environ", {"OPENAI_API_KEY_CONFIGURED": "false"}, clear=False
        ):
            result = health.apply_configured_blockers(self.domain, result)
        self.assertEqual("failed", result["status"])
        self.assertEqual("ai_provider_configuration", result["failed_stage"])
        self.assertEqual("missing_secret", result["error_class"])

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

    def test_completed_brace_spx_snapshot_is_freshness_exempt(self) -> None:
        brace_spx = next(item for item in health.DOMAINS if item.key == "brace_spx")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / brace_spx.data_path
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-07-20T12:00:00Z",
                        "progress": {"completed": 48, "total": 48},
                        "holdout": {"status": "sealed"},
                    }
                ),
                encoding="utf-8",
            )
            reason = health.terminal_snapshot(brace_spx, root)
        result = health.build_domain_status(
            brace_spx,
            [run(4, NOW - timedelta(hours=1))],
            NOW,
            datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
            None,
            freshness_exempt_reason=reason,
        )
        self.assertEqual("healthy", result["status"])
        self.assertEqual("completed_research_with_sealed_holdout", reason)

    def test_failed_related_workflow_fails_the_aggregate_domain(self) -> None:
        brace_spx = next(item for item in health.DOMAINS if item.key == "brace_spx")
        research = [run(10, NOW - timedelta(minutes=5))]
        panel = [run(9, NOW - timedelta(minutes=20), conclusion="failure")]

        def fake_runs(repository, workflow_file, token):
            if workflow_file == "brace-spx-recovery-engine.yml":
                return research
            if workflow_file == "brace-spx-public-panel.yml":
                return panel
            return [run(1, NOW - timedelta(hours=1))]

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                health,
                "fetch_workflow_runs",
                side_effect=fake_runs,
            ),
        ):
            root = Path(directory)
            path = root / brace_spx.data_path
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-07-20T12:00:00Z",
                        "progress": {"completed": 48, "total": 48},
                        "holdout": {"status": "sealed"},
                    }
                ),
                encoding="utf-8",
            )
            registry = health.build_registry(
                root=root,
                now=NOW,
                previous={"workflows": {}},
                repository="example/repo",
                token="token",
                offline=False,
            )
        state = registry["workflows"]["brace_spx"]
        self.assertEqual("failed", state["status"])
        self.assertEqual(
            "brace-spx-public-panel.yml",
            state["components"]["brace-spx-public-panel.yml"]["workflow_file"],
        )
        self.assertEqual("9", state["failed_run_id"])

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
