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
            states={x["status"] for x in payload["engines"]}
            self.assertTrue(states <= {"RUNNING","NO DATA","STALE","COLLECTING"})
            self.assertIn("RUNNING", states)
            self.assertIn("NO DATA", states)

    def test_infrastructure_shadow_workflows_do_not_block_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / ".github/workflows"
            folder.mkdir(parents=True)
            for name in ("brs-r2-shadow-archive-pilot.yml",
                         "p2-shadow-watchdog.yml",
                         "shadow-engines-watchdog.yml"):
                (folder / name).write_text("name: test\\n", encoding="utf-8")
            info = coverage(root)
            self.assertTrue(info["complete"])
            self.assertEqual(info["unmapped_shadow_workflows"], [])

            # A genuinely new, unregistered shadow engine must still be detected.
            (folder / "unknown-predictor-shadow.yml").write_text(
                "name: test\\n", encoding="utf-8"
            )
            info = coverage(root)
            self.assertFalse(info["complete"])
            self.assertEqual(info["unmapped_shadow_workflows"],
                             ["unknown-predictor-shadow.yml"])

    def test_wes_collects_without_fabricating_resolved_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"data/investments/wes_incremental_alpha_report.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "generated_at":"2026-09-28T10:20:00Z",
                "overall":{"resolved_pairs":0},
                "pipeline":{
                    "records_total":10, "actual_wes_outcomes":2,
                    "missed_historical_captures":2,
                    "resolved_comparable_pairs":0,
                },
            }), encoding="utf-8")
            statuses={"investments-wes.yml":{
                "databaseId":17, "status":"completed", "conclusion":"success",
                "createdAt":"2026-09-28T10:20:00Z", "updatedAt":"2026-09-28T10:21:00Z",
            }}
            payload=build(root,statuses,now=datetime(2026,9,28,10,30,tzinfo=timezone.utc))
            row=next(x for x in payload["engines"] if x["id"]=="wes")
            self.assertEqual(row["status"],"COLLECTING")
            self.assertEqual(row["pipeline"]["actual_wes_outcomes"],2)
            self.assertEqual(row["observations"],0)
            self.assertFalse(row["provenance"]["domain_provenance"]["production_authority"])

    def test_wes_stale_source_does_not_look_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"data/investments/wes_incremental_alpha_report.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "generated_at":"2026-09-27T10:00:00Z",
                "overall":{"resolved_pairs":0}, "pipeline":{},
            }), encoding="utf-8")
            statuses={"investments-wes.yml":{
                "databaseId":18, "status":"completed", "conclusion":"success",
                "createdAt":"2026-09-28T10:20:00Z", "updatedAt":"2026-09-28T10:21:00Z",
            }}
            payload=build(root,statuses,now=datetime(2026,9,28,10,30,tzinfo=timezone.utc))
            row=next(x for x in payload["engines"] if x["id"]=="wes")
            self.assertEqual(row["status"],"STALE")

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

    def test_asd_recent_workflow_cannot_hide_an_old_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"data/investments/fse_discovery_public.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({"generated_at":"2026-10-13T19:36:55Z",
                "summary":{"families":13,"prospective_resolutions":15}}),encoding="utf-8")
            statuses={"fse-autonomous-structure-discovery.yml":{
                "databaseId":23,"status":"completed","conclusion":"success",
                "createdAt":"2026-10-21T18:00:00Z","updatedAt":"2026-10-21T18:01:00Z"}}
            payload=build(root,statuses,now=datetime(2026,10,21,18,30,tzinfo=timezone.utc))
            row=next(x for x in payload["engines"] if x["id"]=="fse-asd")
            self.assertEqual(row["status"],"STALE")
            self.assertEqual(row["observations"],15)

    def test_missing_run_is_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=build(Path(tmp),{},now=datetime(2026,9,28,10,30,tzinfo=timezone.utc))
            row=next(x for x in payload["engines"] if x["id"]=="hypothesis-shadow")
            self.assertEqual(row["status"],"IDLE")


if __name__ == "__main__":
    unittest.main()
