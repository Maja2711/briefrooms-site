import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import brace_entity_calibration_diagnostics as d


NOW = datetime(2027, 8, 20, 12, 0, tzinfo=timezone.utc)


def make_source(root: Path, records):
    forecasts = []
    verifications = []
    closures = {}
    for i, rec in enumerate(records):
        fid = f"f{i}"
        vid = f"v{i}"
        entity = rec.get("entity", f"e{i%3}")
        dimension = rec.get("dimension", "revenue_durability")
        sector = rec.get("sector", ["Technology", "Financials", "Health Care"][i % 3])
        forecast_at = rec.get("forecast_at", f"2027-0{1+(i%6)}-01T00:00:00Z")
        target_at = rec.get("target_at", f"2027-{min(9, 5+(i%4)):02d}-01T00:00:00Z")
        verified_at = rec.get("verified_at", f"2027-{min(8, 2+(i%6)):02d}-15T00:00:00Z")
        outcome_observed_at = rec.get("outcome_observed_at", verified_at)
        belief_id = f"entity.{entity}.{dimension}"
        forecasts.append({
            "forecast_id": fid,
            "belief_id": belief_id,
            "forecast_at": forecast_at,
            "target_at": target_at,
            "regime": "entity_fundamental_reporting_v1",
            "metadata": {
                "dimension": dimension,
                "sector": sector,
                "reporting_regime": rec.get("reporting_regime", "domestic_sec_periodic_reporting"),
            },
        })
        p = rec.get("p", 0.6)
        outcome = rec.get("outcome", True)
        verifications.append({
            "verification_id": vid,
            "forecast_id": fid,
            "belief_id": belief_id,
            "predicted_probability": p,
            "forecast_confidence": rec.get("confidence", 0.5),
            "outcome": outcome,
            "forecast_at": forecast_at,
            "target_at": target_at,
            "verified_at": verified_at,
            "entity": entity,
            "regime": "entity_fundamental_reporting_v1",
            "outcome_source": d.EXPECTED_OUTCOME_SOURCE,
            "calibration_eligible": rec.get("calibration_eligible", True),
            "legacy": False,
        })
        closures[fid] = {
            "forecast_id": fid,
            "status": "verified_support" if outcome else "verified_oppose",
            "closed_at": verified_at,
            "target_at": target_at,
            "outcome_observed_at": outcome_observed_at,
            "verification_id": vid,
            "calibration_eligible": True,
        }
    core = {"mode": "shadow", "schema_version": 2, "forecasts": forecasts, "verifications": verifications}
    runtime = {
        "mode": d.MODE,
        "contract_version": d.PR15_CONTRACT_VERSION,
        "forecast_closures": closures,
    }
    report = {
        "mode": d.MODE,
        "contract_version": d.PR15_CONTRACT_VERSION,
        "state_boundary": {"entity_bridge_enabled": False, "with_without_bridge_enabled": False},
    }
    core_path = root / "core.json"
    runtime_path = root / "runtime.json"
    report_path = root / "report.json"
    core_path.write_text(json.dumps(core))
    runtime_path.write_text(json.dumps(runtime))
    report_path.write_text(json.dumps(report))
    return core_path, runtime_path, report_path


class EntityCalibrationDiagnosticsTests(unittest.TestCase):
    def test_empty_input_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_source(root, [])
            report = d.run(root / "state", belief_core_state_path=paths[0], pr15_runtime_state_path=paths[1], pr15_report_path=paths[2], as_of=NOW)
            self.assertEqual(report["calibration"]["n"], 0)
            self.assertFalse(report["promotion_readiness"]["eligible_for_promotion_review"])
            self.assertIsNone(report["effective_n"]["promotion_grade_effective_n"])

    def test_brier_and_log_loss_are_computed_from_frozen_forecasts(self):
        rows = [
            {"predicted_probability": 0.8, "outcome": True},
            {"predicted_probability": 0.2, "outcome": False},
        ]
        m = d.calibration_metrics(rows)
        self.assertAlmostEqual(m["brier_score"], 0.04, places=9)
        self.assertAlmostEqual(m["outcome_rate"], 0.5)
        self.assertAlmostEqual(m["mean_probability"], 0.5)
        self.assertAlmostEqual(m["hit_rate_at_0_5"], 1.0)

    def test_fixed_bins_include_probability_one(self):
        rows = [{"predicted_probability": 1.0, "outcome": True}]
        m = d.calibration_metrics(rows)
        self.assertEqual(m["fixed_probability_bins"][-1]["count"], 1)

    def test_serial_effective_n_penalizes_positive_residual_dependence(self):
        rows = []
        probs = [0.2, 0.25, 0.3, 0.7, 0.75, 0.8]
        outcomes = [False, False, False, True, True, True]
        for i, (p, y) in enumerate(zip(probs, outcomes)):
            rows.append({"verified_at": f"2027-0{i+1}-15T00:00:00Z", "verification_id": str(i), "predicted_probability": p, "outcome": y})
        out = d.serial_effective_n(rows)
        self.assertEqual(out["status"], "ok")
        self.assertLessEqual(out["effective_n"], len(rows))

    def test_cluster_icc_requires_multiple_repeated_groups(self):
        rows = [
            {"entity": "a", "predicted_probability": 0.6, "outcome": True},
            {"entity": "b", "predicted_probability": 0.6, "outcome": False},
            {"entity": "c", "predicted_probability": 0.6, "outcome": True},
        ]
        out = d.cluster_icc_effective_n(rows, "entity")
        self.assertNotEqual(out["status"], "ok")
        self.assertIsNone(out["effective_n"])

    def test_overlap_reports_non_overlapping_cap(self):
        rows = [
            {"forecast_at": "2027-01-01T00:00:00Z", "target_at": "2027-05-01T00:00:00Z", "forecast_id": "a"},
            {"forecast_at": "2027-02-01T00:00:00Z", "target_at": "2027-06-01T00:00:00Z", "forecast_id": "b"},
            {"forecast_at": "2027-07-01T00:00:00Z", "target_at": "2027-11-01T00:00:00Z", "forecast_id": "c"},
        ]
        out = d.overlap_diagnostics(rows)
        self.assertEqual(out["max_non_overlapping_window_count"], 2)
        self.assertGreater(out["overlapping_pair_fraction"], 0)

    def test_concentration_reports_hhi_without_promotion_threshold(self):
        rows = [{"entity": "a"}, {"entity": "a"}, {"entity": "b"}]
        out = d.concentration(rows, "entity")
        self.assertAlmostEqual(out["hhi"], 5 / 9)
        self.assertEqual(out["status"], "descriptive_no_frozen_promotion_threshold")

    def test_non_calibration_eligible_verification_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_source(root, [{"calibration_eligible": False}])
            report = d.run(root / "state", belief_core_state_path=paths[0], pr15_runtime_state_path=paths[1], pr15_report_path=paths[2], as_of=NOW)
            self.assertEqual(report["calibration"]["n"], 0)

    def test_wrong_outcome_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_source(root, [{"p": 0.7, "outcome": True}])
            core = json.loads(paths[0].read_text())
            core["verifications"][0]["outcome_source"] = "manual"
            paths[0].write_text(json.dumps(core))
            report = d.run(root / "state", belief_core_state_path=paths[0], pr15_runtime_state_path=paths[1], pr15_report_path=paths[2], as_of=NOW)
            self.assertEqual(report["calibration"]["n"], 0)
            self.assertEqual(report["data_quality"]["critical_issue_count"], 1)

    def test_same_source_fingerprint_does_not_append_duplicate_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_source(root, [{"p": 0.7, "outcome": True}])
            state_dir = root / "state"
            d.run(state_dir, belief_core_state_path=paths[0], pr15_runtime_state_path=paths[1], pr15_report_path=paths[2], as_of=NOW)
            r2 = d.run(state_dir, belief_core_state_path=paths[0], pr15_runtime_state_path=paths[1], pr15_report_path=paths[2], as_of=NOW)
            state = json.loads((state_dir / d.STATE_FILENAME).read_text())
            self.assertEqual(len(state["diagnostic_snapshots"]), 1)
            self.assertFalse(r2["sample"]["new_diagnostic_snapshot_this_run"])

    def test_regime_robustness_remains_unavailable_without_frozen_context(self):
        out = d.regime_diagnostics([{"regime": "entity_fundamental_reporting_v1"}])
        self.assertFalse(out["multi_regime_robustness_assessable"])
        self.assertIsNone(out["promotion_regime_robust"])

    def test_all_safety_controls_false_and_no_promotion_gate(self):
        self.assertTrue(d.safety_controls())
        self.assertTrue(all(v is False for v in d.safety_controls().values()))
        self.assertFalse(d.capabilities()["with_without_bridge_enabled"])
        self.assertFalse(d.capabilities()["promotion_gate_enabled"])
        self.assertFalse(d.promotion_evidence_standard()["automatic_promotion"])


if __name__ == "__main__":
    unittest.main()
