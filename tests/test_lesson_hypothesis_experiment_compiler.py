from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import validation_epoch as ve
from scripts.hypothesis_experiment_compiler import (
    RUNTIME_FILENAME,
    compile_registry,
    launch_shadow_experiments,
    validate_compiled_registry,
)
from scripts.lesson_hypothesis_registry import (
    DEFAULT_REGISTRY,
    load_registry,
    validate_registry,
)


class LessonHypothesisExperimentCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = load_registry(DEFAULT_REGISTRY)

    def test_seed_registry_is_hash_committed_and_zero_authority(self) -> None:
        status = validate_registry(self.source)
        self.assertTrue(status["ok"])
        self.assertEqual(status["lessons"], 2)
        self.assertEqual(status["hypotheses"], 2)
        self.assertEqual(status["ready_for_shadow"], 2)
        self.assertTrue(status["zero_authority"])
        self.assertTrue(all(row["production_authority"] is False for row in self.source["hypotheses"]))

    def test_compiler_is_deterministic_and_pr35_compatible(self) -> None:
        first = compile_registry(self.source)
        second = compile_registry(self.source)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["total"], 2)
        self.assertEqual({row["candidate"]["engine_id"] for row in first["experiments"]}, {"gpw_daily", "us_daily"})
        self.assertTrue(all(row["stage"] == "PR35" for row in first["experiments"]))
        self.assertTrue(all(row["candidate"]["validation_target_n"] == 30 for row in first["experiments"]))
        self.assertTrue(all(row["candidate"]["from_value"] == 72.0 for row in first["experiments"]))
        self.assertTrue(all(row["candidate"]["to_value"] == 71.0 for row in first["experiments"]))
        self.assertTrue(all(row["production_impact"] is False for row in first["experiments"]))
        self.assertTrue(all(row["automatic_promotion"] is False for row in first["experiments"]))
        self.assertTrue(validate_compiled_registry(first)["ok"])

    def test_launch_commits_validation_epoch_before_formal_evidence(self) -> None:
        old_shadow = {
            "shadow_outcome_id": "old-shadow-1",
            "row_sha256": "legacy-digest-only",
            "decision_at": "2026-09-03T12:00:00Z",
        }
        now = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            runtime = launch_shadow_experiments(
                self.source,
                state,
                committed_at=now,
                shadow_rows=[old_shadow],
            )

            self.assertEqual(runtime["summary"]["running_shadow"], 2)
            self.assertEqual(runtime["summary"]["compiled"], 0)
            self.assertTrue((state / RUNTIME_FILENAME).exists())

            chain = ve.verify_chain(state / ve.LEDGER_FILENAME)
            self.assertTrue(chain["ok"])
            self.assertEqual(chain["events"], 2)

            events = ve._read_jsonl(state / ve.LEDGER_FILENAME)
            self.assertEqual(len(events), 2)
            for experiment in runtime["experiments"]:
                self.assertEqual(experiment["status"], "RUNNING_SHADOW")
                reference = experiment["validation_epoch"]
                event = ve.verify_epoch_reference(
                    state,
                    experiment["candidate"],
                    stage="PR35",
                    reference=reference,
                )
                boundary = event["evidence_boundary"]
                self.assertEqual(boundary["strict_decision_at_after"], "2026-09-04T10:00:00Z")
                self.assertFalse(boundary["older_decisions_formally_eligible"])
                self.assertEqual(boundary["existing_shadow_count"], 1)
                self.assertFalse(experiment["authority"]["trade_execution"])
                self.assertFalse(experiment["authority"]["production_policy_writeback"])

    def test_registry_tamper_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.source)
        tampered["hypotheses"][0]["experiment_spec"]["to_value"] = 70.0
        with self.assertRaises(ValueError):
            validate_registry(tampered)

        compiled = compile_registry(self.source)
        compiled["authority"]["automatic_promotion"] = True
        with self.assertRaises(ValueError):
            validate_compiled_registry(compiled)


if __name__ == "__main__":
    unittest.main()
