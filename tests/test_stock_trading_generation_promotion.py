from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_generation_promotion as gp
from scripts import stock_trading_generation_router as router
from scripts import stock_trading_portfolio as portfolio
from scripts.stock_trading_v2_discovery import Bar


class GenerationPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = gp.load_config()
        self.now = datetime(2026, 9, 16, 20, 30, tzinfo=timezone.utc)

    def _state(self, definition: str = "def-v2") -> dict:
        return {
            "schema_version": gp.STATE_SCHEMA,
            "updated_at": None,
            "controls": {
                "closed_loop_enabled": True,
                "automatic_promotion_enabled": True,
                "automatic_rollback_enabled": True,
                "manual_approval_required": False,
                "promotion_scope": "entry_decision_source",
                "shared_canonical_portfolio_risk_kernel": True,
                "replacement_authority_enabled": False,
            },
            "markets": {
                market: {
                    "active_generation": "v1",
                    "parent_generation": "v1",
                    "challenger_generation": "v2",
                    "status": "COLLECTING_PRIMARY",
                    "revision": 0,
                    "validation_start_at": "2026-09-01T00:00:00Z",
                    "v2_definition_sha256": definition,
                    "promoted_definition_sha256": None,
                    "primary": None,
                    "holdout": None,
                    "promoted_at": None,
                    "rollback": None,
                    "blocked_until": None,
                }
                for market in ("GPW", "US")
            },
            "state_sha256": None,
        }

    @staticmethod
    def _v1_payload(generated: str, symbol: str = "AAA") -> dict:
        return {
            "generated_at": generated,
            "decision": "TRADE",
            "selection": {
                "symbol": symbol,
                "reference_price": 100.0,
                "stop": 95.0,
                "target": 110.0,
                "risk_percent": 0.05,
                "reward_risk": 2.0,
                "market_snapshot": {"last": 100.0},
            },
        }

    @staticmethod
    def _v2_payload(generated: str, symbol: str = "BBB", action: str = "BUY") -> dict:
        return {
            "schema_version": "stock-trading-v2-portfolio-opportunity-v1",
            "market": "US",
            "generated_at": generated,
            "decision": {
                "action": action,
                "reason": "test",
                "candidate": None if action in {"CASH", "HOLD"} else {
                    "symbol": symbol,
                    "market_data_symbol": symbol,
                    "name": symbol,
                    "utility": 65.0,
                    "eligible": True,
                    "research_risk_plan": {
                        "status": "VALID_RESEARCH_REFERENCE",
                        "execution_ready": False,
                        "reference_price": 50.0,
                        "stop": 48.0,
                        "target": 54.0,
                        "risk_percent": 0.04,
                        "reward_risk": 2.0,
                    },
                },
                "replace": None,
            },
            "opportunity_sha256": "test-opportunity-sha",
        }

    def test_definition_change_fails_closed_from_active_v2(self) -> None:
        state = self._state("old")
        row = state["markets"]["US"]
        row["active_generation"] = "v2"
        row["promoted_definition_sha256"] = "old"
        row["promoted_at"] = "2026-09-10T00:00:00Z"
        gp.reconcile_definition(state, "new", now=self.now)
        self.assertEqual(row["active_generation"], "v1")
        self.assertEqual(row["v2_definition_sha256"], "new")
        self.assertEqual(row["status"], "COLLECTING_PRIMARY")
        self.assertIsNone(row["promoted_definition_sha256"])

    def test_freeze_is_prospective_immutable_and_captures_cash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 20:30 UTC is after the US close on this date.
            generated = "2026-09-16T20:15:00Z"
            v1 = self._v1_payload(generated)
            v2 = self._v2_payload(generated, action="CASH")
            with patch.object(gp.opportunity, "validate_opportunity", return_value=None):
                pair = gp.freeze_pair(
                    "US", now=self.now, v1_payload=v1, v2_payload=v2,
                    definition_sha256="def", config=self.config, root=root,
                )
                self.assertIsNotNone(pair)
                self.assertTrue(pair["eligible_for_formal_evidence"])
                self.assertEqual(pair["v1"]["action"], "LONG")
                self.assertEqual(pair["v2"]["action"], "CASH")
                first_hash = pair["pair_sha256"]
                changed = self._v2_payload(generated, symbol="ZZZ")
                pair2 = gp.freeze_pair(
                    "US", now=self.now + timedelta(minutes=5), v1_payload=v1,
                    v2_payload=changed, definition_sha256="def", config=self.config, root=root,
                )
                self.assertEqual(pair2["pair_sha256"], first_hash)

    def test_settlement_uses_same_future_only_replay_for_both_generations(self) -> None:
        pair = {
            "schema_version": gp.PAIR_SCHEMA,
            "market": "US",
            "session_date": "2026-09-16",
            "frozen_at": "2026-09-16T20:30:00Z",
            "v2_definition_sha256": "def",
            "v1_source_sha256": "v1",
            "v2_source_sha256": "v2",
            "v1": {
                "action": "LONG", "generated_at": "2026-09-16T20:00:00Z",
                "symbol": "AAA", "market_data_symbol": "AAA",
                "risk_plan": {"reference_price": 100.0, "stop": 95.0, "target": 110.0, "entry_zone": [], "skip_above": None, "reward_risk": 2.0, "risk_percent": 0.05},
            },
            "v2": {
                "action": "LONG", "generated_at": "2026-09-16T20:10:00Z",
                "symbol": "BBB", "market_data_symbol": "BBB",
                "risk_plan": {"reference_price": 100.0, "stop": 95.0, "target": 110.0, "entry_zone": [], "skip_above": None, "reward_risk": 2.0, "risk_percent": 0.05},
            },
            "eligible_for_formal_evidence": True,
            "generation_disagreement": True,
            "governance": {"prospective_only": True, "immutable": True, "no_lookahead": True, "production_decision_influence_at_freeze": False, "activation_model": "next_session_open", "same_bar_policy": "stop_first_conservative"},
        }
        pair["pair_sha256"] = gp._sha(pair)
        bars = {
            "AAA": [
                Bar("2026-09-16", 100, 101, 99, 100, 1),
                Bar("2026-09-17", 100, 101, 98, 99, 1),
                Bar("2026-09-18", 99, 100, 97, 98, 1),
                Bar("2026-09-21", 98, 99, 96, 97, 1),
                Bar("2026-09-22", 97, 98, 95.5, 96, 1),
                Bar("2026-09-23", 96, 97, 95.5, 96, 1),
            ],
            "BBB": [
                Bar("2026-09-16", 100, 101, 99, 100, 1),
                Bar("2026-09-17", 100, 103, 99, 102, 1),
                Bar("2026-09-18", 102, 105, 101, 104, 1),
                Bar("2026-09-21", 104, 108, 103, 107, 1),
                Bar("2026-09-22", 107, 109, 106, 108, 1),
                Bar("2026-09-23", 108, 109, 107, 108, 1),
            ],
        }
        outcome = gp.settle_pair(pair, config=self.config, bars_provider=lambda symbol, market: bars[symbol])
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome["formal_evidence_eligible"])
        self.assertGreater(outcome["paired_net_incremental_return_percent"], 0)
        self.assertEqual(outcome["v1"]["entry_session"], "2026-09-17")
        self.assertEqual(outcome["v2"]["entry_session"], "2026-09-17")

    def _pair_and_outcome(self, pair_root: Path, outcome_root: Path, market: str, day: str, frozen_at: str, definition: str, delta: float, symbol: str) -> str:
        pair = {
            "schema_version": gp.PAIR_SCHEMA,
            "market": market,
            "session_date": day,
            "frozen_at": frozen_at,
            "v2_definition_sha256": definition,
            "v1_source_sha256": "v1",
            "v2_source_sha256": "v2",
            "v1": {"action": "CASH", "generated_at": frozen_at, "symbol": None, "risk_plan": None},
            "v2": {"action": "LONG", "generated_at": frozen_at, "symbol": symbol, "market_data_symbol": symbol, "risk_plan": {"reference_price": 100, "stop": 95, "target": 110}},
            "eligible_for_formal_evidence": True,
            "generation_disagreement": True,
            "governance": {"prospective_only": True, "immutable": True, "no_lookahead": True, "production_decision_influence_at_freeze": False},
        }
        pair["pair_sha256"] = gp._sha(pair)
        ppath = gp.pair_path(pair_root, market, day)
        ppath.parent.mkdir(parents=True, exist_ok=True)
        gp._atomic(ppath, pair)
        out = {
            "schema_version": gp.OUTCOME_SCHEMA,
            "market": market,
            "session_date": day,
            "settled_at": frozen_at,
            "source_pair_sha256": pair["pair_sha256"],
            "v2_definition_sha256": definition,
            "horizon_sessions": 5,
            "formal_evidence_eligible": True,
            "v1": {"status": "SETTLED", "net_return_percent": 0.0},
            "v2": {"status": "SETTLED", "net_return_percent": delta},
            "paired_net_incremental_return_percent": delta,
            "symbols": [symbol],
            "governance": {"immutable": True, "no_lookahead": True, "same_frozen_decision_date": True, "production_decision_influence": False},
        }
        out["outcome_sha256"] = gp._sha(out)
        opath = gp.outcome_path(outcome_root, market, day)
        opath.parent.mkdir(parents=True, exist_ok=True)
        gp._atomic(opath, out)
        return out["outcome_sha256"]

    def test_primary_then_fresh_disjoint_holdout_promotes_automatically(self) -> None:
        config = copy.deepcopy(self.config)
        config["evaluation"].update({
            "primary_fixed_paired_n": 2,
            "holdout_fixed_paired_n": 2,
            "bootstrap_samples": 200,
            "minimum_net_incremental_return_percent": 0.01,
            "minimum_net_positive_rate": 0.5,
            "minimum_unique_symbols": 1,
            "minimum_span_days": 0,
            "maximum_single_positive_contribution_share": 1.0,
        })
        state = self._state("def")
        state["markets"]["GPW"]["validation_start_at"] = "2026-09-01T00:00:00Z"
        with tempfile.TemporaryDirectory() as td:
            pair_root = Path(td) / "pairs"; outcome_root = Path(td) / "outcomes"
            self._pair_and_outcome(pair_root, outcome_root, "GPW", "2026-09-02", "2026-09-02T16:00:00Z", "def", 1.0, "A.WA")
            self._pair_and_outcome(pair_root, outcome_root, "GPW", "2026-09-03", "2026-09-03T16:00:00Z", "def", 1.0, "B.WA")
            t0 = datetime(2026, 9, 4, tzinfo=timezone.utc)
            gp.evaluate_state(state, config=config, now=t0, pair_root=pair_root, outcome_root=outcome_root)
            row = state["markets"]["GPW"]
            self.assertEqual(row["primary"]["status"], "PASS")
            self.assertEqual(row["active_generation"], "v1")
            self._pair_and_outcome(pair_root, outcome_root, "GPW", "2026-09-07", "2026-09-07T16:00:00Z", "def", 1.2, "C.WA")
            self._pair_and_outcome(pair_root, outcome_root, "GPW", "2026-09-08", "2026-09-08T16:00:00Z", "def", 1.1, "D.WA")
            gp.evaluate_state(state, config=config, now=datetime(2026, 9, 9, tzinfo=timezone.utc), pair_root=pair_root, outcome_root=outcome_root)
            self.assertEqual(row["holdout"]["status"], "PASS")
            self.assertEqual(row["active_generation"], "v2")
            self.assertEqual(row["status"], "ACTIVE_V2_PRODUCTION_ENTRY_SOURCE")
            self.assertTrue(set(row["primary"]["sample_outcome_sha256"]).isdisjoint(row["holdout"]["sample_outcome_sha256"]))

    def test_fresh_post_promotion_evidence_can_rollback(self) -> None:
        config = copy.deepcopy(self.config)
        config["evaluation"].update({"bootstrap_samples": 200, "minimum_unique_symbols": 1, "minimum_span_days": 0})
        config["rollback"].update({"fixed_paired_n": 2, "minimum_span_days": 0, "maximum_net_incremental_mean_percent": -0.1, "maximum_net_positive_rate": 0.4})
        state = self._state("def")
        row = state["markets"]["US"]
        row.update({"active_generation": "v2", "promoted_definition_sha256": "def", "promoted_at": "2026-09-10T00:00:00Z", "status": "ACTIVE_V2_PRODUCTION_ENTRY_SOURCE", "rollback": {"status": "MONITORING", "monitored_outcome_sha256": []}})
        with tempfile.TemporaryDirectory() as td:
            pair_root = Path(td) / "pairs"; outcome_root = Path(td) / "outcomes"
            self._pair_and_outcome(pair_root, outcome_root, "US", "2026-09-11", "2026-09-11T21:00:00Z", "def", -1.0, "A")
            self._pair_and_outcome(pair_root, outcome_root, "US", "2026-09-14", "2026-09-14T21:00:00Z", "def", -1.0, "B")
            gp.evaluate_state(state, config=config, now=datetime(2026, 9, 15, tzinfo=timezone.utc), pair_root=pair_root, outcome_root=outcome_root)
            self.assertEqual(row["active_generation"], "v1")
            self.assertEqual(row["status"], "ROLLED_BACK_TO_V1")
            self.assertIsNotNone(row["blocked_until"])


class GenerationRouterTests(unittest.TestCase):
    def test_promoted_v2_can_open_below_legacy_score_gate_but_not_break_risk_kernel(self) -> None:
        config = gp.load_config()
        current_hash = gp.definition_hash(gp.ROOT, config)
        gen_state = gp.load_state()
        gen_state = copy.deepcopy(gen_state)
        row = gen_state["markets"]["US"]
        row.update({
            "active_generation": "v2",
            "v2_definition_sha256": current_hash,
            "promoted_definition_sha256": current_hash,
            "status": "ACTIVE_V2_PRODUCTION_ENTRY_SOURCE",
            "promoted_at": "2026-09-16T18:00:00Z",
        })
        policy = portfolio.load_policy()
        state = portfolio.empty_state(datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc), policy)
        v1 = {"generated_at": "2026-09-16T19:00:00Z", "decision": "NO_TRADE"}
        v2 = GenerationPromotionTests._v2_payload("2026-09-16T19:55:00Z", symbol="V2X", action="BUY")
        now = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
        with patch.object(router.opportunity, "validate_opportunity", return_value=None):
            updated, audit = router.apply_entry_source(
                state, "US", now=now, policy=policy, generation_state=gen_state,
                config=config, v1_payload=v1, v2_payload=v2,
                quote_provider=lambda symbol, market, when: 50.0,
            )
        self.assertEqual(audit["action"], "open")
        positions = portfolio.open_positions(updated, "US")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "V2X")
        self.assertEqual(positions[0]["source_generation"], "v2")
        self.assertLessEqual(positions[0]["risk_percent"], policy["markets"]["US"]["maximum_risk_percent"])

    def test_intentional_v2_cash_does_not_fallback_to_v1(self) -> None:
        config = gp.load_config()
        current_hash = gp.definition_hash(gp.ROOT, config)
        gen_state = copy.deepcopy(gp.load_state())
        row = gen_state["markets"]["US"]
        row.update({"active_generation": "v2", "v2_definition_sha256": current_hash, "promoted_definition_sha256": current_hash, "status": "ACTIVE_V2_PRODUCTION_ENTRY_SOURCE", "promoted_at": "2026-09-16T18:00:00Z"})
        policy = portfolio.load_policy()
        state = portfolio.empty_state(datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc), policy)
        v1 = GenerationPromotionTests._v1_payload("2026-09-16T19:55:00Z", symbol="V1X")
        v2 = GenerationPromotionTests._v2_payload("2026-09-16T19:55:00Z", action="CASH")
        now = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
        with patch.object(router.opportunity, "validate_opportunity", return_value=None):
            updated, audit = router.apply_entry_source(state, "US", now=now, policy=policy, generation_state=gen_state, config=config, v1_payload=v1, v2_payload=v2, quote_provider=lambda *args: 50.0)
        self.assertEqual(audit["action"], "v2_no_new_entry")
        self.assertEqual(portfolio.open_positions(updated, "US"), [])


if __name__ == "__main__":
    unittest.main()
