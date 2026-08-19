from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import belief_data_quality_adapter as dq
import belief_calibration_foundation as foundation

NOW = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def belief_state(verifications=None, forecasts=None) -> dict:
    return {
        "schema_version": 2,
        "mode": "shadow",
        "definitions": [],
        "evidence": [],
        "beliefs": [],
        "history": {},
        "forecasts": list(forecasts or []),
        "verifications": list(verifications or []),
    }


def verification(fid: str, p: float, outcome: bool, *, regime="normal", horizon="<=1d") -> dict:
    y = 1.0 if outcome else 0.0
    return {
        "verification_id": f"v-{fid}",
        "forecast_id": fid,
        "forecast_set_id": f"set-{fid}",
        "belief_id": "spx.trend.bullish",
        "predicted_probability": p,
        "forecast_confidence": 0.7,
        "outcome": outcome,
        "forecast_at": "2026-08-18T14:00:00Z",
        "target_at": "2026-08-18T20:00:00Z",
        "verified_at": "2026-08-18T20:05:00Z",
        "horizon_hours": 6,
        "horizon_bucket": horizon,
        "domain": "trend",
        "entity": "SPX",
        "regime": regime,
        "alternative_group": None,
        "outcome_rule": "spy_close_above_reference",
        "outcome_source": "Yahoo Finance chart",
        "outcome_ref": "yahoo:SPY",
        "brier_score": round((p - y) ** 2, 6),
        "log_loss": 0.2,
        "evidence_snapshot": [],
        "calibration_eligible": True,
        "legacy": False,
        "note": "",
    }


class DataQualityAdapterTests(unittest.TestCase):
    def test_stale_invalid_and_source_health_are_explicit(self):
        rows = [
            {
                "observation_id": "o1",
                "adapter": "market_data",
                "source": "Yahoo",
                "status": "ok",
                "observed_at": "2026-08-19T15:58:00Z",
            },
            {
                "observation_id": "o2",
                "adapter": "market_data",
                "source": "Yahoo",
                "status": "stale",
                "observed_at": "2026-08-18T15:58:00Z",
            },
            {
                "observation_id": "o3",
                "adapter": "macro_data",
                "source": "BLS",
                "status": "invalid",
                "observed_at": "2026-08-19T14:00:00Z",
            },
        ]
        report = dq.observation_quality(rows, NOW)
        self.assertEqual(report["count"], 3)
        self.assertAlmostEqual(report["stale_invalid_rate"], 2 / 3, places=6)
        self.assertEqual(report["by_source"]["Yahoo"]["observations"], 2)
        self.assertIn(report["by_source"]["Yahoo"]["health"], {"watch", "degraded"})

    def test_forecast_latency_and_due_coverage_are_point_in_time(self):
        forecasts = [
            {
                "forecast_id": "f1",
                "forecast_at": "2026-08-18T14:00:00Z",
                "target_at": "2026-08-18T20:00:00Z",
                "predicted_probability": 0.65,
                "evidence_snapshot": [
                    {"observed_at": "2026-08-18T13:00:00Z", "freshness": 0.9}
                ],
            },
            {
                "forecast_id": "f2",
                "forecast_at": "2026-08-19T14:00:00Z",
                "target_at": "2026-08-20T14:00:00Z",
                "predicted_probability": 0.55,
                "evidence_snapshot": [
                    {"observed_at": "2026-08-19T13:30:00Z", "freshness": 0.95}
                ],
            },
        ]
        report = dq.forecast_quality(forecasts, [verification("f1", 0.65, True)], NOW)
        self.assertEqual(report["due_forecasts"], 1)
        self.assertEqual(report["due_verified"], 1)
        self.assertEqual(report["due_unresolved"], 0)
        self.assertEqual(report["frozen_forecast_coverage"], 1.0)
        self.assertAlmostEqual(report["forecast_evidence_latency_hours"]["mean"], 0.75)


class CalibrationFoundationTests(unittest.TestCase):
    def test_no_gse_artifact_is_reported_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "belief"
            write_json(root / "state.json", belief_state())
            write_json(root / "scheduler.json", {"schema_version": 2, "gaps": []})
            report = foundation.build_report(root, gse_state_dir=Path(tmp) / "missing", now=NOW)
            self.assertEqual(report["report_name"], "BELIEF_CALIBRATION_REPORT")
            self.assertFalse(report["gse_forecast_source"]["available"])
            self.assertFalse(report["promotion_gate"]["decision_influence_allowed"])
            self.assertFalse(report["promotion_gate"]["bounded_modifier_allowed"])
            self.assertFalse(report["controls"]["automatic_tuning_enabled"])

    def test_gse_is_calibrated_as_forecast_source_not_belief_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            belief = Path(tmp) / "belief"
            gse = Path(tmp) / "gse"
            write_json(belief / "state.json", belief_state([verification("bf1", 0.7, True)]))
            write_json(belief / "scheduler.json", {"schema_version": 2, "gaps": []})
            write_json(gse / "gse_state.json", {
                "schema_version": 1,
                "mode": "shadow",
                "last_run_at": "2026-08-19T15:17:00Z",
                "last_status": {"evidence_collected": 2, "forecasts_frozen": 1},
                "controls": {
                    "trade_execution_enabled": False,
                    "policy_output_enabled": False,
                    "automatic_tuning_enabled": False,
                    "decision_engine_connected": False,
                },
                "cadence": {"evidence_scan": "hourly_24x7"},
            })
            write_json(gse / "gse_calibration.json", {
                "schema_version": 1,
                "mode": "shadow",
                "overall": {"count": 1, "mean_brier": 0.09},
                "automatic_tuning_enabled": False,
            })
            write_jsonl(gse / "gse_evidence.jsonl", [
                {
                    "evidence_id": "ge1",
                    "source": "United Nations News",
                    "source_type": "primary",
                    "published_at": "2026-08-19T14:00:00Z",
                    "reliability": 0.86,
                }
            ])
            write_jsonl(gse / "gse_forecasts.jsonl", [
                {
                    "forecast_id": "gf1",
                    "asset": "SPX",
                    "forecast_at": "2026-08-18T12:00:00Z",
                    "target_at": "2026-08-19T12:00:00Z",
                    "predicted_probability": 0.7,
                    "baseline_value": 650.0,
                    "scenario_snapshot": [{"scenario_type": "x"}],
                    "evidence_snapshot": [{"published_at": "2026-08-18T11:00:00Z"}],
                }
            ])
            write_jsonl(gse / "gse_verifications.jsonl", [
                {
                    "verification_id": "gv1",
                    "forecast_id": "gf1",
                    "asset": "SPX",
                    "horizon_hours": 24,
                    "predicted_probability": 0.7,
                    "outcome": True,
                    "brier_score": 0.09,
                    "log_loss": 0.356675,
                    "calibration_eligible": True,
                }
            ])
            report = foundation.build_report(belief, gse_state_dir=gse, now=NOW)
            source = report["gse_forecast_source"]
            self.assertTrue(source["available"])
            self.assertTrue(source["safety_controls_verified"])
            self.assertEqual(source["calibration"]["canonical_recomputed"]["count"], 1)
            self.assertEqual(source["future_adapter_role"], "forecast_source_only_until_separate_GSE_to_Belief_Core_read_only_adapter_PR")
            self.assertFalse(source["decision_influence"])
            self.assertFalse(report["promotion_gate"]["decision_influence_allowed"])

    def test_core_calibration_metrics_are_exposed_in_one_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "belief"
            verifications = [verification("f1", 0.8, True), verification("f2", 0.7, False)]
            write_json(root / "state.json", belief_state(verifications))
            write_json(root / "scheduler.json", {"schema_version": 2, "gaps": []})
            report = foundation.build_report(root, now=NOW)
            self.assertEqual(report["belief_calibration"]["count_calibration_eligible"], 2)
            self.assertIsNotNone(report["proper_scoring"]["brier"])
            self.assertIsNotNone(report["proper_scoring"]["log_loss"])
            self.assertIsNotNone(report["proper_scoring"]["ece"])
            self.assertIsNotNone(report["proper_scoring"]["mce"])
            self.assertIn("reliability_curve", report["proper_scoring"])
            self.assertIn("by_regime", report["regime_horizon_slices"])
            self.assertIn("by_horizon", report["regime_horizon_slices"])


if __name__ == "__main__":
    unittest.main()
