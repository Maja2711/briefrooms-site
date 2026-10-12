import base64
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

from scripts import shadow_engines_watchdog as watchdog
from scripts import publish_shadow_snapshot as publisher


class ShadowObservatoryWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 17, 0, tzinfo=timezone.utc)
        self.snapshot = {
            "schema_version": "briefrooms-shadow-engines-public-v1",
            "generated_at": (self.now - timedelta(minutes=19)).isoformat(),
            "read_only": True,
            "production_authority": False,
            "engines": [],
            "coverage": {"complete": True},
        }

    def test_fresh_at_19_and_20_minutes_stale_at_21(self):
        self.assertEqual(watchdog.evaluate(self.snapshot, self.now)["reason"], "ok")
        self.snapshot["generated_at"] = (self.now - timedelta(minutes=20)).isoformat()
        self.assertFalse(watchdog.evaluate(self.snapshot, self.now)["stale"])
        self.snapshot["generated_at"] = (self.now - timedelta(minutes=21)).isoformat()
        self.assertEqual(watchdog.evaluate(self.snapshot, self.now)["reason"], "stale_report")

    def test_unavailable_invalid_future_and_wrong_authority_fail_closed(self):
        self.assertTrue(watchdog.evaluate(None, self.now)["stale"])
        self.snapshot["generated_at"] = (self.now + timedelta(minutes=3)).isoformat()
        self.assertEqual(watchdog.evaluate(self.snapshot, self.now)["reason"], "future_dated_report")
        self.snapshot["generated_at"] = self.now.isoformat()
        self.snapshot["production_authority"] = True
        self.assertEqual(watchdog.evaluate(self.snapshot, self.now)["reason"], "authority_invariant_violation")

    def test_remote_read_and_deduplicated_issue_recovery(self):
        calls = []
        alarm = {"title": watchdog.ISSUE_TITLE, "body": watchdog.ISSUE_MARKER, "number": 842}
        created = []
        def client(repo, token, path, method="GET", body=None):
            calls.append((path, method, body))
            if path.startswith("contents/"):
                return {"content": base64.b64encode(json.dumps(self.snapshot).encode()).decode()}
            if path.startswith("actions/"):
                return {"workflow_runs": [{"id": 4, "status": "completed",
                    "conclusion": "failure", "created_at": "2026-10-12T16:40:00Z",
                    "html_url": "https://github.com/example/workflow/4"}]}
            if path.startswith("issues?"):
                return [alarm] if created else []
            if path == "issues" and method == "POST":
                created.append(alarm)
                return alarm
            if path == "issues/842/comments" and method == "POST":
                return {}
            if path == "issues/842" and method == "PATCH":
                return {}
            raise AssertionError((path, method))
        self.assertEqual(watchdog.fetch_snapshot("owner/repo", "token", client), self.snapshot)
        self.assertEqual(watchdog.fetch_runs("owner/repo", "token", client)[0]["conclusion"], "failure")
        stale = watchdog.evaluate(None, self.now)
        self.assertEqual(watchdog.reconcile("owner/repo", "token", stale, [], "", client),
                         "opened_issue_842")
        self.assertEqual(watchdog.reconcile("owner/repo", "token", stale, [], "", client),
                         "existing_issue_842")
        healthy = watchdog.evaluate(self.snapshot, self.now)
        self.assertEqual(watchdog.reconcile("owner/repo", "token", healthy, [], "", client),
                         "healthy_closed_1")
        self.assertEqual(len([c for c in calls if c[0] == "issues" and c[1] == "POST"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "issues/842" and c[1] == "PATCH"]), 1)

    def test_publisher_conflict_retry_and_freshness(self):
        fresh_now = datetime.now(timezone.utc)
        report = dict(self.snapshot)
        report["generated_at"] = fresh_now.isoformat()
        remote_old = dict(report)
        remote_old["generated_at"] = (fresh_now - timedelta(hours=1)).isoformat()
        gets, puts = [], []
        def request(repo, token, method, payload=None):
            if method == "GET":
                gets.append(method)
                return {"sha": "abc123", "content":
                        base64.b64encode(json.dumps(remote_old).encode()).decode()}
            puts.append(payload)
            if len(puts) == 1:
                raise HTTPError("https://api.github.com/mock", 409, "conflict", {}, None)
            return {"commit": {"sha": "published"}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            path.write_text(json.dumps(report))
            self.assertEqual(publisher.publish(path, "owner/repo", "token",
                                              request=request, sleep=lambda _: None),
                             "published")
        self.assertEqual(len(gets), 2)
        self.assertEqual(len(puts), 2)
        self.assertEqual(puts[0]["branch"], "main")
        self.assertEqual(puts[0]["sha"], "abc123")
        self.assertEqual(base64.b64decode(puts[1]["content"]), json.dumps(report).encode())

    def test_publisher_cannot_revert_fresher_remote_or_bypass_authority(self):
        report = dict(self.snapshot)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        newer = dict(report)
        newer["generated_at"] = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        def request(repo, token, method, payload=None):
            if method == "PUT":
                raise AssertionError("older report must not overwrite newer report")
            return {"sha": "new", "content": base64.b64encode(json.dumps(newer).encode()).decode()}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            path.write_text(json.dumps(report))
            self.assertEqual(publisher.publish(path, "owner/repo", "token", request=request),
                             "skipped_newer_remote")
        report["production_authority"] = True
        with self.assertRaises(ValueError):
            publisher.verify_snapshot(report)
        report["production_authority"] = False
        report["generated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):
            publisher.verify_snapshot(report)


if __name__ == "__main__":
    unittest.main()
