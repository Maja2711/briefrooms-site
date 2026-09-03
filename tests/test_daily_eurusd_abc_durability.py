from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import daily_eurusd_abc_durability as abc_rsd


class DailyEURUSDABCDurabilityTests(unittest.TestCase):
    def _write_required(self, root: Path) -> None:
        (root / "EURUSD_DAILY_ABC_STATE.json").write_text(
            json.dumps({"schema_version": "eurusd-daily-abc-experiment-v1", "captures": []}),
            encoding="utf-8",
        )
        (root / "EURUSD_DAILY_ABC_REPORT.json").write_text(
            json.dumps({"mode": "research_shadow", "decision_influence": False}),
            encoding="utf-8",
        )
        (root / "EURUSD_DAILY_ABC_LEARNING.json").write_text(
            json.dumps({"schema_version": "eurusd-abc-learning-state-v1", "episodes": []}),
            encoding="utf-8",
        )
        (root / "EURUSD_DAILY_ABC_LEARNING_REPORT.json").write_text(
            json.dumps({"schema_version": "eurusd-abc-learning-report-v1", "authority": {"decision_influence": False}}),
            encoding="utf-8",
        )

    def test_pr20_layer_reuses_pr19_2_durability_contract(self):
        spec = abc_rsd.rsd.LAYERS[abc_rsd.LAYER_ID]
        self.assertEqual(spec.producer_workflow, "Daily EURUSD A-B-C Live Shadow")
        self.assertEqual(spec.primary_artifact, "daily-eurusd-abc-live-shadow-state")
        self.assertEqual(spec.archive_filename, "daily-eurusd-abc-live-shadow-state.tgz")
        self.assertEqual(
            spec.required_files,
            (
                "EURUSD_DAILY_ABC_STATE.json",
                "EURUSD_DAILY_ABC_REPORT.json",
                "EURUSD_DAILY_ABC_LEARNING.json",
                "EURUSD_DAILY_ABC_LEARNING_REPORT.json",
            ),
        )

    def test_seal_verify_and_parent_lineage_for_pr20(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_required(root)
            first = abc_rsd.rsd.seal_state(abc_rsd.LAYER_ID, root)
            verified = abc_rsd.rsd.verify_state(abc_rsd.LAYER_ID, root)
            self.assertEqual(verified["checkpoint_id"], first["checkpoint_id"])

            (root / "EURUSD_DAILY_ABC_REPORT.json").write_text(
                json.dumps({"mode": "research_shadow", "decision_influence": False, "sample": 1}),
                encoding="utf-8",
            )
            second = abc_rsd.rsd.seal_state(abc_rsd.LAYER_ID, root)
            self.assertEqual(second["parent_checkpoint_id"], first["checkpoint_id"])
            self.assertFalse(second["durability_contract"]["public_repository_state_persistence"])
            abc_rsd.rsd.verify_state(abc_rsd.LAYER_ID, root)


if __name__ == "__main__":
    unittest.main()
