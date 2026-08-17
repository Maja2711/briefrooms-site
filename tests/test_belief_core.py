from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from belief_core import BeliefCore, BeliefDefinition, Evidence  # noqa: E402

AS_OF = "2026-08-17T20:00:00Z"


def ev(evidence_id: str, belief_id: str = "trend", direction: int = 1, strength: float = 0.8,
       reliability: float = 0.9, cluster: str | None = None, source: str = "Source A",
       source_type: str = "primary", observed_at: str = "2026-08-17T19:00:00Z") -> Evidence:
    return Evidence(evidence_id=evidence_id, belief_id=belief_id, source=source, observed_at=observed_at,
        direction=direction, strength=strength, reliability=reliability,
        independence_cluster=cluster or evidence_id, source_type=source_type)


class BeliefCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.core = BeliefCore(self.temp.name)
        self.core.register_beliefs([BeliefDefinition("trend", "SPX trend remains bullish", prior_probability=0.5,
            half_life_hours=24, entity="SPX", domain="trend")])
    def tearDown(self) -> None: self.temp.cleanup()

    def test_probability_is_bounded(self) -> None:
        self.core.ingest([ev("e1")]); state = self.core.recompute(AS_OF)["trend"]
        self.assertGreaterEqual(state.probability, 0.0); self.assertLessEqual(state.probability, 1.0)

    def test_confidence_is_not_probability(self) -> None:
        self.core.ingest([ev("e1", strength=1.0, reliability=0.5)]); state = self.core.recompute(AS_OF)["trend"]
        self.assertNotAlmostEqual(state.probability, state.confidence, places=4)

    def test_independence_cluster_prevents_double_counting(self) -> None:
        self.core.ingest([ev("primary", cluster="earnings-q2", source="Company", source_type="primary"),
            ev("wire-copy", cluster="earnings-q2", source="Wire", source_type="derived", strength=1.0, reliability=1.0)])
        state = self.core.recompute(AS_OF)["trend"]; self.assertEqual(state.independent_clusters, 1)
        codes = {x["code"] for x in self.core.dashboard_snapshot(AS_OF)["audit_findings"]}
        self.assertIn("duplicate_provenance_cluster", codes)

    def test_decay_reduces_old_evidence_effect(self) -> None:
        fresh = BeliefCore(Path(self.temp.name)/"fresh"); fresh.register_beliefs([BeliefDefinition("trend", "Trend", half_life_hours=24)])
        fresh.ingest([ev("f", observed_at="2026-08-17T19:00:00Z")]); p_fresh = fresh.recompute(AS_OF)["trend"].probability
        old = BeliefCore(Path(self.temp.name)/"old"); old.register_beliefs([BeliefDefinition("trend", "Trend", half_life_hours=24)])
        old.ingest([ev("o", observed_at="2026-08-13T20:00:00Z")]); p_old = old.recompute(AS_OF)["trend"].probability
        self.assertGreater(p_fresh, p_old); self.assertGreater(p_old, 0.5)

    def test_contradiction_lowers_confidence(self) -> None:
        clean = BeliefCore(Path(self.temp.name)/"clean"); clean.register_beliefs([BeliefDefinition("trend", "Trend")])
        clean.ingest([ev("a"), ev("b", cluster="b", source="Source B")]); clean_state = clean.recompute(AS_OF)["trend"]
        conflict = BeliefCore(Path(self.temp.name)/"conflict"); conflict.register_beliefs([BeliefDefinition("trend", "Trend")])
        conflict.ingest([ev("a"), ev("b", cluster="b", source="Source B", direction=-1)]); conflict_state = conflict.recompute(AS_OF)["trend"]
        self.assertGreater(conflict_state.contradiction_score, 0.5); self.assertLess(conflict_state.confidence, clean_state.confidence)

    def test_alternative_hypotheses_normalize(self) -> None:
        self.core.register_beliefs([BeliefDefinition("soft", "Soft landing", .5, alternative_group="macro-regime"),
            BeliefDefinition("recession", "Recession", .3, alternative_group="macro-regime"),
            BeliefDefinition("inflation", "Inflation return", .2, alternative_group="macro-regime")])
        states = self.core.recompute(AS_OF); total = sum(states[k].probability for k in ("soft","recession","inflation"))
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_provenance_is_preserved(self) -> None:
        item = Evidence("e1", "trend", "Company filing", "2026-08-17T19:00:00Z", 1, .8, .95, "q2",
            source_type="primary", source_ref="filing://q2", derived_from=("raw-1",))
        self.core.ingest([item]); self.core.recompute(AS_OF); snap = self.core.dashboard_snapshot(AS_OF)
        self.assertEqual(snap["evidence"]["e1"]["source_ref"], "filing://q2"); self.assertEqual(snap["evidence"]["e1"]["derived_from"], ["raw-1"])

    def test_persistence_round_trip(self) -> None:
        self.core.ingest([ev("e1")]); original = self.core.recompute(AS_OF)["trend"]; loaded = BeliefCore(self.temp.name)
        self.assertAlmostEqual(loaded.beliefs["trend"].probability, original.probability); self.assertIn("e1", loaded.evidence)

    def test_ingest_is_idempotent_but_immutable(self) -> None:
        item = ev("e1"); self.core.ingest([item, item]); self.assertEqual(len(self.core.evidence), 1)
        with self.assertRaises(ValueError): self.core.ingest([ev("e1", strength=.2)])

    def test_verification_records_brier_without_action(self) -> None:
        self.core.ingest([ev("e1")]); self.core.recompute(AS_OF); verification = self.core.verify("trend", True, AS_OF)
        self.assertGreaterEqual(verification.brier_score, 0); self.assertEqual(self.core.calibration_summary()["count"], 1)
        self.assertFalse(hasattr(self.core, "execute_trade"))

    def test_dashboard_hard_codes_shadow_safety_controls(self) -> None:
        self.core.recompute(AS_OF); snapshot = self.core.dashboard_snapshot(AS_OF)
        self.assertEqual(snapshot["mode"], "shadow"); self.assertFalse(snapshot["controls"]["decision_engine_connected"])
        self.assertFalse(snapshot["controls"]["trade_execution_enabled"]); self.assertFalse(snapshot["controls"]["policy_output_enabled"])
        json.dumps(snapshot)


if __name__ == "__main__": unittest.main()
