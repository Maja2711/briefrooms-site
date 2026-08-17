from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from belief_core import BeliefCore, BeliefDefinition, Evidence, ForecastSnapshot, horizon_bucket  # noqa: E402
from belief_calibration import build_calibration_report, metrics  # noqa: E402

AS_OF = "2026-08-17T20:00:00Z"
TARGET = "2026-08-18T20:00:00Z"
VERIFY = "2026-08-18T21:00:00Z"


def ev(eid: str, belief_id: str = "trend", direction: int = 1, strength: float = .8,
       reliability: float = .9, cluster: str | None = None, source: str = "Source A",
       source_type: str = "primary", observed_at: str = "2026-08-17T19:00:00Z",
       evidence_type: str = "price") -> Evidence:
    return Evidence(eid, belief_id, source, observed_at, direction, strength, reliability,
                    cluster or eid, source_type=source_type, evidence_type=evidence_type)


class BeliefCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = BeliefCore(self.tmp.name)
        self.core.register_beliefs([BeliefDefinition("trend", "SPX trend bullish", .5, 24, "SPX", "trend", horizon_hours=24)])

    def tearDown(self): self.tmp.cleanup()

    def compute(self):
        self.core.ingest([ev("e1")])
        return self.core.recompute(AS_OF)["trend"]

    def freeze(self, regime="normal"):
        self.compute()
        return self.core.capture_forecast("trend", AS_OF, TARGET, regime=regime)

    def test_probability_bounded(self):
        s = self.compute(); self.assertTrue(0 <= s.probability <= 1)

    def test_probability_confidence_separate(self):
        self.core.ingest([ev("e1", reliability=.4)])
        s = self.core.recompute(AS_OF)["trend"]
        self.assertNotAlmostEqual(s.probability, s.confidence, places=4)

    def test_cluster_dedup(self):
        self.core.ingest([ev("a", cluster="q2"), ev("b", cluster="q2", source="Wire", source_type="derived", strength=1, reliability=1)])
        s = self.core.recompute(AS_OF)["trend"]
        self.assertEqual(s.independent_clusters, 1)
        self.assertEqual(len(s.representative_evidence_ids), 1)

    def test_primary_preferred_as_representative(self):
        self.core.ingest([ev("primary", cluster="x", source_type="primary", strength=.3), ev("derived", cluster="x", source_type="derived", strength=1)])
        s = self.core.recompute(AS_OF)["trend"]
        self.assertEqual(s.representative_evidence_ids, ["primary"])

    def test_decay(self):
        self.core.ingest([ev("fresh")]); fresh = self.core.recompute(AS_OF)["trend"].probability
        t2 = tempfile.TemporaryDirectory(); c2 = BeliefCore(t2.name); c2.register_beliefs([BeliefDefinition("trend","t",.5,24)])
        c2.ingest([ev("old", observed_at="2026-08-13T20:00:00Z")]); old = c2.recompute(AS_OF)["trend"].probability
        t2.cleanup(); self.assertGreater(fresh, old)

    def test_contradiction_reduces_confidence(self):
        self.core.ingest([ev("a"), ev("b", cluster="b", source="B", direction=-1)])
        s = self.core.recompute(AS_OF)["trend"]
        self.assertGreater(s.contradiction_score, .5)

    def test_future_evidence_excluded_and_critical(self):
        self.core.ingest([ev("future", observed_at="2026-08-18T20:00:00Z")])
        s = self.core.recompute(AS_OF)["trend"]
        self.assertEqual(s.independent_clusters, 0)
        snap = self.core.dashboard_snapshot(AS_OF)
        self.assertIn("future_dated_evidence", {x["code"] for x in snap["audit_findings"]})
        self.assertEqual(s.audit_status, "critical")

    def test_alternative_group_normalizes(self):
        self.core.register_beliefs([BeliefDefinition("soft","Soft",.5, alternative_group="macro"),
                                    BeliefDefinition("rec","Rec",.3, alternative_group="macro"),
                                    BeliefDefinition("infl","Infl",.2, alternative_group="macro")])
        states = self.core.recompute(AS_OF)
        self.assertAlmostEqual(sum(states[k].probability for k in ("soft","rec","infl")), 1, places=5)

    def test_evidence_immutable(self):
        x = ev("e1"); self.core.ingest([x, x])
        with self.assertRaises(ValueError): self.core.ingest([ev("e1", strength=.2)])

    def test_definition_immutable_after_forecast(self):
        self.freeze()
        with self.assertRaises(ValueError):
            self.core.register_beliefs([BeliefDefinition("trend","changed",.5,24,"SPX","trend",horizon_hours=24)])

    def test_forecast_frozen(self):
        f = self.freeze()
        self.assertEqual(f.predicted_probability, self.core.beliefs["trend"].probability)
        self.assertEqual(f.target_at, TARGET)
        self.assertTrue(f.evidence_snapshot)

    def test_forecast_idempotent(self):
        f1 = self.freeze(); f2 = self.core.capture_forecast("trend", AS_OF, TARGET, regime="normal")
        self.assertEqual(f1.forecast_id, f2.forecast_id)
        self.assertEqual(len(self.core.forecasts), 1)

    def test_forecast_immutable(self):
        self.freeze()
        with self.assertRaises(ValueError): self.core.capture_forecast("trend", AS_OF, "2026-08-19T20:00:00Z", regime="normal", forecast_id=next(iter(self.core.forecasts)))

    def test_capture_requires_current_timestamp(self):
        self.compute()
        with self.assertRaises(ValueError): self.core.capture_forecast("trend", "2026-08-17T19:00:00Z", TARGET)

    def test_no_future_target(self):
        self.compute()
        with self.assertRaises(ValueError): self.core.capture_forecast("trend", AS_OF, AS_OF)

    def test_verify_before_target_rejected(self):
        f = self.freeze()
        with self.assertRaises(ValueError): self.core.verify_forecast(f.forecast_id, True, "2026-08-18T19:00:00Z")

    def test_verify_uses_frozen_probability_not_current(self):
        f = self.freeze(); frozen = f.predicted_probability
        self.core.ingest([ev("bad", direction=-1, cluster="bad", source="B", observed_at="2026-08-18T19:00:00Z")])
        self.core.recompute("2026-08-18T19:30:00Z")
        self.assertNotEqual(frozen, self.core.beliefs["trend"].probability)
        v = self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertEqual(v.predicted_probability, frozen)

    def test_verification_idempotent(self):
        f = self.freeze(); v1 = self.core.verify_forecast(f.forecast_id, True, VERIFY); v2 = self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertEqual(v1.verification_id, v2.verification_id)

    def test_changed_verification_rejected(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        with self.assertRaises(ValueError): self.core.verify_forecast(f.forecast_id, False, VERIFY)

    def test_legacy_verify_excluded_from_calibration(self):
        self.compute(); v = self.core.verify("trend", True, AS_OF)
        self.assertFalse(v.calibration_eligible)
        self.assertEqual(self.core.calibration_summary()["count_calibration_eligible"], 0)

    def test_verify_convenience_uses_frozen_forecast(self):
        f = self.freeze(); v = self.core.verify("trend", True, VERIFY)
        self.assertEqual(v.forecast_id, f.forecast_id); self.assertTrue(v.calibration_eligible)

    def test_brier_score(self):
        f = self.freeze(); v = self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertAlmostEqual(v.brier_score, (f.predicted_probability - 1) ** 2, places=6)

    def test_calibration_buckets(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        report = self.core.calibration_summary()
        self.assertEqual(sum(x["count"] for x in report["reliability_curve"]), 1)

    def test_source_attribution_direction(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        s = self.core.calibration_summary()["source_performance"]["Source A"]
        self.assertEqual(s["direction_accuracy"], 1.0)

    def test_opposing_source_correct_on_false_outcome(self):
        self.core.ingest([ev("e1", direction=-1)]); self.core.recompute(AS_OF)
        f = self.core.capture_forecast("trend", AS_OF, TARGET)
        self.core.verify_forecast(f.forecast_id, False, VERIFY)
        s = self.core.calibration_summary()["source_performance"]["Source A"]
        self.assertEqual(s["direction_accuracy"], 1.0)

    def test_evidence_type_attribution(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertIn("price", self.core.calibration_summary()["evidence_type_performance"])

    def test_dimensions(self):
        f = self.freeze(regime="risk-on"); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        r = self.core.calibration_summary()
        self.assertIn("trend", r["by_domain"]); self.assertIn("SPX", r["by_entity"]); self.assertIn("risk-on", r["by_regime"])

    def test_horizon_bucket(self):
        self.assertEqual(horizon_bucket(24), "<=1d"); self.assertEqual(horizon_bucket(48), "1-3d")

    def test_confidence_diagnostics_not_probability_calibration(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        rows = self.core.calibration_summary()["confidence_diagnostics"]
        self.assertEqual(sum(x["count"] for x in rows), 1)

    def test_automatic_tuning_disabled(self):
        self.freeze(); r = self.core.calibration_summary()
        self.assertFalse(r["automatic_tuning_enabled"])
        self.assertFalse(self.core.dashboard_snapshot(AS_OF)["controls"]["automatic_tuning_enabled"])

    def test_persistence_round_trip(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        loaded = BeliefCore(self.tmp.name)
        self.assertIn(f.forecast_id, loaded.forecasts); self.assertEqual(len(loaded.verifications), 1)

    def test_v1_verification_migrates_as_ineligible(self):
        state = {"schema_version":1,"mode":"shadow","definitions":[BeliefDefinition("trend","t").to_dict()],"evidence":[],"beliefs":[],"history":{},
                 "verifications":[{"belief_id":"trend","predicted_probability":.8,"outcome":True,"verified_at":VERIFY,"brier_score":.04,"note":"old"}]}
        Path(self.tmp.name,"state.json").write_text(json.dumps(state), encoding="utf-8")
        c = BeliefCore(self.tmp.name); v = next(iter(c.verifications.values()))
        self.assertTrue(v.legacy); self.assertFalse(v.calibration_eligible)

    def test_ledger_hash_chain_valid(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertTrue(self.core.verify_ledger_integrity()["valid"])

    def test_ledger_tamper_detected(self):
        self.freeze(); p = Path(self.tmp.name,"ledger.jsonl"); lines = p.read_text().splitlines(); obj=json.loads(lines[-1]); obj["belief_id"]="tampered"; lines[-1]=json.dumps(obj); p.write_text("\n".join(lines)+"\n")
        self.assertFalse(self.core.verify_ledger_integrity()["valid"])

    def test_unresolved_due(self):
        self.freeze(); rows = self.core.unresolved_forecasts(VERIFY); self.assertTrue(rows[0]["due"])

    def test_verified_removed_from_unresolved(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertEqual(self.core.unresolved_forecasts(), [])

    def test_trajectory_diagnostics(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        t = self.core.trajectory_diagnostics(); self.assertIn(next(iter(self.core.verifications)), t)

    def test_drift_insufficient_sample(self):
        f = self.freeze(); self.core.verify_forecast(f.forecast_id, True, VERIFY)
        self.assertEqual(self.core.calibration_summary()["drift"]["status"], "insufficient_sample")

    def test_overconfidence_recommendation_after_sufficient_sample(self):
        rows=[]
        for i in range(30):
            rows.append({"verification_id":str(i),"belief_id":"x","predicted_probability":.9,"outcome":False,
                         "brier_score":.81,"verified_at":f"2026-01-{(i%28)+1:02d}T00:00:00Z","forecast_confidence":.8,
                         "domain":"d","entity":"e","regime":"r","horizon_bucket":"<=1d","calibration_eligible":True,
                         "evidence_snapshot":[]})
        codes={x["code"] for x in build_calibration_report(rows)["recommendations"]}
        self.assertIn("global_overconfidence", codes)

    def test_dashboard_shadow_controls(self):
        self.compute(); s=self.core.dashboard_snapshot(AS_OF)
        self.assertEqual(s["mode"],"shadow"); self.assertFalse(s["controls"]["trade_execution_enabled"]); self.assertFalse(s["controls"]["policy_output_enabled"])


    def test_cluster_direction_conflict_penalizes_confidence_and_audits(self):
        self.core.ingest([ev("p", cluster="same", direction=1), ev("n", cluster="same", direction=-1, source="B")])
        s = self.core.recompute(AS_OF)["trend"]
        self.assertEqual(s.cluster_conflict_score, 1.0)
        codes = {x["code"] for x in self.core.dashboard_snapshot(AS_OF)["audit_findings"]}
        self.assertIn("cluster_direction_conflict", codes)

    def test_derived_without_lineage_audited(self):
        self.core.ingest([ev("d", source_type="derived")])
        self.core.recompute(AS_OF)
        codes = {x["code"] for x in self.core.dashboard_snapshot(AS_OF)["audit_findings"]}
        self.assertIn("derived_without_lineage", codes)

    def test_known_provenance_cycle_is_critical(self):
        a = Evidence("a","trend","A","2026-08-17T19:00:00Z",1,.8,.9,"a",source_type="derived",derived_from=("b",))
        b = Evidence("b","trend","B","2026-08-17T19:00:00Z",1,.8,.9,"b",source_type="derived",derived_from=("a",))
        self.core.ingest([a,b]); s=self.core.recompute(AS_OF)["trend"]
        self.assertEqual(s.audit_status,"critical")
        self.assertIn("provenance_cycle", {x["code"] for x in self.core.dashboard_snapshot(AS_OF)["audit_findings"]})

    def test_source_ref_cluster_collision_audited(self):
        a = Evidence("a","trend","A","2026-08-17T19:00:00Z",1,.8,.9,"c1",source_ref="same://event")
        b = Evidence("b","trend","A","2026-08-17T19:00:00Z",1,.8,.9,"c2",source_ref="same://event")
        self.core.ingest([a,b]); self.core.recompute(AS_OF)
        self.assertIn("provenance_cluster_collision", {x["code"] for x in self.core.dashboard_snapshot(AS_OF)["audit_findings"]})

    def test_outcome_provenance_persisted(self):
        f=self.freeze(); v=self.core.verify_forecast(f.forecast_id,True,VERIFY,outcome_source="official_close",outcome_ref="px://spx")
        self.assertEqual(v.outcome_source,"official_close"); self.assertEqual(v.outcome_ref,"px://spx")
        self.assertIn("official_close", self.core.calibration_summary()["by_outcome_source"])

    def test_alternative_group_atomic_verification_and_multiclass_metrics(self):
        c = BeliefCore(Path(self.tmp.name)/"alts")
        c.register_beliefs([BeliefDefinition("soft","Soft",.5,24,"SPX","macro","macro",horizon_hours=24),
                            BeliefDefinition("rec","Recession",.3,24,"SPX","macro","macro",horizon_hours=24),
                            BeliefDefinition("infl","Inflation",.2,24,"SPX","macro","macro",horizon_hours=24)])
        c.recompute(AS_OF); fs=c.capture_all_forecasts(AS_OF,regime="normal")
        self.assertEqual(len({f.forecast_set_id for f in fs}),1)
        vs=c.verify_alternative_group("macro","soft",VERIFY,outcome_source="macro_resolution")
        self.assertEqual(len(vs),3); self.assertEqual(sum(v.outcome for v in vs),1)
        m=c.calibration_summary()["alternative_groups"]
        self.assertEqual(m["count"],1); self.assertIsNotNone(m["mean_multiclass_brier"])

    def test_source_reliability_suggestion_never_auto_applies(self):
        rows=[]
        for i in range(15):
            rows.append({"verification_id":str(i),"forecast_id":"f"+str(i),"belief_id":"x","predicted_probability":.8,"outcome":False,
                         "brier_score":.64,"verified_at":f"2026-02-{(i%15)+1:02d}T00:00:00Z","forecast_confidence":.8,
                         "domain":"d","entity":"e","regime":"r","horizon_bucket":"<=1d","calibration_eligible":True,
                         "outcome_source":"official","alternative_group":None,"forecast_set_id":"s"+str(i),
                         "evidence_snapshot":[{"source":"Bad","evidence_type":"rumor","direction":1,"effective_mass":.8,"reliability":.9}]})
        report=build_calibration_report(rows); src=report["source_performance"]["Bad"]
        self.assertLess(src["suggested_reliability_delta"],0)
        self.assertFalse(report["automatic_tuning_enabled"])

    def test_bucket_overconfidence_diagnosis(self):
        rows=[]
        for i in range(8):
            rows.append({"verification_id":str(i),"belief_id":"x","predicted_probability":.85,"outcome":False,"brier_score":.7225,
                         "verified_at":f"2026-03-{i+1:02d}T00:00:00Z","forecast_confidence":.8,"domain":"d","entity":"e",
                         "regime":"r","horizon_bucket":"<=1d","calibration_eligible":True,"evidence_snapshot":[]})
        curve=build_calibration_report(rows)["reliability_curve"]
        b=next(x for x in curve if x["count"])
        self.assertEqual(b["diagnosis"],"overconfident")


if __name__ == "__main__": unittest.main()
