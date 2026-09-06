import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import brace_portfolio_learning_loop_v1 as loop
import brace_portfolio_learning_promotion_v1 as gate


ENVELOPE = {
    "schema_version": "brace-portfolio-learning-envelope-v1",
    "engine_id": "brace_portfolio_10k",
    "base_currency": "PLN",
    "prospective_only": True,
    "historical_backfill": False,
    "real_broker_integration": False,
    "trade_execution_authority": False,
    "learner_may_expand_envelope": False,
    "supported_fx_pairs": {"USD": "USDPLN", "EUR": "EURPLN"},
    "fx_hedge": {"allowed_ratios": [0.0, 0.25, 0.5, 0.75, 1.0], "maximum_ratio": 1.0, "minimum_notional_pln": 250.0, "estimated_round_trip_cost_bps": 8.0, "promotion_step_limit": 0.25},
    "options_hedge": {"enabled_if_market_data_available": True, "allowed_strategies": ["PROTECTIVE_PUT", "PUT_SPREAD", "COLLAR"], "naked_short_options": False, "minimum_dte": 20, "maximum_dte": 90, "maximum_relative_bid_ask_spread": 0.20, "minimum_bid": 0.01, "maximum_single_premium_fraction_nav": 0.005, "maximum_annual_premium_fraction_nav": 0.015, "maximum_covered_call_notional_fraction": 1.0},
    "validation": {"fx_minimum_resolved_observations": 3, "options_minimum_resolved_observations": 3, "required_consecutive_confirmations": 2, "minimum_ci_lower_bound_utility": 0.0, "minimum_mean_utility_improvement": 0.001},
}

POLICY = {
    "schema_version": "brace-portfolio-production-learning-policy-v1",
    "engine_id": "brace_portfolio_10k",
    "policy_version": 1,
    "status": "BASELINE_NO_HEDGE",
    "base_currency": "PLN",
    "fx_hedge_ratio_by_regime": {"DEFAULT": {"USD": 0.0, "EUR": 0.0}},
    "options_strategy_by_regime": {"DEFAULT": "NONE"},
    "promotion": {"automatic": True, "manual_approval_required": False, "last_promotion_id": None, "evidence_sha256": None, "validation_epoch_id": None},
    "safety": {"bounded_by": "test", "learner_may_expand_envelope": False, "real_broker_integration": False, "trade_execution_authority": False, "naked_short_options": False},
}

PORTFOLIO = {
    "portfolio_id": "test",
    "base_currency": "PLN",
    "total_value_pln": 10000.0,
    "cash_pln": 5000.0,
    "positions": [
        {"id": "usd", "status": "active", "currency": "USD", "current_value_pln": 4000.0, "current_fx_to_pln": 4.0, "current_fx_updated_at": "2026-01-01T12:00:00+00:00"},
        {"id": "eur", "status": "active", "currency": "EUR", "current_value_pln": 1000.0, "current_fx_to_pln": 5.0, "current_fx_updated_at": "2026-01-01T12:00:00+00:00"},
    ],
}


class BracePortfolioLearningLoopV1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.paths = {
            "ENVELOPE": root / "envelope.json",
            "PRODUCTION_POLICY": root / "policy.json",
            "PORTFOLIO": root / "portfolio.json",
            "OPTION_MARKET": root / "options.json",
            "STATE_ROOT": root / "state",
            "SNAPSHOT_DIR": root / "state" / "snapshots",
            "SETTLEMENTS": root / "state" / "settlements.jsonl",
            "STATE": root / "state" / "learning_state.json",
            "PROMOTIONS": root / "state" / "promotions.jsonl",
            "PRODUCTION_HEDGE_PLAN": root / "production_plan.json",
        }
        for name, value in self.paths.items():
            setattr(loop, name, value)
        gate.STATE = root / "state" / "promotion_gate_state.json"
        self.paths["ENVELOPE"].write_text(json.dumps(ENVELOPE), encoding="utf-8")
        self.paths["PRODUCTION_POLICY"].write_text(json.dumps(POLICY), encoding="utf-8")
        self.paths["PORTFOLIO"].write_text(json.dumps(PORTFOLIO), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_is_prospective_immutable_and_pln_fx_aware(self):
        now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
        result = loop.capture(now)
        self.assertEqual(result["status"], "CAPTURED")
        snap = json.loads(next(self.paths["SNAPSHOT_DIR"].glob("*.json")).read_text())
        self.assertTrue(snap["prospective_only"])
        self.assertFalse(snap["historical_backfill"])
        self.assertEqual(snap["fx_exposures"]["USD"]["spot"], 4.0)
        self.assertEqual(snap["fx_exposures"]["EUR"]["spot"], 5.0)
        again = loop.capture(now)
        self.assertEqual(again["status"], "ALREADY_CAPTURED_IDENTICAL")

    def test_fx_counterfactual_rewards_hedge_when_usd_falls(self):
        loop.capture(datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc))
        changed = dict(PORTFOLIO)
        changed["positions"] = [dict(x) for x in PORTFOLIO["positions"]]
        changed["positions"][0]["current_fx_to_pln"] = 3.8
        changed["positions"][0]["current_value_pln"] = 3800.0
        self.paths["PORTFOLIO"].write_text(json.dumps(changed), encoding="utf-8")
        result = loop.settle(date(2026, 1, 8))
        self.assertGreater(result["created"], 0)
        rows = loop._jsonl(self.paths["SETTLEMENTS"])
        half = next(r for r in rows if r["currency"] == "USD" and r["horizon_days"] == 7 and r["variant"]["hedge_ratio"] == 0.5)
        none = next(r for r in rows if r["currency"] == "USD" and r["horizon_days"] == 7 and r["variant"]["hedge_ratio"] == 0.0)
        self.assertGreater(half["utility_improvement"], 0)
        self.assertEqual(none["utility_improvement"], 0.0)

    def test_options_require_real_complete_market_quote(self):
        market = {
            "market_context": {"fx_regime": "HIGH_VOL"},
            "strategies": [
                {"strategy_id": "good", "strategy_type": "PROTECTIVE_PUT", "market_data_complete": True, "dte": 45, "relative_bid_ask_spread": 0.10, "net_premium_pln": 40.0, "hedged_notional_pln": 2000.0},
                {"strategy_id": "missing", "strategy_type": "PROTECTIVE_PUT", "market_data_complete": False, "dte": 45, "relative_bid_ask_spread": 0.10, "net_premium_pln": 10.0, "hedged_notional_pln": 2000.0},
            ],
        }
        self.paths["OPTION_MARKET"].write_text(json.dumps(market), encoding="utf-8")
        result = loop.capture(datetime(2026, 1, 2, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(result["eligible_option_strategies"], 1)
        snap = json.loads(next(self.paths["SNAPSHOT_DIR"].glob("*.json")).read_text())
        self.assertEqual([x["strategy_id"] for x in snap["option_strategies"]], ["good"])

    def test_repeated_run_without_new_evidence_cannot_confirm_promotion(self):
        # Three independent 30d observations make 25% USD hedge a candidate.
        for i in range(3):
            row = {
                "schema_version": loop.SETTLEMENT_SCHEMA,
                "settlement_id": f"s{i}",
                "snapshot_id": f"snap{i}",
                "signal_date": f"2026-01-0{i+1}",
                "evaluation_date": f"2026-02-0{i+1}",
                "horizon_days": 30,
                "kind": "FX",
                "currency": "USD",
                "market_regime": "DEFAULT",
                "variant": {"hedge_ratio": 0.25},
                "counterfactual_baseline": "NO_FX_HEDGE",
                "utility_improvement": 0.01,
                "economically_evaluable": True,
                "prospective_only": True,
            }
            row["row_sha256"] = loop._sha(row)
            loop._append(self.paths["SETTLEMENTS"], row)
        first = gate.evaluate()
        self.assertEqual(first["confirmations"], 1)
        second = gate.evaluate()
        self.assertEqual(second["confirmations"], 1)
        self.assertEqual(second["status"], "NO_VALIDATED_PROMOTION")

        # A new prospective observation confirms the same policy thesis.
        row = {
            "schema_version": loop.SETTLEMENT_SCHEMA,
            "settlement_id": "s3",
            "snapshot_id": "snap3",
            "signal_date": "2026-01-04",
            "evaluation_date": "2026-02-04",
            "horizon_days": 30,
            "kind": "FX",
            "currency": "USD",
            "market_regime": "DEFAULT",
            "variant": {"hedge_ratio": 0.25},
            "counterfactual_baseline": "NO_FX_HEDGE",
            "utility_improvement": 0.01,
            "economically_evaluable": True,
            "prospective_only": True,
        }
        row["row_sha256"] = loop._sha(row)
        loop._append(self.paths["SETTLEMENTS"], row)
        promoted = gate.evaluate()
        self.assertEqual(promoted["status"], "AUTO_PROMOTED")
        policy = json.loads(self.paths["PRODUCTION_POLICY"].read_text())
        self.assertEqual(policy["fx_hedge_ratio_by_regime"]["DEFAULT"]["USD"], 0.25)
        self.assertFalse(policy["safety"]["real_broker_integration"])

    def test_production_plan_never_has_execution_authority(self):
        plan = loop.build_production_hedge_plan(datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc))
        self.assertFalse(plan["execution"]["real_broker_integration"])
        self.assertFalse(plan["execution"]["trade_execution_authority"])
        self.assertEqual(plan["fx_targets"][0]["status"], "TARGET_ONLY_NO_BROKER_EXECUTION")

    def test_envelope_cannot_be_expanded_by_policy(self):
        broken = json.loads(json.dumps(POLICY))
        broken["fx_hedge_ratio_by_regime"]["DEFAULT"]["USD"] = 1.25
        with self.assertRaises(ValueError):
            loop._validate_policy(broken, ENVELOPE)


if __name__ == "__main__":
    unittest.main()
