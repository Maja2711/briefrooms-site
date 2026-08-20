import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from scripts.brace_information_disagreement_capture import (
    CONTRACT_VERSION,
    PR16_1_CONTRACT_VERSION,
    PR17_CONTRACT_VERSION,
    STATE_FILENAME,
    _sha,
    build_capture,
    capabilities,
    disagreement_topology,
    engine_stance,
    run,
    safety_controls,
    sector_belief_id,
)


NOW = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
DECISION_AT = "2026-08-20T12:30:00Z"
WORLD_ID = "world-test"
PAIR_ID = "pair-test"


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def analysis_payload():
    return {
        "generated_at": DECISION_AT,
        "methodology_version": "brace-portfolio-v3.0.0",
        "positions": [
            {
                "instrument_id": "amzn",
                "broker_symbol": "AMZN.US",
                "asset_type": "STOCK",
                "sector": "Consumer Discretionary",
                "region": "United States",
                "currency": "USD",
                "current_price": 100.0,
                "market_date": "2026-08-20",
                "current_price_updated_at": "2026-08-20T12:20:00Z",
                "current_price_source": "fixture",
                "current_fx_to_pln": 3.7,
                "current_fx_updated_at": "2026-08-20T12:20:00Z",
                "current_fx_source": "fixture",
                "current_weight": 0.10,
                "target_weight": 0.15,
                "quality_score": 72.0,
                "valuation_score": 55.0,
                "momentum_score": 68.0,
                "risk_score": 60.0,
                "diversification_score": 48.0,
                "thesis_score": 64.0,
                "data_quality_score": 100.0,
                "final_score": 60.0,
                "risk_adjusted_score": 0.42,
                "expected_return_base": 0.14,
                "expected_return_bull": 0.30,
                "expected_return_bear": -0.12,
                "expected_drawdown": 0.25,
                "probability_of_reaching_target": 0.56,
                "target_shortfall": 0.0,
                "required_risk_to_target": 0.0,
                "confidence_score": 1.0,
                "momentum": {"return_6m": 0.20},
                "risk": {"volatility": 0.30},
                "quality": {"revenue_growth": 0.15},
                "valuation": {"forward_pe": 25.0},
                "liquidity": {"average_volume": 1_000_000},
                "fundamental_data_status": "AVAILABLE",
                "data_status": "AVAILABLE",
                "source_errors": [],
                "positive_factors": ["quality_score"],
                "negative_factors": [],
                "conditions_for_change": ["material thesis change"],
                "thesis_pl": "test",
                "thesis_en": "test",
                "invalidation_pl": "test invalidation",
                "invalidation_en": "test invalidation",
            }
        ],
    }


def pending_payload():
    return {
        "generated_at": DECISION_AT,
        "methodology_version": "brace-portfolio-v3.0.0",
        "safe_mode": False,
        "recommendations": [
            {
                "instrument": "amzn",
                "broker_symbol": "AMZN.US",
                "action": "HOLD",
                "final_score": 60.0,
                "confidence": 1.0,
                "current_weight": 0.10,
                "proposed_weight": 0.10,
                "signal_price": 100.0,
                "positive_factors": ["quality_score"],
                "negative_factors": [],
                "conditions_for_change": ["material thesis change"],
                "material_event_context": {"report_count": 1},
            }
        ],
    }


def world_snapshot(created_at="2026-08-20T12:00:00Z"):
    def belief(p, c=0.6):
        return {
            "probability": {"value": p},
            "confidence": {"value": c},
        }

    return {
        "world_state_id": WORLD_ID,
        "contract_version": "investment-world-state-v1",
        "created_at": created_at,
        "context_as_of": "2026-08-20T11:55:00Z",
        "source_cutoff_at": "2026-08-20T11:55:00Z",
        "belief_context": {
            "broad_market": {
                "market.rates.supportive": belief(0.65),
                "market.liquidity.supportive": belief(0.60),
                "market.macro_regime.supportive": belief(0.55),
                "market.risk_regime.supportive": belief(0.58),
            },
            "sector_factor": {
                "sector.consumer_discretionary.leadership": belief(0.70),
                "factor.growth.leadership": belief(0.65),
                "factor.quality.leadership": belief(0.60),
                "factor.momentum.leadership": belief(0.62),
                "factor.small_cap.leadership": belief(0.48),
            },
        },
    }


def pair_payload(analysis, pending, *, world_id=WORLD_ID):
    return {
        "pair_set_id": PAIR_ID,
        "decision_set_id": "decision-test",
        "decision_at": DECISION_AT,
        "decision_world_state_id": world_id,
        "engine_methodology_version": "brace-portfolio-v3.0.0",
        "engine_consumed_belief": False,
        "hypothetical_only": True,
        "historical_backfill": False,
        "source_sha256": {
            "analysis": _sha(analysis),
            "pending_decisions": _sha(pending),
        },
        "items": [
            {
                "instrument": "amzn",
                "broker_symbol": "AMZN.US",
                "signal_price": 100.0,
                "original_score": 60.0,
                "source_action": "HOLD",
                "without_action": "HOLD",
                "with_action": "HOLD",
                "belief_modifier_score_points": 0.3,
                "forecasts": [
                    {
                        "forecast_id": "forecast-amzn-revenue",
                        "belief_id": "entity.amzn.revenue_durability",
                        "dimension": "revenue_durability",
                        "predicted_probability": 0.8,
                        "forecast_confidence": 0.5,
                        "forecast_at": "2026-08-20T12:10:00Z",
                        "target_at": "2026-12-18T12:10:00Z",
                        "forecast_world_state_id": WORLD_ID,
                        "binding_id": "binding-test",
                    }
                ],
            }
        ],
    }


def pr17_state(pair=None, outcome=None):
    return {
        "contract_version": PR17_CONTRACT_VERSION,
        "pair_sets": {} if pair is None else {pair["pair_set_id"]: pair},
        "economic_outcomes": {} if outcome is None else {PAIR_ID: outcome},
    }


def pr17_report():
    return {
        "contract_version": PR17_CONTRACT_VERSION,
        "mode": "research_shadow",
        "active_decision_influence": False,
    }


def world_state(snapshot=None):
    return {
        "contract_version": PR16_1_CONTRACT_VERSION,
        "snapshots": [snapshot or world_snapshot()],
    }


def world_report():
    return {
        "contract_version": PR16_1_CONTRACT_VERSION,
        "active_decision_influence": False,
    }


class BraceInformationDisagreementCaptureTests(unittest.TestCase):
    def test_governance_has_zero_authority(self):
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["prospective_engine_information_capture_enabled"])
        self.assertFalse(caps["marginal_information_value_score_enabled"])
        self.assertFalse(caps["promotion_gate_enabled"])

    def test_engine_stance_uses_frozen_brace_regions(self):
        self.assertEqual(engine_stance(final_score=60, risk_score=60, data_quality_confidence=1), "POSITIVE")
        self.assertEqual(engine_stance(final_score=50, risk_score=60, data_quality_confidence=1), "NEUTRAL")
        self.assertEqual(engine_stance(final_score=42, risk_score=60, data_quality_confidence=1), "NEGATIVE")
        self.assertEqual(engine_stance(final_score=80, risk_score=20, data_quality_confidence=1), "NEGATIVE")
        self.assertEqual(engine_stance(final_score=80, risk_score=60, data_quality_confidence=0.4), "UNAVAILABLE")

    def test_sector_mapping_is_explicit(self):
        self.assertEqual(sector_belief_id("Consumer Discretionary"), "sector.consumer_discretionary.leadership")
        self.assertEqual(sector_belief_id("Financial Services"), "sector.financials.leadership")
        self.assertIsNone(sector_belief_id("Utilities"))

    def test_disagreement_topology_marks_engine_entity_conflict(self):
        topology = disagreement_topology(
            {"engine_stance": "POSITIVE"},
            {"entity_stance": "NEGATIVE"},
            {"market_stance": "POSITIVE", "sector_stance": "POSITIVE", "factor_stance": "POSITIVE"},
        )
        self.assertEqual(topology["engine_entity_relation"], "CONFLICT")
        self.assertEqual(topology["top_down_state"], "SUPPORTIVE")
        self.assertTrue(topology["flags"]["engine_entity_conflict"])
        self.assertIn("ENGINE_POSITIVE", topology["pattern_code"])

    def test_build_capture_freezes_engine_belief_and_world_information(self):
        analysis = analysis_payload()
        pending = pending_payload()
        pair = pair_payload(analysis, pending)
        capture, terminal = build_capture(
            pair,
            analysis=analysis,
            pending=pending,
            world_state=world_state(),
            captured_at=NOW,
        )
        self.assertIsNone(terminal)
        self.assertIsNotNone(capture)
        item = capture["items"][0]
        self.assertEqual(item["engine_information"]["feature_scores"]["momentum_score"], 68.0)
        self.assertEqual(item["engine_information"]["data_quality_confidence"]["semantic_type"], "data_quality_confidence")
        self.assertEqual(item["belief_information"]["forecasts"][0]["probability_semantic_type"], "model_probability")
        self.assertEqual(item["world_context"]["sector_belief_id"], "sector.consumer_discretionary.leadership")
        self.assertIsNone(item["research_readiness"]["miv_score"])
        self.assertFalse(capture["historical_information_backfill"])

    def test_source_hash_mismatch_is_terminal(self):
        analysis = analysis_payload()
        pending = pending_payload()
        pair = pair_payload(analysis, pending)
        changed = deepcopy(analysis)
        changed["positions"][0]["momentum_score"] = 99.0
        capture, terminal = build_capture(
            pair,
            analysis=changed,
            pending=pending,
            world_state=world_state(),
            captured_at=NOW,
        )
        self.assertIsNone(capture)
        self.assertEqual(terminal["status"], "source_snapshot_not_available")
        self.assertTrue(terminal["terminal_no_reconstruction"])

    def test_world_state_must_preexist_decision(self):
        analysis = analysis_payload()
        pending = pending_payload()
        pair = pair_payload(analysis, pending)
        future_world = world_snapshot(created_at="2026-08-20T12:40:00Z")
        capture, terminal = build_capture(
            pair,
            analysis=analysis,
            pending=pending,
            world_state=world_state(future_world),
            captured_at=NOW,
        )
        self.assertIsNone(capture)
        self.assertEqual(terminal["status"], "decision_world_state_not_prospective")

    def _paths(self, root: Path, pair=None, outcome=None):
        analysis = analysis_payload()
        pending = pending_payload()
        payloads = {
            "pr17_state.json": pr17_state(pair, outcome),
            "pr17_report.json": pr17_report(),
            "world_state.json": world_state(),
            "world_report.json": world_report(),
            "analysis.json": analysis,
            "pending.json": pending,
        }
        paths = {}
        for name, payload in payloads.items():
            path = root / name
            write_json(path, payload)
            paths[name] = path
        return paths

    def _run(self, state_dir: Path, paths, at=NOW):
        return run(
            state_dir,
            pr17_state_path=paths["pr17_state.json"],
            pr17_report_path=paths["pr17_report.json"],
            world_state_path=paths["world_state.json"],
            world_report_path=paths["world_report.json"],
            analysis_path=paths["analysis.json"],
            pending_path=paths["pending.json"],
            as_of=at,
        )

    def test_first_run_existing_pair_is_activation_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = analysis_payload()
            pending = pending_payload()
            pair = pair_payload(analysis, pending)
            paths = self._paths(root, pair=pair)
            report = self._run(root / "state", paths)
            self.assertTrue(report["sample"]["activation_only_this_run"])
            self.assertEqual(report["sample"]["captures_total"], 0)
            self.assertEqual(report["sample"]["pre_activation_pair_sets_total"], 1)

    def test_new_post_activation_pair_is_captured_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root, pair=None)
            self._run(root / "state", paths)
            analysis = analysis_payload()
            pending = pending_payload()
            pair = pair_payload(analysis, pending)
            write_json(paths["pr17_state.json"], pr17_state(pair))
            report = self._run(root / "state", paths, datetime(2026, 8, 20, 13, 5, tzinfo=timezone.utc))
            self.assertEqual(report["sample"]["captures_total"], 1)
            state_path = root / "state" / STATE_FILENAME
            before = json.loads(state_path.read_text())
            report2 = self._run(root / "state", paths, datetime(2026, 8, 20, 13, 10, tzinfo=timezone.utc))
            after = json.loads(state_path.read_text())
            self.assertEqual(before["captures"], after["captures"])
            self.assertEqual(report2["sample"]["captures_total"], 1)

    def test_matured_pr17_outcome_joins_descriptive_delta_without_miv_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root, pair=None)
            self._run(root / "state", paths)
            analysis = analysis_payload()
            pending = pending_payload()
            pair = pair_payload(analysis, pending)
            write_json(paths["pr17_state.json"], pr17_state(pair))
            self._run(root / "state", paths, datetime(2026, 8, 20, 13, 5, tzinfo=timezone.utc))
            outcome = {
                "pair_set_id": PAIR_ID,
                "status": "matured",
                "calibration_eligible": True,
                "without_return": 0.01,
                "with_return": 0.015,
                "delta_return": 0.005,
                "delta_pnl_pln": 50.0,
            }
            write_json(paths["pr17_state.json"], pr17_state(pair, outcome))
            report = self._run(root / "state", paths, datetime(2026, 8, 28, 13, 5, tzinfo=timezone.utc))
            row = report["descriptive_rows"][0]
            self.assertTrue(row["matured"])
            self.assertEqual(row["delta_return"], 0.005)
            self.assertEqual(row["redundancy_status"], "NOT_YET_ESTIMABLE")
            self.assertEqual(row["orthogonality_status"], "NOT_YET_ESTIMABLE")
            self.assertIsNone(row["miv_score"])
            self.assertIsNone(report["marginal_information_value"]["miv_score"])


if __name__ == "__main__":
    unittest.main()
