from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import build_lab_health as health


class LabHealthContractTests(unittest.TestCase):
    def fixture(self, root: Path) -> dict:
        inv = root / "data/investments"
        inv.mkdir(parents=True)
        stamp = "2026-09-25T18:00:00Z"
        (inv / "experiment_registry.json").write_text(json.dumps({
            "schema_version": "briefrooms-experiment-registry-v1",
            "generated_at": stamp,
            "summary": {"errors": 0},
        }))
        (inv / "experience_store_public.json").write_text(json.dumps({
            "schema_version": "briefrooms-experience-store-public-v1",
            "generated_at": stamp,
        }))
        (inv / "research_lab_report.json").write_text(json.dumps({
            "schema_version": "briefrooms-research-lab-report-v2",
            "generated_at": stamp,
            "execution_loop_closed": True,
            "queue_remaining": 0,
        }))
        return {
            "experiment-registry.yml": {"status": "completed", "conclusion": "success"},
            "experience-store-public.yml": {"status": "completed", "conclusion": "success"},
            "research-lab.yml": {"status": "completed", "conclusion": "success"},
        }

    def test_healthy_requires_source_and_workflow_health(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = self.fixture(root)
            out = health.build(root, workflows, now=datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "HEALTHY")
        self.assertTrue(out["contract"]["frontend_load_success_is_not_health"])

    def test_failed_workflow_is_error_even_when_json_loads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = self.fixture(root)
            workflows["research-lab.yml"] = {"status": "completed", "conclusion": "failure"}
            out = health.build(root, workflows, now=datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "ERROR")
        self.assertTrue(any("workflow_conclusion:failure" in reason for reason in out["reasons"]))

    def test_open_research_queue_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workflows = self.fixture(root)
            path = root / "data/investments/research_lab_report.json"
            report = json.loads(path.read_text())
            report["queue_remaining"] = 7
            path.write_text(json.dumps(report))
            out = health.build(root, workflows, now=datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "ERROR")
        self.assertIn("research_lab:research_queue_not_drained", out["reasons"])


if __name__ == "__main__":
    unittest.main()
