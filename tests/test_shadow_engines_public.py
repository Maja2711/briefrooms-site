import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_shadow_engines_public import build, coverage, SPECS


class ShadowEnginesPublicTests(unittest.TestCase):
    def test_registry_has_required_status_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/".github/workflows").mkdir(parents=True)
            # Mirror only mapped shadow workflow filenames to exercise coverage.
            for spec in SPECS:
                for wf in spec["workflows"]:
                    if "shadow" in wf:
                        (root/".github/workflows"/wf).write_text("name: test\n", encoding="utf-8")
            statuses={spec["workflows"][0]:{
                "databaseId":1,"status":"completed","conclusion":"success",
                "createdAt":"2026-09-28T10:00:00Z","updatedAt":"2026-09-28T10:01:00Z"
            } for spec in SPECS}
            payload=build(root,statuses,now=datetime(2026,9,28,10,30,tzinfo=timezone.utc))
            self.assertEqual(payload["schema_version"],"briefrooms-shadow-engines-public-v1")
            self.assertEqual(payload["summary"]["total"],len(SPECS))
            self.assertTrue(payload["read_only"])
            self.assertFalse(payload["production_authority"])
            self.assertTrue(payload["coverage"]["complete"])
            self.assertEqual({x["status"] for x in payload["engines"]},{"NO DATA"})

    def test_failure_is_error_even_without_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            statuses={SPECS[0]["workflows"][0]:{
                "databaseId":2,"status":"completed","conclusion":"failure",
                "createdAt":"2026-09-28T10:00:00Z","updatedAt":"2026-09-28T10:01:00Z"
            }}
            payload=build(root,statuses,now=datetime(2026,9,28,10,30,tzinfo=timezone.utc))
            row=next(x for x in payload["engines"] if x["id"]=="belief-core")
            self.assertEqual(row["status"],"ERROR")

    def test_missing_run_is_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=build(Path(tmp),{},now=datetime(2026,9,28,10,30,tzinfo=timezone.utc))
            row=next(x for x in payload["engines"] if x["id"]=="hypothesis-shadow")
            self.assertEqual(row["status"],"IDLE")


if __name__ == "__main__":
    unittest.main()
