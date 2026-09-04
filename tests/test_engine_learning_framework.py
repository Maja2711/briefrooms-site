from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import engine_learning_framework as elf


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data/investments/learning_engine_registry_v1.json"


class EngineSpecificLearningFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = elf.load_learning_registry(REGISTRY_PATH, root=ROOT)
        self.by_engine = {row["engine_id"]: row for row in self.registry["engines"]}

    def test_registry_covers_every_public_experiment_and_isolates_state(self) -> None:
        status = elf.validate_learning_registry(self.registry, root=ROOT)
        self.assertEqual(status["engines"], 9)
        self.assertEqual(status["public_experiments_mapped"], 7)
        self.assertEqual(status["isolated_partitions"], 9)

        partitions = [row["state_partition"] for row in self.registry["engines"]]
        self.assertEqual(len(partitions), len(set(partitions)))
        for row in self.registry["engines"]:
            self.assertEqual(
                row["lifecycle"],
                ["LESSON", "HYPOTHESIS", "EXPERIMENT", "VALIDATION_EPOCH", "EVIDENCE", "RESULT", "LESSON"],
            )
            self.assertFalse(row["knowledge_exchange"]["automatic_cross_engine_writeback"])
            self.assertTrue(row["knowledge_exchange"]["external_lesson_requires_local_experiment"])
            for value in row["authority"].values():
                self.assertFalse(value)

        self.assertEqual(self.by_engine["brace_spx"]["adapter"]["kind"], "brace_spx_shadow_snapshot")
        self.assertEqual(self.by_engine["wes"]["adapter"]["kind"], "wes_incremental_alpha_snapshot")
        self.assertEqual(self.by_engine["gpw_daily"]["adapter"]["mode"], "DELEGATED_EXISTING_LOOP")
        self.assertEqual(self.by_engine["us_daily"]["adapter"]["mode"], "DELEGATED_EXISTING_LOOP")

    def test_run_all_keeps_loops_separate_and_publishes_lessons_only(self) -> None:
        now = "2026-09-04T20:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "engine_learning_state"
            summary = elf.run_all(ROOT, state_root, self.registry, now=now)
            self.assertEqual(summary["engines"], 9)
            self.assertEqual(summary["delegated_existing_loops"], 2)
            self.assertEqual(summary["engine_local_loops"], 7)
            self.assertEqual(summary["public_experiments_mapped"], 7)

            brace_evidence = elf._read_jsonl(state_root / "brace_spx/evidence.jsonl")
            wes_evidence = elf._read_jsonl(state_root / "wes/evidence.jsonl")
            self.assertEqual(len(brace_evidence), 1)
            self.assertEqual(len(wes_evidence), 1)
            self.assertEqual(brace_evidence[0]["engine_id"], "brace_spx")
            self.assertIn("observations_collected", brace_evidence[0]["metrics"])
            self.assertEqual(brace_evidence[0]["metrics"]["live_orders"], 0)
            self.assertEqual(wes_evidence[0]["engine_id"], "wes")
            self.assertIs(wes_evidence[0]["facts"]["historical_backfill_allowed"], False)
            self.assertIs(wes_evidence[0]["facts"]["active_decision_influence"], False)

            brace_lessons = elf._read_jsonl(state_root / "brace_spx/lessons.jsonl")
            wes_lessons = elf._read_jsonl(state_root / "wes/lessons.jsonl")
            self.assertEqual(len(brace_lessons), 1)
            self.assertEqual(len(wes_lessons), 1)

            bus = elf._read_jsonl(state_root / elf.KNOWLEDGE_BUS_FILENAME)
            self.assertEqual(len(bus), 2)
            self.assertEqual({row["source_engine_id"] for row in bus}, {"brace_spx", "wes"})
            for row in bus:
                self.assertEqual(row["recipient_use"], "HYPOTHESIS_INPUT_ONLY")
                self.assertTrue(row["requires_recipient_local_experiment"])
                self.assertFalse(row["automatic_cross_engine_writeback"])

            brace_inputs = json.loads((state_root / "brace_spx" / elf.HYPOTHESIS_INPUT_FILENAME).read_text())
            external_sources = {row["source_engine_id"] for row in brace_inputs["external_lessons"]}
            self.assertIn("wes", external_sources)
            self.assertNotIn("brace_spx", external_sources)
            self.assertFalse(brace_inputs["rules"]["external_lesson_has_direct_policy_authority"])

            # Knowledge exchange must not copy WES lessons into BRACE's local lesson ledger.
            self.assertEqual(len(elf._read_jsonl(state_root / "brace_spx/lessons.jsonl")), 1)

            observatory = json.loads((state_root / elf.OBSERVATORY_FILENAME).read_text())
            self.assertEqual(observatory["coverage"]["public_experiments_total"], 7)
            self.assertEqual(observatory["coverage"]["public_experiments_mapped"], 7)
            self.assertEqual(observatory["coverage"]["unmapped_public_experiments"], [])
            self.assertFalse(observatory["authority"]["decision_authority"])
            self.assertFalse(observatory["authority"]["engine_state_writeback"])

            before = {
                engine: len(elf._read_jsonl(state_root / engine / "evidence.jsonl"))
                for engine in ("brace_spx", "wes", "timesfm", "gse_v2", "eurusd_abc", "strategy_research_lab")
            }
            second = elf.run_all(ROOT, state_root, self.registry, now=now)
            after = {
                engine: len(elf._read_jsonl(state_root / engine / "evidence.jsonl"))
                for engine in before
            }
            self.assertEqual(before, after)
            self.assertEqual(second["knowledge_bus_events"], 2)

    def test_engine_local_lifecycle_has_immutable_lineage(self) -> None:
        now = "2026-09-04T20:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "engine_learning_state"
            elf.run_engine(ROOT, state_root, self.by_engine["wes"], now=now)

            hypothesis = elf.record_local_hypothesis(
                state_root,
                engine_id="wes",
                lesson_ids=["lesson-wes-engine-local-evidence-contract-v1"],
                claim="Prospective WES evidence should be evaluated only inside the WES loop.",
                experiment_family="incremental_decision_value",
                success_criteria=[{"metric": "mean_incremental_alpha_percent", "operator": ">", "value": 0.0}],
                falsification_criteria=[{"metric": "mean_incremental_alpha_percent", "operator": "<=", "value": 0.0}],
                created_at=now,
            )
            experiment = elf.record_local_experiment(
                state_root,
                engine_id="wes",
                hypothesis_id=hypothesis["hypothesis_id"],
                contract={
                    "sample_unit": "resolved_counterfactual_pairs",
                    "fixed_n": 12,
                    "prospective_only": True,
                    "historical_backfill": False,
                    "formal_evaluation_count": 1,
                },
                created_at=now,
            )
            epoch = elf.commit_local_validation_epoch(
                state_root,
                engine_id="wes",
                experiment_id=experiment["experiment_id"],
                committed_at=now,
            )
            self.assertTrue(epoch["eligibility_rule"]["prospective_only"])
            self.assertFalse(epoch["eligibility_rule"]["historical_backfill"])
            self.assertFalse(epoch["evidence_boundary"]["older_evidence_formally_eligible"])

            result = elf.record_local_result(
                state_root,
                engine_id="wes",
                experiment_id=experiment["experiment_id"],
                epoch_id=epoch["epoch_id"],
                verdict="INCONCLUSIVE",
                metrics={"mean_incremental_alpha_percent": None, "sample_n": 0},
                completed_at="2026-09-05T20:00:00Z",
            )
            lesson = elf.derive_local_lesson_from_result(
                state_root,
                engine_id="wes",
                result=result,
                statement="WES remains inconclusive until its own prospective counterfactual sample is complete.",
            )
            self.assertEqual(lesson["lineage"]["source_result_id"], result["result_id"])
            self.assertTrue(lesson["hypothesis_input_ready"])
            self.assertFalse(lesson["production_authority"])

            self.assertFalse((state_root / "brace_spx/results.jsonl").exists())
            self.assertEqual(len(elf._read_jsonl(state_root / "wes/results.jsonl")), 1)
            self.assertEqual(len(elf._read_jsonl(state_root / "wes/lessons.jsonl")), 2)

    def test_experiment_rejects_non_prospective_contract(self) -> None:
        now = "2026-09-04T20:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "engine_learning_state"
            elf.run_engine(ROOT, state_root, self.by_engine["brace_spx"], now=now)
            hypothesis = elf.record_local_hypothesis(
                state_root,
                engine_id="brace_spx",
                lesson_ids=["lesson-brace-engine-local-evidence-contract-v1"],
                claim="BRACE local experiment must remain prospective.",
                experiment_family="regime_signal_validation",
                success_criteria=[{"metric": "strict_gate_passed", "operator": "==", "value": 1}],
                falsification_criteria=[{"metric": "strict_gate_passed", "operator": "==", "value": 0}],
                created_at=now,
            )
            with self.assertRaises(ValueError):
                elf.record_local_experiment(
                    state_root,
                    engine_id="brace_spx",
                    hypothesis_id=hypothesis["hypothesis_id"],
                    contract={"prospective_only": False, "historical_backfill": True},
                    created_at=now,
                )


if __name__ == "__main__":
    unittest.main()
