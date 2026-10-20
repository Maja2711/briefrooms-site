from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import news_publication_incident as incident


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.failed = {"status": "failed", "checked_at": "2026-10-20T06:00:00+00:00",
                       "age_minutes": 91, "marker": "old", "reasons": ["status_stale"]}

    def test_incident_contains_reason_and_workflow_link(self):
        body = incident.issue_body(self.failed, "Maja2711/briefrooms-site", "123")
        self.assertIn("status_stale", body)
        self.assertIn("91 minutes", body)
        self.assertIn("/actions/runs/123", body)

    def test_create_when_no_open_incident(self):
        with patch.object(incident, "gh", side_effect=['[]', '']) as gh:
            self.assertEqual("created", incident.notify(self.failed, "owner/repo", "123"))
            self.assertEqual("create", gh.call_args_list[1].args[1])

    def test_update_existing_incident_without_duplicate(self):
        with patch.object(incident, "gh", side_effect=['[{"number":42,"title":"[NEWS SLA] Publication stale or production mismatch"}]', '']) as gh:
            self.assertEqual("updated", incident.notify(self.failed, "owner/repo", "123"))
            self.assertEqual(("issue", "comment", "42"), gh.call_args_list[1].args[:3])


if __name__ == "__main__":
    unittest.main()
