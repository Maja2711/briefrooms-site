from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import investments_wes_belief_calibration as cal


def bridge_record(record_id="wes-spx-belief-1", decision_id="wes-1", relationship="STRONG_CONFLICT", strength=0.8, captured_at="2026-08-20T09:20:00Z"):
    return {
        "record_id": record_id,
        "decision_id": decision_id,
        "week_id": "2026-W34",
        "instrument_id": "sp500_futures",
        "captured_at": captured_at,
        "engine_belief_calibration_eligible": True,
        "wes": {
            "available": True,
            "actionable": True,
            "direction": "long",
            "strategy_id": "weekly_trend",
            "raw_score": 80.0,
            "entry_class": "monday_weekly",
            "entry_price": 7800.0,
            "entry_captured_at": "2026-08-20T09:05:00Z",
            "risk_plan": {"stop_loss_price": 7700.0, "take_profit_price": 8000.0},
        },
        "belief_state": {
            "available": True,
            "forecast_set_id": "set-1",
            "forecast_at": "2026-08-20T08:30:00Z",
            "stance": "defensive" if "CONFLICT" in relationship else "risk_on",
            "confidence": 0.85,
            "risk_on_probability_mean": 0.25 if "CONFLICT" in relationship else 0.75,
            "snapshot_sha256": "beliefsha",
        },
        "relationship": {"class": relationship, "strength": strength, "alpha_eligible": True},
    }


def source_record(decision_id="wes-1", wes_net=-1.0, v5_net=-0.5, incremental=-0.5):
    return {
        "decision_id": decision_id,
        "week_id": "2026-W34",
        "wes_actual": {
            "direction": "long",
            "strategy_id": "weekly_trend",
            "entry_price": 7800.0,
            "entry_captured_at": "2026-08-20T09:05:00Z",
        },
        "outcome": {
            "status": "resolved_incremental_alpha",
            "closed_at": "2026-08-20T18:00:00Z",
            "exit_reason": "stop_loss",
            "wes_net_result_percent": wes_net,
            "v5_counterfactual_net_result_percent": v5_net,
            "incremental_wes_vs_v5_percent": incremental,
        },
    }


class ContractTests(unittest.TestCase):
    def test_safety_is_hard_off(self):
        self.assertTrue(cal.safety_controls())
        self.assertTrue(all(v is False for v in cal.safety_controls().values()))
        self.assertFalse(cal.evaluation_policy()["production_modifier_proposed"])

    def test_conflict_only_reduces_risk_and_records_negative_score_modifier(self):
        result = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))
        contract = result["contract"]
        self.assertEqual(result["status"], "frozen")
        self.assertAlmostEqual(contract["with_belief_hypothetical"]["risk_scale"], 0.92)
        self.assertAlmostEqual(contract["with_belief_hypothetical"]["score_modifier_points_telemetry_only"], -1.6)
        self.assertFalse(contract["with_belief_hypothetical"]["direction_changed"])

    def test_agreement_never_increases_exposure(self):
        result = cal.make_contract(bridge_record(relationship="STRONG_AGREEMENT", strength=0.9), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))
        contract = result["contract"]
        self.assertEqual(contract["with_belief_hypothetical"]["risk_scale"], 1.0)
        self.assertAlmostEqual(contract["with_belief_hypothetical"]["score_modifier_points_telemetry_only"], 1.8)

    def test_late_contract_is_missed_not_reconstructed(self):
        result = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "missed_not_reconstructed")
        self.assertIsNone(result["contract"])


class SettlementTests(unittest.TestCase):
    def test_conflict_attenuation_improves_loss(self):
        contract = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))["contract"]
        out = cal.settle_contract(contract, source_record(wes_net=-1.0, v5_net=-0.5, incremental=-0.5), datetime(2026, 8, 20, 18, 5, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "resolved")
        self.assertAlmostEqual(out["with_belief_hypothetical_net_percent"], -0.92)
        self.assertAlmostEqual(out["delta_pnl_percent"], 0.08)
        self.assertAlmostEqual(out["with_belief_vs_v5_incremental_alpha_percent"], -0.42)

    def test_conflict_attenuation_reduces_profitable_outcome_too(self):
        contract = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))["contract"]
        out = cal.settle_contract(contract, source_record(wes_net=1.0, v5_net=0.2, incremental=0.8), datetime(2026, 8, 20, 18, 5, tzinfo=timezone.utc))
        self.assertAlmostEqual(out["with_belief_hypothetical_net_percent"], 0.92)
        self.assertAlmostEqual(out["delta_pnl_percent"], -0.08)

    def test_source_identity_mismatch_fails_closed(self):
        contract = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))["contract"]
        bad = source_record()
        bad["wes_actual"]["direction"] = "short"
        out = cal.settle_contract(contract, bad, datetime(2026, 8, 20, 18, 5, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "source_identity_mismatch")

    def test_unresolved_wes_outcome_stays_pending(self):
        contract = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))["contract"]
        src = source_record()
        src["outcome"]["wes_net_result_percent"] = None
        out = cal.settle_contract(contract, src, datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(out["status"], "pending_wes_outcome")


class EndToEndTests(unittest.TestCase):
    def test_first_run_activates_without_backfill_then_contracts_and_settles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir()
            wes_source = root / "wes.json"
            wes_source.write_text(json.dumps({"records": []}))
            (bridge_dir / "engine_belief_observations.jsonl").write_text("")
            first = cal.run_calibration(root / "cal", bridge_dir, wes_source, datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc))
            self.assertEqual(first["contracts_total"], 0)
            row = bridge_record(captured_at="2026-08-20T09:20:00Z")
            (bridge_dir / "engine_belief_observations.jsonl").write_text(json.dumps(row) + "\n")
            wes_source.write_text(json.dumps({"records": [source_record()]}))
            second = cal.run_calibration(root / "cal", bridge_dir, wes_source, datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))
            self.assertEqual(second["contracts_added"], 1)
            self.assertEqual(second["settled_pairs"], 1)
            third = cal.run_calibration(root / "cal", bridge_dir, wes_source, datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc))
            self.assertEqual(third["contracts_added"], 0)
            self.assertEqual(third["settled_pairs"], 1)

    def test_pre_activation_bridge_record_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge_dir = root / "bridge"
            bridge_dir.mkdir()
            (bridge_dir / "engine_belief_observations.jsonl").write_text("")
            wes_source = root / "wes.json"
            wes_source.write_text(json.dumps({"records": []}))
            cal.run_calibration(root / "cal", bridge_dir, wes_source, datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
            old = bridge_record(captured_at="2026-08-20T09:20:00Z")
            (bridge_dir / "engine_belief_observations.jsonl").write_text(json.dumps(old) + "\n")
            out = cal.run_calibration(root / "cal", bridge_dir, wes_source, datetime(2026, 8, 20, 12, 5, tzinfo=timezone.utc))
            self.assertEqual(out["contracts_total"], 0)


class ReportTests(unittest.TestCase):
    def test_relationship_report_contains_wes_v5_alpha_and_with_without(self):
        conflict = cal.make_contract(bridge_record(), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))["contract"]
        agree = cal.make_contract(bridge_record(record_id="r2", decision_id="wes-2", relationship="STRONG_AGREEMENT", strength=0.9), datetime(2026, 8, 20, 9, 25, tzinfo=timezone.utc))["contract"]
        rows = [
            {"record_id": "r1", "contract": conflict, "settlement": cal.settle_contract(conflict, source_record(), datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc))},
            {"record_id": "r2", "contract": agree, "settlement": cal.settle_contract(agree, source_record(decision_id="wes-2", wes_net=1.0, v5_net=0.2, incremental=0.8), datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc))},
        ]
        report = cal.build_report(rows, {"activated_at": "2026-08-20T09:00:00Z"}, datetime(2026, 8, 20, 19, 0, tzinfo=timezone.utc))
        self.assertEqual(report["sample"]["settled_pairs"], 2)
        self.assertIn("STRONG_CONFLICT", report["by_relationship"])
        self.assertIn("STRONG_AGREEMENT", report["by_relationship"])
        self.assertIsNotNone(report["overall"]["mean_wes_vs_v5_incremental_alpha_percent"])
        self.assertFalse(report["active_decision_influence"])

    def test_sample_thresholds_are_descriptive_not_promotion(self):
        report = cal.build_report([], {"activated_at": "x"}, datetime(2026, 8, 20, 19, 0, tzinfo=timezone.utc))
        self.assertEqual(report["sample"]["minimum_before_descriptive_analysis"], 12)
        self.assertEqual(report["sample"]["minimum_before_relationship_analysis"], 30)
        self.assertFalse(report["bounded_influence_enabled"])


if __name__ == "__main__":
    unittest.main()
