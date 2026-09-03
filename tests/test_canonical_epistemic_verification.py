from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

try:
    from canonical_epistemic_verification import (
        CanonicalEpistemicVerificationError,
        build_target,
        build_targets,
        calibration_record,
        resolve_target,
        target_from_dict,
        verify_target,
        verification_from_dict,
        verify_verification,
    )
    from canonical_epistemic_verification_builder import build_runtime
    from belief_calibration import build_calibration_report
except ModuleNotFoundError:
    from scripts.canonical_epistemic_verification import (
        CanonicalEpistemicVerificationError,
        build_target,
        build_targets,
        calibration_record,
        resolve_target,
        target_from_dict,
        verify_target,
        verification_from_dict,
        verify_verification,
    )
    from scripts.canonical_epistemic_verification_builder import build_runtime
    from scripts.belief_calibration import build_calibration_report


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def canonical_state() -> dict:
    return {
        "contract_version": "briefrooms-epistemic-state-v1",
        "state_id": "eps-demo",
        "state_hash": H1,
        "as_of": "2026-09-03T08:00:00Z",
        "beliefs": [
            {
                "belief_id": "belief-growth",
                "belief_hash": H2,
                "as_of": "2026-09-03T07:59:00Z",
                "probability": 0.70,
                "confidence": 0.80,
                "domain": "macro",
                "entity": "PL",
                "evidence_ids": ["ev-b", "ev-a"],
                "verify_later": True,
                "expected_outcome": "growth_above_threshold",
            },
            {
                "belief_id": "belief-no-target",
                "belief_hash": H3,
                "as_of": "2026-09-03T07:59:00Z",
                "probability": 0.40,
                "confidence": 0.50,
                "domain": "macro",
                "entity": "PL",
                "evidence_ids": [],
                "verify_later": False,
            },
        ],
        "evidence": [
            {"evidence_id": "ev-a", "evidence_hash": H3},
            {"evidence_id": "ev-b", "evidence_hash": H4},
        ],
    }


class CanonicalEpistemicVerificationTests(unittest.TestCase):
    def test_target_is_deterministic_and_binds_exact_lineage(self):
        a = build_target(canonical_state(), "belief-growth")
        b = build_target(canonical_state(), "belief-growth")
        self.assertEqual(a, b)
        self.assertTrue(a.target_id.startswith("epvt-"))
        self.assertEqual(a.state_id, "eps-demo")
        self.assertEqual(a.state_hash, H1)
        self.assertEqual(a.belief_hash, H2)
        self.assertEqual([x.evidence_id for x in a.evidence_bindings], ["ev-a", "ev-b"])
        self.assertEqual([x.evidence_hash for x in a.evidence_bindings], [H3, H4])
        self.assertFalse(any(a.authority.__dict__.values()))

    def test_only_verify_later_beliefs_create_prospective_targets(self):
        targets = build_targets(canonical_state())
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].belief_id, "belief-growth")
        with self.assertRaises(CanonicalEpistemicVerificationError):
            build_target(canonical_state(), "belief-no-target")

    def test_tampered_state_belief_or_evidence_binding_fails_closed(self):
        target = build_target(canonical_state(), "belief-growth")
        for tampered in (
            replace(target, state_hash="a" * 64),
            replace(target, belief_hash="b" * 64),
            replace(target, evidence_bindings=(replace(target.evidence_bindings[0], evidence_hash="c" * 64),) + target.evidence_bindings[1:]),
        ):
            with self.assertRaises(CanonicalEpistemicVerificationError):
                verify_target(tampered)

    def test_serialized_target_is_self_verifying(self):
        target = build_target(canonical_state(), "belief-growth")
        self.assertEqual(target_from_dict(target.to_dict()), target)
        payload = target.to_dict()
        payload["predicted_probability"] = 0.71
        with self.assertRaises(CanonicalEpistemicVerificationError):
            target_from_dict(payload)

    def test_outcome_must_be_later_than_frozen_state(self):
        target = build_target(canonical_state(), "belief-growth")
        with self.assertRaises(CanonicalEpistemicVerificationError):
            resolve_target(target, outcome=True, verified_at="2026-09-03T08:00:00Z", outcome_source="official")

    def test_resolution_is_deterministic_scored_and_read_only(self):
        target = build_target(canonical_state(), "belief-growth")
        kwargs = dict(outcome=True, verified_at="2026-09-04T08:00:00Z", outcome_source="official", outcome_ref="ref-1")
        a = resolve_target(target, **kwargs)
        b = resolve_target(target, **kwargs)
        self.assertEqual(a, b)
        self.assertTrue(a.verification_id.startswith("epvv-"))
        self.assertEqual(a.brier_score, 0.09)
        self.assertGreater(a.log_loss, 0.0)
        self.assertFalse(any(a.authority.__dict__.values()))
        self.assertEqual(verification_from_dict(a.to_dict()), a)
        verify_verification(a)

    def test_tampered_scoring_or_lineage_is_rejected(self):
        target = build_target(canonical_state(), "belief-growth")
        row = resolve_target(target, outcome=False, verified_at="2026-09-04T08:00:00Z", outcome_source="official")
        with self.assertRaises(CanonicalEpistemicVerificationError):
            verify_verification(replace(row, brier_score=0.01))
        with self.assertRaises(CanonicalEpistemicVerificationError):
            verify_verification(replace(row, target_hash="f" * 64))

    def test_existing_belief_calibration_accepts_canonical_adapter(self):
        target = build_target(canonical_state(), "belief-growth")
        row = resolve_target(target, outcome=True, verified_at="2026-09-04T08:00:00Z", outcome_source="official")
        record = calibration_record(row)
        report = build_calibration_report([record])
        self.assertEqual(report["count_calibration_eligible"], 1)
        self.assertEqual(report["overall"]["count"], 1)
        self.assertEqual(report["overall"]["mean_brier"], 0.09)
        self.assertEqual(record["canonical_state_hash"], H1)
        self.assertFalse(record["legacy"])

    def test_runtime_builder_is_prospective_idempotent_and_resolves_explicit_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "canonical_epistemic_state.json").write_text(json.dumps(canonical_state()), encoding="utf-8")
            first = build_runtime(root)
            second = build_runtime(root)
            self.assertEqual(first["targets_total"], 1)
            self.assertEqual(second["targets_total"], 1)
            target = target_from_dict(json.loads((root / "epistemic_verification_targets.jsonl").read_text().strip()))
            outcome = {
                "target_id": target.target_id,
                "outcome": True,
                "verified_at": "2026-09-04T08:00:00Z",
                "outcome_source": "official",
                "outcome_ref": "release-1",
            }
            (root / "epistemic_outcomes.jsonl").write_text(json.dumps(outcome) + "\n", encoding="utf-8")
            resolved = build_runtime(root)
            self.assertEqual(resolved["verifications_total"], 1)
            verification = verification_from_dict(json.loads((root / "canonical_epistemic_verifications.jsonl").read_text().strip()))
            self.assertEqual(verification.target_id, target.target_id)
            self.assertEqual(verification.state_hash, H1)

    def test_unknown_outcome_cannot_fabricate_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "canonical_epistemic_state.json").write_text(json.dumps(canonical_state()), encoding="utf-8")
            (root / "epistemic_outcomes.jsonl").write_text(json.dumps({
                "target_id": "epvt-does-not-exist", "outcome": True,
                "verified_at": "2026-09-04T08:00:00Z", "outcome_source": "official",
            }) + "\n", encoding="utf-8")
            with self.assertRaises(CanonicalEpistemicVerificationError):
                build_runtime(root)


if __name__ == "__main__":
    unittest.main()
