from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import autonomous_policy_promotion as app
from scripts import validation_epoch as ve
from scripts.experiment_result_lesson_loop import (
    LESSONS_FILENAME,
    RESULTS_FILENAME,
    run_loop,
    verify_lesson_ledger,
    verify_result_ledger,
)
from scripts.hypothesis_experiment_compiler import RUNTIME_FILENAME, launch_shadow_experiments
from scripts.lesson_hypothesis_registry import DEFAULT_REGISTRY, load_registry, registry_hash, validate_registry


class ExperimentResultLessonLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = load_registry(DEFAULT_REGISTRY)
        self.start = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)

    def _shadow_row(
        self,
        index: int,
        *,
        engine_id: str = "gpw_daily",
        decision_at: datetime | None = None,
        score: float = 71.5,
        gate: str = "minimum_composite_score",
        threshold: float = 72.0,
        other_gates: bool = True,
        ret: float = 0.30,
        r_multiple: float = 0.20,
    ) -> dict:
        when = decision_at or (self.start + timedelta(hours=index + 1))
        row = {
            "schema_version": app.SHADOW_SCHEMA,
            "shadow_outcome_id": f"shadow-{engine_id}-{index}",
            "snapshot_id": f"snap-{index}",
            "candidate_id": f"real-rejected-candidate-{index}",
            "engine_id": engine_id,
            "decision_at": when.isoformat().replace("+00:00", "Z"),
            "settled_at": (when + timedelta(days=3)).isoformat().replace("+00:00", "Z"),
            "symbol": f"TEST{index}",
            "candidate_score": score,
            "first_blocking_gate": gate,
            "source_threshold": threshold,
            "other_hard_gates_passed": other_gates,
            "entry": 100.0,
            "exit_price": 100.3,
            "exit_reason": "two_session_horizon",
            "exit_day": (when.date() + timedelta(days=3)).isoformat(),
            "return_percent": ret,
            "r_multiple": r_multiple,
            "conservative_same_bar": False,
            "settlement_rule": "frozen_reference_entry_then_next_two_full_sessions_stop_target_horizon_v1",
        }
        row["row_sha256"] = app._shadow_hash(row)
        return row

    def _prepare(self, state: Path, rows: list[dict]) -> None:
        launch_shadow_experiments(self.source, state, committed_at=self.start, shadow_rows=[])
        shadow_path = state / app.SHADOW_FILENAME
        shadow_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
        app.verify_shadow(shadow_path)

    def test_fixed_n_boundary_prevents_early_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self._prepare(state, [self._shadow_row(i) for i in range(29)])
            summary = run_loop(state, now=self.start + timedelta(days=40))
            self.assertEqual(summary["results_created"], 0)
            self.assertEqual(summary["lessons_created"], 0)
            self.assertEqual(summary["waiting_for_fixed_n"], 2)
            self.assertEqual(verify_result_ledger(state / RESULTS_FILENAME)["events"], 0)
            self.assertEqual(verify_lesson_ledger(state / LESSONS_FILENAME)["events"], 0)

    def test_supported_result_creates_one_immutable_lesson_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            noise = [
                self._shadow_row(100, decision_at=self.start - timedelta(seconds=1)),
                self._shadow_row(101, score=70.9),
                self._shadow_row(102, gate="liquidity_gate"),
                self._shadow_row(103, other_gates=False),
            ]
            eligible = [self._shadow_row(i) for i in range(30)]
            self._prepare(state, noise + eligible)
            summary = run_loop(state, now=self.start + timedelta(days=40))
            self.assertEqual(summary["results_created"], 1)
            self.assertEqual(summary["lessons_created"], 1)
            self.assertEqual(summary["terminal"]["SUPPORTED"], 1)

            results = [json.loads(x) for x in (state / RESULTS_FILENAME).read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result["verdict"], "SUPPORTED")
            self.assertEqual(result["sample_n"], 30)
            self.assertEqual(result["formal_evaluation_number"], 1)
            self.assertEqual(result["metrics"]["mean_return_percent"], 0.3)
            self.assertEqual(result["metrics"]["positive_rate"], 1.0)
            self.assertEqual(result["metrics"]["mean_r"], 0.2)
            self.assertTrue(all(row["passed"] for row in result["criteria_evaluation"]["success_criteria"]))
            self.assertFalse(any(row["passed"] for row in result["criteria_evaluation"]["falsification_criteria"]))
            self.assertTrue(all(x.startswith("shadow-gpw_daily-") for x in result["sample_outcome_ids"]))

            lessons = [json.loads(x) for x in (state / LESSONS_FILENAME).read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(len(lessons), 1)
            lesson = lessons[0]
            self.assertEqual(lesson["status"], "OBSERVED")
            self.assertTrue(lesson["hypothesis_input_ready"])
            self.assertFalse(lesson["production_authority"])
            self.assertEqual(lesson["lineage"]["source_result_id"], result["result_id"])

            # A derived lesson is directly compatible with the next Registry cycle.
            merged = copy.deepcopy(self.source)
            merged["lessons"].append({k: v for k, v in lesson.items() if k not in {"schema_version", "previous_hash", "row_sha256"}})
            merged["registry_sha256"] = registry_hash(merged)
            self.assertTrue(validate_registry(merged)["ok"])

            runtime = json.loads((state / RUNTIME_FILENAME).read_text(encoding="utf-8"))
            gpw = next(row for row in runtime["experiments"] if row["candidate"]["engine_id"] == "gpw_daily")
            self.assertEqual(gpw["status"], "SUPPORTED")
            self.assertEqual(gpw["result_id"], result["result_id"])
            original_epoch = gpw["validation_epoch"]["epoch_id"]

            second = run_loop(state, now=self.start + timedelta(days=41))
            self.assertEqual(second["results_created"], 0)
            self.assertEqual(second["lessons_created"], 0)
            self.assertEqual(verify_result_ledger(state / RESULTS_FILENAME)["events"], 1)
            self.assertEqual(verify_lesson_ledger(state / LESSONS_FILENAME)["events"], 1)
            runtime2 = json.loads((state / RUNTIME_FILENAME).read_text(encoding="utf-8"))
            gpw2 = next(row for row in runtime2["experiments"] if row["candidate"]["engine_id"] == "gpw_daily")
            self.assertEqual(gpw2["validation_epoch"]["epoch_id"], original_epoch)

    def test_preregistered_falsifier_creates_rejected_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            rows = [self._shadow_row(i, ret=-0.20, r_multiple=-0.20) for i in range(30)]
            self._prepare(state, rows)
            summary = run_loop(state, now=self.start + timedelta(days=40))
            self.assertEqual(summary["terminal"]["REJECTED"], 1)
            result = json.loads((state / RESULTS_FILENAME).read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(result["verdict"], "REJECTED")
            self.assertTrue(any(row["passed"] for row in result["criteria_evaluation"]["falsification_criteria"]))

    def test_result_ledger_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self._prepare(state, [self._shadow_row(i) for i in range(30)])
            run_loop(state, now=self.start + timedelta(days=40))
            path = state / RESULTS_FILENAME
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            row["verdict"] = "REJECTED"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_result_ledger(path)


if __name__ == "__main__":
    unittest.main()
