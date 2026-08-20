import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.brace_marginal_information_value_diagnostics import (
    CONTRACT_VERSION,
    PR17_CONTRACT_VERSION,
    PR18_CONTRACT_VERSION,
    STATE_FILENAME,
    build_observations,
    capabilities,
    dependence_diagnostics,
    disagreement_regime_diagnostics,
    economic_incremental_value,
    redundancy_orthogonality,
    run,
    safety_controls,
)


DECISION_AT = "2026-08-20T12:30:00Z"
TARGET_AT = "2026-08-27T12:30:00Z"
CLOSED_AT = "2026-08-28T12:35:00Z"
NOW = datetime(2026, 8, 28, 13, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def engine_info(score=60.0, quality=70.0):
    return {
        "feature_scores": {
            "quality_score": quality,
            "valuation_score": 55.0,
            "momentum_score": 68.0,
            "risk_score": 60.0,
            "diversification_score": 48.0,
            "thesis_score": 64.0,
            "final_score": score,
            "risk_adjusted_score": 0.4,
        },
        "expectations": {
            "expected_return_base": 0.14,
            "expected_drawdown": 0.25,
            "probability_of_reaching_target": 0.56,
        },
        "data_quality_confidence": {
            "value": 1.0,
            "semantic_type": "data_quality_confidence",
        },
    }


def belief_info(forecast_id="forecast-1", signal=0.30, dimension="revenue_durability", decision_changed=False):
    p = (signal / 0.5 + 1.0) / 2.0
    return {
        "forecasts": [
            {
                "forecast_id": forecast_id,
                "belief_id": f"entity.amzn.{dimension}",
                "dimension": dimension,
                "predicted_probability": p,
                "forecast_confidence": 0.5,
                "forecast_at": "2026-08-20T12:10:00Z",
                "target_at": "2026-12-18T12:10:00Z",
            }
        ],
        "dimensions": [dimension],
        "aggregate_confidence_weighted_signed_signal": signal,
        "primary_modifier_score_points": 0.6,
        "modifier_nonzero": True,
        "without_action": "HOLD",
        "with_action": "ADD" if decision_changed else "HOLD",
        "decision_changed": decision_changed,
    }


def topology(relation="AGREEMENT", market="POSITIVE", sector="POSITIVE", factor="POSITIVE"):
    entity = "POSITIVE"
    engine = "POSITIVE" if relation == "AGREEMENT" else "NEGATIVE"
    return {
        "engine_stance": engine,
        "entity_stance": entity,
        "market_stance": market,
        "sector_stance": sector,
        "factor_stance": factor,
        "engine_entity_relation": relation,
        "top_down_state": "SUPPORTIVE" if market == sector == "POSITIVE" else "MIXED",
        "pattern_code": f"ENGINE_{engine}__ENTITY_{entity}__MARKET_{market}__SECTOR_{sector}__FACTOR_{factor}__ENGINE_ENTITY_{relation}",
    }


def capture(pair_id="pair-1", instrument="amzn", forecast_id="forecast-1", signal=0.30, quality=70.0, relation="AGREEMENT"):
    return {
        "pair_set_id": pair_id,
        "decision_set_id": f"decision-{pair_id}",
        "decision_at": DECISION_AT,
        "decision_world_state_id": "world-1",
        "engine_methodology_version": "brace-portfolio-v3.0.0",
        "prospective_to_economic_outcome": True,
        "historical_information_backfill": False,
        "source_reconstruction": False,
        "promotion_authority": False,
        "items": [
            {
                "instrument": instrument,
                "engine_information": engine_info(quality=quality),
                "belief_information": belief_info(forecast_id=forecast_id, signal=signal),
                "world_context": {"world_state_id": "world-1"},
                "disagreement_topology": topology(relation=relation),
            }
        ],
    }


def pair(pair_id="pair-1", instrument="amzn", item_count=1):
    items = [{"instrument": instrument}]
    if item_count == 2:
        items.append({"instrument": "msft"})
    return {
        "pair_set_id": pair_id,
        "decision_set_id": f"decision-{pair_id}",
        "decision_at": DECISION_AT,
        "target_at": TARGET_AT,
        "portfolio_notional_pln": 10000.0,
        "engine_consumed_belief": False,
        "hypothetical_only": True,
        "historical_backfill": False,
        "promotion_authority": False,
        "items": items,
    }


def outcome(pair_id="pair-1", instrument="amzn", without=0.01, with_ret=0.015, closed_at=CLOSED_AT, include_item=True):
    payload = {
        "pair_set_id": pair_id,
        "status": "matured",
        "calibration_eligible": True,
        "target_at": TARGET_AT,
        "closed_at": closed_at,
        "without_return": without,
        "with_return": with_ret,
        "delta_return": with_ret - without,
        "without_turnover": 0.0,
        "with_turnover": 0.02,
        "without_cost_return": 0.0,
        "with_cost_return": 0.00001,
    }
    if include_item:
        payload["items"] = [
            {
                "instrument": instrument,
                "without_contribution_return": without,
                "with_contribution_return": with_ret,
                "without_turnover": 0.0,
                "with_turnover": 0.02,
                "without_cost_return": 0.0,
                "with_cost_return": 0.00001,
            }
        ]
    return payload


def pr18_state(captures=None):
    return {
        "contract_version": PR18_CONTRACT_VERSION,
        "captures": captures or {},
        "terminal_uncaptured": {},
    }


def pr18_report():
    return {
        "contract_version": PR18_CONTRACT_VERSION,
        "mode": "research_shadow",
        "active_decision_influence": False,
        "information_contracts": {
            "source_snapshot_sha_parity_required": True,
            "historical_information_backfill": False,
            "retroactive_source_reconstruction": False,
        },
    }


def pr17_state(pairs=None, outcomes=None):
    return {
        "contract_version": PR17_CONTRACT_VERSION,
        "pair_sets": pairs or {},
        "economic_outcomes": outcomes or {},
    }


def pr17_report():
    return {
        "contract_version": PR17_CONTRACT_VERSION,
        "mode": "research_shadow",
        "active_decision_influence": False,
        "with_without_economics": {
            "ENGINE_ORIGINAL_WITHOUT_BELIEF": {},
            "ENGINE_PLUS_HYPOTHETICAL_BELIEF_WITH_BELIEF": {},
            "DELTA": {},
        },
    }


class MarginalInformationValueDiagnosticsTests(unittest.TestCase):
    def test_governance_is_zero_authority_and_no_composite_score(self):
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["economic_incremental_value_diagnostics_enabled"])
        self.assertTrue(caps["redundancy_proxy_enabled"])
        self.assertFalse(caps["composite_miv_score_enabled"])
        self.assertFalse(caps["promotion_gate_enabled"])

    def test_matured_item_join_measures_incremental_contribution(self):
        cap = capture()
        p = pair()
        o = outcome()
        rows, issues = build_observations(
            pr18_state({"pair-1": cap}),
            pr17_state({"pair-1": p}, {"pair-1": o}),
            as_of=NOW,
        )
        self.assertEqual(issues, [])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["economic_eligible"])
        self.assertAlmostEqual(rows[0]["delta_contribution_return"], 0.005)
        econ = economic_incremental_value(rows)
        self.assertEqual(econ["matured_item_n"], 1)
        self.assertAlmostEqual(econ["delta_pnl_pln_sum"], 50.0)

    def test_multi_item_pair_without_item_outcomes_is_not_duplicated(self):
        cap = capture()
        cap["items"].append({
            "instrument": "msft",
            "engine_information": engine_info(),
            "belief_information": belief_info("forecast-msft", 0.2),
            "world_context": {"world_state_id": "world-1"},
            "disagreement_topology": topology(),
        })
        p = pair(item_count=2)
        o = outcome(include_item=False)
        rows, issues = build_observations(
            pr18_state({"pair-1": cap}),
            pr17_state({"pair-1": p}, {"pair-1": o}),
            as_of=NOW,
        )
        self.assertEqual(sum(1 for row in rows if row["economic_eligible"]), 0)
        self.assertTrue(any(row["code"] == "multi_item_pair_missing_item_outcomes" for row in issues))

    def test_repeated_same_belief_state_does_not_make_redundancy_estimable(self):
        rows = []
        for i in range(8):
            rows.append({
                "belief_state_key": "same-state",
                "belief_signal": 0.3,
                "engine_features": {"feature_scores.quality_score": 50 + i},
            })
        diag = redundancy_orthogonality(rows)
        self.assertEqual(diag["unique_belief_state_n"], 1)
        self.assertEqual(diag["status"], "NOT_YET_ESTIMABLE")

    def test_redundancy_proxy_uses_unique_belief_states(self):
        rows = []
        for i, signal in enumerate((0.1, 0.2, 0.3, 0.4), 1):
            rows.append({
                "belief_state_key": f"state-{i}",
                "belief_signal": signal,
                "engine_features": {
                    "feature_scores.quality_score": i * 10.0,
                    "feature_scores.valuation_score": 100.0 - i,
                },
            })
        diag = redundancy_orthogonality(rows)
        self.assertEqual(diag["status"], "DESCRIPTIVE_PROXY_AVAILABLE")
        self.assertAlmostEqual(diag["redundancy_proxy"], 1.0)
        self.assertAlmostEqual(diag["orthogonality_proxy"], 0.0)
        self.assertFalse(diag["engineering_min_is_promotion_threshold"])

    def test_effective_n_floor_caps_same_pair_and_belief_state(self):
        rows = [
            {
                "economic_eligible": True,
                "delta_contribution_return": 0.01,
                "decision_at": DECISION_AT,
                "pair_set_id": "pair-1",
                "instrument": "amzn",
                "belief_state_key": "state-1",
            },
            {
                "economic_eligible": True,
                "delta_contribution_return": -0.005,
                "decision_at": DECISION_AT,
                "pair_set_id": "pair-1",
                "instrument": "msft",
                "belief_state_key": "state-1",
            },
        ]
        diag = dependence_diagnostics(rows)
        self.assertEqual(diag["raw_matured_item_n"], 2)
        self.assertEqual(diag["unique_pair_n"], 1)
        self.assertEqual(diag["unique_belief_state_n"], 1)
        self.assertEqual(diag["descriptive_effective_n_floor"], 1.0)
        self.assertIsNone(diag["promotion_grade_effective_n"])

    def test_disagreement_regime_cross_is_descriptive(self):
        rows = [
            {
                "engine_entity_relation": "CONFLICT",
                "regime_signature": "MARKET_POSITIVE__SECTOR_POSITIVE__FACTOR_POSITIVE__TOPDOWN_SUPPORTIVE",
                "pattern_code": "pattern-a",
                "top_down_state": "SUPPORTIVE",
                "belief_signature": "revenue_durability",
                "instrument": "amzn",
                "economic_eligible": True,
                "delta_contribution_return": 0.01,
                "pair_set_id": "pair-1",
                "belief_state_key": "state-1",
                "decision_changed": True,
            }
        ]
        diag = disagreement_regime_diagnostics(rows)
        self.assertEqual(len(diag["engine_entity_x_regime"]), 1)
        self.assertEqual(diag["engine_entity_x_regime"][0]["engine_entity_relation"], "CONFLICT")
        self.assertFalse(diag["alpha_threshold_defined_here"])

    def _paths(self, root: Path, captures=None, pairs=None, outcomes=None):
        payloads = {
            "pr18_state.json": pr18_state(captures),
            "pr18_report.json": pr18_report(),
            "pr17_state.json": pr17_state(pairs, outcomes),
            "pr17_report.json": pr17_report(),
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
            pr18_state_path=paths["pr18_state.json"],
            pr18_report_path=paths["pr18_report.json"],
            pr17_state_path=paths["pr17_state.json"],
            pr17_report_path=paths["pr17_report.json"],
            as_of=at,
        )

    def test_first_run_can_bootstrap_existing_prospective_pr18_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cap = capture()
            p = pair()
            paths = self._paths(root, {"pair-1": cap}, {"pair-1": p}, {})
            report = self._run(root / "state", paths)
            self.assertEqual(report["sample"]["observation_rows_total"], 1)
            self.assertEqual(report["sample"]["matured_economic_rows"], 0)
            self.assertEqual(report["miv"]["status"], "COLLECTING_NO_MATURED_DATA")
            self.assertIsNone(report["miv"]["composite_miv_score"])
            self.assertTrue(report["runtime"]["new_diagnostic_snapshot_this_run"])

    def test_same_source_fingerprint_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cap = capture()
            p = pair()
            paths = self._paths(root, {"pair-1": cap}, {"pair-1": p}, {})
            first = self._run(root / "state", paths)
            second = self._run(root / "state", paths, datetime(2026, 8, 28, 13, 5, tzinfo=timezone.utc))
            self.assertEqual(first["runtime"]["diagnostic_snapshots_total"], 1)
            self.assertFalse(second["runtime"]["new_diagnostic_snapshot_this_run"])
            state = json.loads((root / "state" / STATE_FILENAME).read_text())
            self.assertEqual(len(state["diagnostic_snapshots"]), 1)

    def test_new_matured_outcome_appends_new_diagnostic_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cap = capture()
            p = pair()
            paths = self._paths(root, {"pair-1": cap}, {"pair-1": p}, {})
            self._run(root / "state", paths)
            write_json(paths["pr17_state.json"], pr17_state({"pair-1": p}, {"pair-1": outcome()}))
            report = self._run(root / "state", paths, datetime(2026, 8, 28, 13, 10, tzinfo=timezone.utc))
            self.assertEqual(report["miv"]["status"], "DESCRIPTIVE_DIAGNOSTICS_AVAILABLE")
            self.assertEqual(report["sample"]["matured_economic_rows"], 1)
            self.assertEqual(report["runtime"]["diagnostic_snapshots_total"], 2)
            self.assertIsNone(report["miv"]["composite_miv_score"])

    def test_future_or_pre_target_outcome_is_excluded(self):
        cap = capture()
        p = pair()
        bad = outcome(closed_at="2026-08-26T12:35:00Z")
        rows, issues = build_observations(
            pr18_state({"pair-1": cap}),
            pr17_state({"pair-1": p}, {"pair-1": bad}),
            as_of=NOW,
        )
        self.assertFalse(rows[0]["economic_eligible"])
        self.assertTrue(any(row["code"] == "invalid_matured_outcome_temporal_boundary" for row in issues))

    def test_no_effective_n_or_promotion_threshold_defined_here(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            report = self._run(root / "state", paths)
            self.assertFalse(report["methodology"]["effective_n_threshold_defined_here"])
            self.assertFalse(report["promotion"]["effective_n_threshold_defined_here"])
            self.assertEqual(report["promotion"]["status"], "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW")
            self.assertFalse(report["miv"]["edge_claim_allowed"])


if __name__ == "__main__":
    unittest.main()
