from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import engine_learning_framework as elf
from scripts import wes_progress_calibration as wpc


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/investments/wes_calibration_policy_v1.json"
REGISTRY_PATH = ROOT / "data/investments/learning_engine_registry_v1.json"


class WesProgressCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = wpc.load_policy(POLICY_PATH)

    def _append_evidence(
        self,
        state_root: Path,
        *,
        n: int,
        effective: float,
        alpha: float | None,
        better: float | None,
        observed_at: str,
        historical_backfill_allowed: bool = False,
        active_decision_influence: bool = False,
    ) -> dict:
        payload = {
            "resolved_pairs": n,
            "effective_samples": effective,
            "mean_incremental_alpha_percent": alpha,
            "wes_better_than_v5_rate": better,
            "observed_at": observed_at,
        }
        row = elf._evidence_row(
            engine_id="wes",
            observed_at=observed_at,
            source="test:wes_incremental_alpha_report",
            source_event_key=f"resolved={n}:{observed_at}",
            sample_count=n,
            sample_unit="resolved_counterfactual_pairs",
            metrics={
                "resolved_pairs": n,
                "mean_incremental_alpha_percent": alpha,
                "median_incremental_alpha_percent": alpha,
                "wes_better_than_v5_rate": better,
                "best_incremental_alpha_percent": alpha,
                "worst_incremental_alpha_percent": alpha,
                "economic_decisions": n,
                "effective_samples": effective,
            },
            facts={
                "sample_status": "collecting_prospective_pairs",
                "minimum_before_descriptive_analysis": 12,
                "baseline_definition": "prospectively_frozen_pre_wes_v5_risk_plan",
                "historical_backfill_allowed": historical_backfill_allowed,
                "active_decision_influence": active_decision_influence,
                "bounded_influence_enabled": False,
            },
            source_payload=payload,
        )
        return elf._append_chained(
            state_root / "wes" / "evidence.jsonl",
            schema=elf.EVIDENCE_SCHEMA,
            id_field="evidence_id",
            payload=row,
        )

    def test_policy_is_shadow_only_and_zero_authority(self) -> None:
        status = wpc.validate_policy(self.policy)
        self.assertTrue(status["ok"])
        self.assertEqual(status["minimum_resolved_pairs"], 12)
        self.assertEqual(status["validation_target_n"], 30)
        self.assertEqual(self.policy["candidate"]["mode"], "SHADOW_CHALLENGER_ONLY")
        self.assertFalse(self.policy["candidate"]["automatic_active_wes_writeback"])
        self.assertTrue(self.policy["candidate"]["requires_promotion_gate"])
        for value in self.policy["authority"].values():
            self.assertFalse(value)

    def test_activation_does_not_retroactively_create_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            self._append_evidence(
                state,
                n=16,
                effective=16.0,
                alpha=0.24,
                better=0.68,
                observed_at="2026-09-04T20:00:00Z",
            )
            first = wpc.advance_wes_calibration(
                state,
                policy=self.policy,
                now="2026-09-04T20:05:00Z",
            )
            self.assertTrue(first["activated"])
            self.assertEqual(first["progress_signals_created"], 0)
            self.assertEqual(first["calibration_candidates_created"], 0)
            self.assertEqual(len(elf._read_jsonl(state / "wes/calibration_candidates.jsonl")), 0)

    def test_under_minimum_sample_never_creates_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-04T20:00:00Z")
            self._append_evidence(
                state,
                n=11,
                effective=11.0,
                alpha=0.30,
                better=0.70,
                observed_at="2026-09-05T20:00:00Z",
            )
            result = wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-05T20:01:00Z")
            self.assertEqual(result["calibration_candidates_created"], 0)
            signals = elf._read_jsonl(state / "wes/progress_signals.jsonl")
            self.assertEqual(signals[-1]["conclusion"], "INSUFFICIENT_PROGRESS")

    def test_first_positive_snapshot_is_descriptive_second_improving_snapshot_creates_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-04T20:00:00Z")

            first_evidence = self._append_evidence(
                state,
                n=12,
                effective=12.0,
                alpha=0.15,
                better=0.60,
                observed_at="2026-09-05T20:00:00Z",
            )
            first = wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-05T20:01:00Z")
            self.assertEqual(first["progress_signals_created"], 1)
            self.assertEqual(first["calibration_candidates_created"], 0)
            self.assertEqual(
                elf._read_jsonl(state / "wes/progress_signals.jsonl")[-1]["conclusion"],
                "DESCRIPTIVE_SIGNAL",
            )

            second_evidence = self._append_evidence(
                state,
                n=16,
                effective=16.0,
                alpha=0.24,
                better=0.68,
                observed_at="2026-09-06T20:00:00Z",
            )
            second = wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-06T20:01:00Z")
            self.assertEqual(second["calibration_candidates_created"], 1)

            signals = elf._read_jsonl(state / "wes/progress_signals.jsonl")
            self.assertEqual(signals[-1]["conclusion"], "SUSTAINED_POSITIVE_PROGRESS")
            self.assertEqual(signals[-1]["source_evidence_id"], second_evidence["evidence_id"])
            self.assertNotEqual(signals[-1]["source_evidence_id"], first_evidence["evidence_id"])

            candidates = elf._read_jsonl(state / "wes/calibration_candidates.jsonl")
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate["engine_id"], "wes")
            self.assertEqual(candidate["status"], "VALIDATING_SHADOW")
            self.assertEqual(candidate["mode"], "SHADOW_CHALLENGER_ONLY")
            self.assertEqual(candidate["calibration_axis"], "incremental_decision_value")
            self.assertFalse(candidate["active_wes_writeback"])
            self.assertTrue(candidate["requires_promotion_gate"])
            self.assertFalse(candidate["automatic_promotion"])
            self.assertFalse(candidate["validation_evidence_rule"]["older_evidence_formally_eligible"])
            self.assertEqual(
                candidate["validation_evidence_rule"]["strict_observed_at_after"],
                "2026-09-06T20:01:00Z",
            )

            experiments = elf._read_jsonl(state / "wes/experiments.jsonl")
            self.assertEqual(len(experiments), 1)
            contract = experiments[0]["contract"]
            self.assertTrue(contract["prospective_only"])
            self.assertFalse(contract["historical_backfill"])
            self.assertFalse(contract["active_wes_writeback"])
            self.assertTrue(contract["requires_promotion_gate"])
            self.assertEqual(contract["fixed_n"], 30)

            epochs = elf._read_jsonl(state / "wes/validation_epochs.jsonl")
            self.assertEqual(len(epochs), 1)
            self.assertFalse(epochs[0]["evidence_boundary"]["older_evidence_formally_eligible"])
            self.assertEqual(epochs[0]["evidence_boundary"]["evidence_events_before"], 2)

            lessons = elf._read_jsonl(state / "wes/lessons.jsonl")
            hypotheses = elf._read_jsonl(state / "wes/hypotheses.jsonl")
            self.assertEqual(len(lessons), 1)
            self.assertEqual(len(hypotheses), 1)
            self.assertEqual(hypotheses[0]["lesson_ids"], [lessons[0]["lesson_id"]])

            self.assertFalse((state / "brace_spx/calibration_candidates.jsonl").exists())
            self.assertFalse((state / "brace_spx/hypotheses.jsonl").exists())

            again = wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-06T20:02:00Z")
            self.assertEqual(again["new_evidence_processed"], 0)
            self.assertEqual(again["progress_signals_created"], 0)
            self.assertEqual(again["calibration_candidates_created"], 0)
            self.assertEqual(len(elf._read_jsonl(state / "wes/calibration_candidates.jsonl")), 1)

    def test_positive_but_not_improving_snapshot_does_not_create_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-04T20:00:00Z")
            self._append_evidence(
                state,
                n=12,
                effective=12.0,
                alpha=0.20,
                better=0.65,
                observed_at="2026-09-05T20:00:00Z",
            )
            wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-05T20:01:00Z")
            self._append_evidence(
                state,
                n=16,
                effective=16.0,
                alpha=0.18,
                better=0.64,
                observed_at="2026-09-06T20:00:00Z",
            )
            result = wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-06T20:01:00Z")
            self.assertEqual(result["calibration_candidates_created"], 0)
            self.assertEqual(
                elf._read_jsonl(state / "wes/progress_signals.jsonl")[-1]["conclusion"],
                "POSITIVE_NO_METRIC_IMPROVEMENT",
            )

    def test_fail_closed_if_wes_source_allows_backfill_or_active_influence(self) -> None:
        for backfill, influence in ((True, False), (False, True)):
            with self.subTest(backfill=backfill, influence=influence):
                with tempfile.TemporaryDirectory() as tmp:
                    state = Path(tmp) / "state"
                    wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-04T20:00:00Z")
                    self._append_evidence(
                        state,
                        n=12,
                        effective=12.0,
                        alpha=0.20,
                        better=0.60,
                        observed_at="2026-09-05T20:00:00Z",
                        historical_backfill_allowed=backfill,
                        active_decision_influence=influence,
                    )
                    with self.assertRaises(ValueError):
                        wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-05T20:01:00Z")
                    self.assertEqual(len(elf._read_jsonl(state / "wes/calibration_candidates.jsonl")), 0)

    def test_only_one_active_candidate_even_if_progress_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            wpc.advance_wes_calibration(state, policy=self.policy, now="2026-09-04T20:00:00Z")
            for n, alpha, better, observed, run_at in (
                (12, 0.15, 0.60, "2026-09-05T20:00:00Z", "2026-09-05T20:01:00Z"),
                (16, 0.24, 0.68, "2026-09-06T20:00:00Z", "2026-09-06T20:01:00Z"),
                (20, 0.30, 0.72, "2026-09-07T20:00:00Z", "2026-09-07T20:01:00Z"),
            ):
                self._append_evidence(state, n=n, effective=float(n), alpha=alpha, better=better, observed_at=observed)
                wpc.advance_wes_calibration(state, policy=self.policy, now=run_at)
            self.assertEqual(len(elf._read_jsonl(state / "wes/calibration_candidates.jsonl")), 1)

    def test_current_real_wes_state_does_not_create_candidate_on_activation(self) -> None:
        registry = elf.load_learning_registry(REGISTRY_PATH, root=ROOT)
        wes_entry = next(row for row in registry["engines"] if row["engine_id"] == "wes")
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            elf.run_engine(ROOT, state, wes_entry, now="2026-09-04T22:00:00Z")
            result = wpc.advance_wes_calibration(
                state,
                policy=self.policy,
                now="2026-09-04T22:01:00Z",
            )
            self.assertTrue(result["activated"])
            self.assertEqual(result["calibration_candidates_created"], 0)
            self.assertEqual(len(elf._read_jsonl(state / "wes/calibration_candidates.jsonl")), 0)


if __name__ == "__main__":
    unittest.main()
