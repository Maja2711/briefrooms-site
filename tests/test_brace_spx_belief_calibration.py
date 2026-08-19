from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import brace_spx_belief_bridge as bridge
from scripts import brace_spx_belief_calibration as cal


def raw_shadow(updated_at="2026-08-20T21:40:00Z", market_date="2026-08-20", target=0.5, applied=0.0):
    snapshots = []
    for i in range(8):
        snapshots.append({
            "candidate_name": f"g6-{i}",
            "families": ["price_trend"],
            "signal": 0.4,
            "regime": "risk_on",
            "target_exposure_next_session": target,
            "applied_exposure_latest_session": applied,
            "shadow_cumulative_return": 0.01,
            "shadow_turnover": 1.0,
        })
    return {
        "schema_version": "6.0.0",
        "generation_id": bridge.GENERATION_ID,
        "candidate_signature": "sig",
        "shadow_start": "2026-08-03",
        "sealed_holdout_end": "2026-07-31",
        "holdout_accessed": False,
        "live_orders": False,
        "autonomous_trading": False,
        "updated_at": updated_at,
        "observations_collected": 70,
        "warmup_required": 70,
        "source_families": list(cal.FAMILY_KEYS),
        "status": "shadow_active_no_orders",
        "observations_remaining": 0,
        "latest_market_date": market_date,
        "latest_regime": "risk_on",
        "family_scores": {
            "price_trend": 0.4,
            "rates": 0.1,
            "liquidity": 0.2,
            "options_vix": 0.3,
        },
        "candidate_snapshots": snapshots,
        "single_champion_selected": False,
    }


def record_for(shadow, stance="risk_on", confidence=0.8, relationship="STRONG_AGREEMENT", record_id="r1"):
    snapshots = shadow["candidate_snapshots"]
    return {
        "record_id": record_id,
        "captured_at": shadow["updated_at"],
        "mode": "shadow",
        "point_in_time": {
            "brace_state_at": shadow["updated_at"],
            "belief_as_of": "2026-08-20T20:00:00Z",
            "prospective_after_activation": True,
            "historical_backfill": False,
        },
        "brace_spx": {
            "available": True,
            "state_type": "parallel_candidate_consensus",
            "stance": "risk_on",
            "confidence": 0.8,
            "reason": "g6_parallel_candidate_consensus_read_only",
            "source": {
                "generation_id": shadow["generation_id"],
                "candidate_signature": shadow["candidate_signature"],
                "updated_at": shadow["updated_at"],
                "latest_market_date": shadow["latest_market_date"],
                "status": shadow["status"],
                "observations_collected": 70,
                "warmup_required": 70,
                "holdout_accessed": False,
                "live_orders": False,
                "autonomous_trading": False,
                "single_champion_selected": False,
            },
            "candidate_consensus": {
                "candidate_count": 8,
                "mean_target_exposure_next_session": sum(row["target_exposure_next_session"] for row in snapshots) / 8,
                "candidate_snapshots_sha256": bridge.canonical_sha256(snapshots),
            },
            "family_scores": dict(shadow["family_scores"]),
            "latest_regime": shadow["latest_regime"],
        },
        "belief_state": {
            "available": True,
            "stance": stance,
            "confidence": confidence,
            "risk_on_probability_mean": 0.7 if stance == "risk_on" else 0.3 if stance == "defensive" else 0.5,
            "reason": "latest_complete_frozen_spx_belief_set",
            "forecast_set_id": "set1",
            "forecast_at": "2026-08-20T20:00:00Z",
            "snapshot_sha256": "beliefhash",
        },
        "relationship": {"class": relationship, "strength": 0.8, "reason": "test"},
        "engine_belief_calibration_eligible": True,
        "alpha_evaluation_enabled": False,
        "with_without_evaluation_enabled": False,
        "decision_influence": False,
        "bounded_modifier_applied": False,
        "controls": bridge.controls(),
        "provenance": {
            "brace_shadow_sha256": bridge.canonical_sha256(shadow),
            "belief_state_source_sha256": "beliefsource",
        },
    }


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def write_market(path: Path, rows):
    lines = ["date,spy_close,irx_annual_yield\n"]
    lines.extend(f"{d},{spy},{irx}\n" for d, spy, irx in rows)
    path.write_text("".join(lines), encoding="utf-8")


class CalibrationContractTests(unittest.TestCase):
    def test_safety_is_hard_off_but_counterfactual_evaluation_is_on(self):
        self.assertTrue(all(value is False for value in cal.safety_controls().values()))
        policy = cal.evaluation_policy()
        self.assertTrue(policy["with_without_evaluation_enabled"])
        self.assertTrue(policy["hypothetical_overlay_only"])
        self.assertTrue(policy["overlay_applied_to_each_frozen_g6_candidate"])
        self.assertFalse(policy["production_modifier_proposed"])

    def test_risk_on_overlay_is_small_predeclared_and_confidence_scaled(self):
        shadow = raw_shadow(target=0.5)
        record = record_for(shadow, stance="risk_on", confidence=0.8)
        contract, reason = cal.build_counterfactual_contract(
            record, shadow, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc)
        )
        self.assertEqual(reason, "contract_frozen")
        self.assertIsNotNone(contract)
        self.assertAlmostEqual(contract["without_belief"]["mean_target_exposure_next_session"], 0.5)
        self.assertAlmostEqual(contract["with_belief_hypothetical"]["requested_tilt"], 0.08)
        self.assertAlmostEqual(contract["with_belief_hypothetical"]["mean_hypothetical_target_exposure"], 0.58)
        self.assertEqual(len(contract["without_belief"]["candidates"]), 8)
        self.assertEqual(len(contract["with_belief_hypothetical"]["candidates"]), 8)
        self.assertFalse(contract["decision_influence"])

    def test_defensive_overlay_reduces_each_candidate_exposure(self):
        shadow = raw_shadow(target=0.5)
        record = record_for(shadow, stance="defensive", confidence=0.6, relationship="STRONG_CONFLICT")
        contract, _ = cal.build_counterfactual_contract(
            record, shadow, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc)
        )
        self.assertAlmostEqual(contract["with_belief_hypothetical"]["mean_hypothetical_target_exposure"], 0.44)
        self.assertTrue(all(row["target_exposure_next_session"] == 0.44 for row in contract["with_belief_hypothetical"]["candidates"]))

    def test_overlay_never_breaks_g6_exposure_cap(self):
        shadow = raw_shadow(target=1.0)
        record = record_for(shadow, stance="risk_on", confidence=1.0)
        contract, _ = cal.build_counterfactual_contract(
            record, shadow, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc)
        )
        self.assertEqual(contract["with_belief_hypothetical"]["mean_hypothetical_target_exposure"], 1.0)
        self.assertEqual(contract["with_belief_hypothetical"]["mean_applied_tilt_after_candidate_clipping"], 0.0)

    def test_raw_g6_hash_mismatch_cannot_create_contract(self):
        shadow = raw_shadow()
        record = record_for(shadow)
        changed = json.loads(json.dumps(shadow))
        changed["candidate_snapshots"][0]["target_exposure_next_session"] = 1.0
        contract, reason = cal.build_counterfactual_contract(
            record, changed, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc)
        )
        self.assertIsNone(contract)
        self.assertEqual(reason, "g6_raw_shadow_hash_mismatch")

    def test_late_contract_capture_is_never_reconstructed(self):
        shadow = raw_shadow()
        record = record_for(shadow)
        contract, reason = cal.build_counterfactual_contract(
            record, shadow, datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)
        )
        self.assertIsNone(contract)
        self.assertEqual(reason, "counterfactual_contract_window_missed")


class AccountingTests(unittest.TestCase):
    def test_g6_one_session_accounting_includes_cash_turnover_and_short_borrow(self):
        result = cal.one_session_portfolio_return(0.5, 0.0, 0.01, 5.0)
        rf = (1.05 ** (1 / 252)) - 1
        expected = 0.5 * 0.01 + 0.5 * rf - 0.5 * cal.COST_PER_UNIT_TURNOVER
        self.assertAlmostEqual(result["net_return"], expected, places=12)
        short = cal.one_session_portfolio_return(-0.5, 0.0, -0.01, 5.0)
        expected_short = 0.5 * 0.01 + 0.5 * rf - 0.5 * cal.SHORT_BORROW_DAILY - 0.5 * cal.COST_PER_UNIT_TURNOVER
        self.assertAlmostEqual(short["net_return"], expected_short, places=12)

    def test_candidate_book_is_not_replaced_by_synthetic_mean_exposure(self):
        shadow = raw_shadow(target=0.0, applied=0.0)
        for i, row in enumerate(shadow["candidate_snapshots"]):
            row["target_exposure_next_session"] = 1.0 if i < 4 else -1.0
        record = record_for(shadow, stance="neutral", confidence=0.8, relationship="NEUTRAL")
        contract, _ = cal.build_counterfactual_contract(
            record, shadow, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc)
        )
        market = [
            {"date": datetime(2026, 8, 20).date(), "spy_close": 100.0, "irx_annual_yield": 5.0},
            {"date": datetime(2026, 8, 21).date(), "spy_close": 101.0, "irx_annual_yield": 5.0},
        ]
        settled, reason = cal.settle_contract(contract, market, datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(reason, "settled")
        self.assertAlmostEqual(settled["without_belief"]["mean_target_exposure"], 0.0)
        synthetic_flat = cal.one_session_portfolio_return(0.0, 0.0, 0.01, 5.0)["net_return"]
        self.assertNotAlmostEqual(settled["without_belief"]["net_return"], synthetic_flat, places=10)
        self.assertEqual(len(settled["candidate_results"]), 8)

    def test_settlement_waits_until_next_session_is_closed(self):
        shadow = raw_shadow()
        record = record_for(shadow)
        contract, _ = cal.build_counterfactual_contract(
            record, shadow, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc)
        )
        market = [
            {"date": datetime(2026, 8, 20).date(), "spy_close": 100.0, "irx_annual_yield": 5.0},
            {"date": datetime(2026, 8, 21).date(), "spy_close": 101.0, "irx_annual_yield": 5.0},
        ]
        pending, reason = cal.settle_contract(contract, market, datetime(2026, 8, 21, 19, 0, tzinfo=timezone.utc))
        self.assertIsNone(pending)
        self.assertEqual(reason, "next_trading_session_not_closed")
        settled, reason = cal.settle_contract(contract, market, datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(reason, "settled")
        self.assertAlmostEqual(settled["forward_spx_return"], 0.01)
        self.assertGreater(settled["with_belief_hypothetical"]["mean_target_exposure"], settled["without_belief"]["mean_target_exposure"])


class EndToEndTests(unittest.TestCase):
    def test_first_run_activates_without_backfill_then_freezes_and_settles_prospectively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calibration = root / "calibration"
            bridge_dir = root / "bridge"
            raw_path = root / "raw.json"
            market_path = root / "market.csv"
            bridge_dir.mkdir()
            write_jsonl(bridge_dir / "engine_belief_observations.jsonl", [])
            first_shadow = raw_shadow(updated_at="2026-08-19T21:40:00Z", market_date="2026-08-19")
            write_json(raw_path, first_shadow)
            write_market(market_path, [("2026-08-19", 99.0, 5.0)])
            first = cal.run_calibration(
                calibration, bridge_dir, raw_path, market_path,
                datetime(2026, 8, 19, 21, 50, tzinfo=timezone.utc),
            )
            self.assertEqual(first["contracts_total"], 0)
            self.assertEqual(first["status"], "activated_waiting_for_next_prospective_bridge_record")

            shadow = raw_shadow(updated_at="2026-08-20T21:40:00Z", market_date="2026-08-20", target=0.5, applied=0.0)
            record = record_for(shadow)
            write_json(raw_path, shadow)
            write_jsonl(bridge_dir / "engine_belief_observations.jsonl", [record])
            write_market(market_path, [("2026-08-20", 100.0, 5.0)])
            second = cal.run_calibration(
                calibration, bridge_dir, raw_path, market_path,
                datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc),
            )
            self.assertEqual(second["new_contracts"], 1)
            self.assertEqual(second["resolved_pairs"], 0)

            write_market(market_path, [
                ("2026-08-20", 100.0, 5.0),
                ("2026-08-21", 101.0, 5.0),
            ])
            third = cal.run_calibration(
                calibration, bridge_dir, raw_path, market_path,
                datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(third["new_contracts"], 0)
            self.assertEqual(third["new_settlements"], 1)
            self.assertEqual(third["resolved_pairs"], 1)
            report = json.loads((calibration / "BRACE_SPX_ENGINE_BELIEF_CALIBRATION_REPORT.json").read_text())
            self.assertEqual(report["sample"]["effective_n"], 1)
            self.assertTrue(report["sample"]["eight_candidate_equal_weight_accounting"])
            self.assertFalse(report["decision_influence"])
            self.assertFalse(report["promotion_gate"]["eligible"])

    def test_settled_pair_is_immutable_if_market_file_changes_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calibration = root / "calibration"
            bridge_dir = root / "bridge"
            raw_path = root / "raw.json"
            market_path = root / "market.csv"
            bridge_dir.mkdir()
            write_jsonl(bridge_dir / "engine_belief_observations.jsonl", [])
            write_json(raw_path, raw_shadow(updated_at="2026-08-19T21:40:00Z", market_date="2026-08-19"))
            write_market(market_path, [("2026-08-19", 99.0, 5.0)])
            cal.run_calibration(calibration, bridge_dir, raw_path, market_path, datetime(2026, 8, 19, 21, 50, tzinfo=timezone.utc))

            shadow = raw_shadow()
            record = record_for(shadow)
            write_json(raw_path, shadow)
            write_jsonl(bridge_dir / "engine_belief_observations.jsonl", [record])
            write_market(market_path, [("2026-08-20", 100.0, 5.0)])
            cal.run_calibration(calibration, bridge_dir, raw_path, market_path, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc))
            write_market(market_path, [("2026-08-20", 100.0, 5.0), ("2026-08-21", 101.0, 5.0)])
            cal.run_calibration(calibration, bridge_dir, raw_path, market_path, datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc))
            before = (calibration / "settlements.jsonl").read_text()
            write_market(market_path, [("2026-08-20", 100.0, 5.0), ("2026-08-21", 150.0, 5.0)])
            cal.run_calibration(calibration, bridge_dir, raw_path, market_path, datetime(2026, 8, 24, 21, 0, tzinfo=timezone.utc))
            after = (calibration / "settlements.jsonl").read_text()
            self.assertEqual(before, after)

    def test_same_market_date_is_capped_to_one_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calibration = root / "calibration"
            bridge_dir = root / "bridge"
            raw_path = root / "raw.json"
            market_path = root / "market.csv"
            bridge_dir.mkdir()
            write_jsonl(bridge_dir / "engine_belief_observations.jsonl", [])
            write_json(raw_path, raw_shadow(updated_at="2026-08-19T21:40:00Z", market_date="2026-08-19"))
            write_market(market_path, [("2026-08-19", 99.0, 5.0)])
            cal.run_calibration(calibration, bridge_dir, raw_path, market_path, datetime(2026, 8, 19, 21, 50, tzinfo=timezone.utc))

            shadow = raw_shadow()
            r1 = record_for(shadow, record_id="r1")
            r2 = record_for(shadow, record_id="r2")
            write_json(raw_path, shadow)
            write_jsonl(bridge_dir / "engine_belief_observations.jsonl", [r1, r2])
            write_market(market_path, [("2026-08-20", 100.0, 5.0)])
            result = cal.run_calibration(calibration, bridge_dir, raw_path, market_path, datetime(2026, 8, 20, 21, 45, tzinfo=timezone.utc))
            self.assertEqual(result["contracts_total"], 1)
            self.assertEqual(result["missed_contracts"], 1)
            misses = cal._read_jsonl(calibration / "missed_contracts.jsonl")
            self.assertEqual(misses[0]["reason"], "duplicate_market_date_non_independent")


class ReportTests(unittest.TestCase):
    def test_conflict_warning_and_with_without_metrics_are_reported(self):
        contract = {
            "contract_id": "c1",
            "family_scores": {key: 0.1 for key in cal.FAMILY_KEYS},
            "without_belief": {"mean_target_exposure_next_session": 0.5},
            "belief": {"risk_on_probability_mean": 0.3, "confidence": 0.8},
            "relationship": {"class": "STRONG_CONFLICT", "strength": 0.8},
        }
        settlement = {
            "contract_id": "c1",
            "decision_market_date": "2026-08-20",
            "forward_spx_return": -0.01,
            "relationship": {"class": "STRONG_CONFLICT", "strength": 0.8},
            "without_belief": {"net_return": -0.005, "directional_hit": False},
            "with_belief_hypothetical": {"net_return": -0.003, "directional_hit": False},
            "belief_directional_hit": True,
            "conflict_warning_hit": True,
            "agreement_confirmation_hit": None,
            "delta_pnl": 0.002,
        }
        state = {"activated_at": "2026-08-19T22:00:00Z"}
        report = cal.build_report([], [contract], [settlement], [], state, datetime(2026, 8, 21, 22, 0, tzinfo=timezone.utc))
        group = report["engine_belief_relationship_calibration"]["by_relationship_group"]["CONFLICT"]
        self.assertEqual(group["conflict_warning_hit_rate"], 1.0)
        self.assertAlmostEqual(report["with_vs_without_belief"]["mean_delta_pnl"], 0.002)
        self.assertFalse(report["promotion_gate"]["eligible"])

    def test_incremental_information_waits_for_sufficient_sample(self):
        result = cal._incremental_information([], {})
        self.assertEqual(result["status"], "insufficient_sample")
        self.assertEqual(result["minimum_n"], cal.MIN_INCREMENTAL_MODEL_N)

    def test_temporal_incremental_model_can_measure_added_belief_signal(self):
        contracts = {}
        settlements = []
        for i in range(40):
            p = 0.25 if i % 2 == 0 else 0.75
            y = (p - 0.5) * 0.02
            cid = f"c{i}"
            contracts[cid] = {
                "contract_id": cid,
                "family_scores": {key: 0.0 for key in cal.FAMILY_KEYS},
                "without_belief": {"mean_target_exposure_next_session": 0.0},
                "belief": {"risk_on_probability_mean": p, "confidence": 0.8},
                "relationship": {"class": "NEUTRAL", "strength": 0.0},
            }
            settlements.append({
                "contract_id": cid,
                "decision_market_date": f"{i:03d}",
                "forward_spx_return": y,
            })
        result = cal._incremental_information(settlements, contracts)
        self.assertEqual(result["status"], "measured_no_promotion_implication")
        self.assertGreater(result["mse_improvement"], 0.0)
        self.assertFalse(result["promotion_decision"])


if __name__ == "__main__":
    unittest.main()
