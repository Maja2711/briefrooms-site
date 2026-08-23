from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import counterfactual_decision_gate_diagnostics as cf
from scripts.learning_ledger import append_event, read_events


class CounterfactualDecisionGateDiagnosticsTests(unittest.TestCase):
    def test_registry_covers_current_decision_engines_and_zero_authority(self) -> None:
        self.assertEqual(
            set(cf.ENGINE_REGISTRY),
            {"gpw_daily", "us_daily", "eurusd_daily", "wes", "brace_portfolio_10k", "brace_spx_g6"},
        )
        self.assertIn("belief_core", cf.UPSTREAM_NON_DECISION_LAYERS)
        self.assertIn("gse", cf.UPSTREAM_NON_DECISION_LAYERS)
        self.assertTrue(cf.safety_controls())
        self.assertTrue(all(value is False for value in cf.safety_controls().values()))

    def test_snapshot_hash_and_no_synthetic_direction_contract(self) -> None:
        snap = cf.make_snapshot(
            engine_id="gpw_daily",
            decision_id="gpw:2026-09-01",
            decision_at="2026-09-01T08:10:00Z",
            actual_action="FLAT",
            decision_stage="paper_research",
            source_ref="synthetic",
            instrument_id="MARKET",
            candidates=[
                cf.candidate_snapshot(
                    "gpw:2026-09-01:FLAT",
                    action="FLAT",
                    selected=True,
                    settlement_mode="flat_zero",
                ),
                cf.candidate_snapshot(
                    "gpw:2026-09-01:AAA.WA:LONG",
                    action="LONG",
                    selected=False,
                    settlement_mode="insufficient_counterfactual_state",
                    gates=[cf.gate_snapshot("source_gate", passed=False)],
                ),
            ],
        )
        cf.validate_snapshot(snap)
        self.assertEqual({row["action"] for row in snap["candidates"]}, {"FLAT", "LONG"})
        self.assertNotIn("SHORT", {row["action"] for row in snap["candidates"]})
        tampered = dict(snap)
        tampered["actual_action"] = "LONG"
        with self.assertRaises(ValueError):
            cf.validate_snapshot(tampered)

    def test_gpw_no_trade_rejections_are_not_given_fake_risk_plan(self) -> None:
        payload = {
            "date": "2026-09-01",
            "generated_at": "2026-09-01T09:20:00+02:00",
            "decision": "BRAK_TRANSAKCJI",
            "selection": None,
            "data_quality": {
                "screened_out": {"AAA.WA": "screened_by_liquidity_atr_or_risk"},
                "analysis_rejections": {"BBB.WA": "source_gate"},
            },
        }
        snapshots = cf.adapt_daily_stock(payload, market="gpw")
        self.assertEqual(len(snapshots), 1)
        snap = snapshots[0]
        self.assertEqual(snap["actual_action"], "FLAT")
        blocked = [row for row in snap["candidates"] if not row["selected"]]
        self.assertEqual(len(blocked), 2)
        for row in blocked:
            self.assertEqual(row["settlement_mode"], "insufficient_counterfactual_state")
            self.assertIsNone(row["entry"])
            self.assertIsNone(row["stop"])
            self.assertIsNone(row["target"])

    def test_flat_missed_opportunity_and_gate_false_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / cf.LEDGER_FILENAME
            ledger.touch()
            snap = cf.make_snapshot(
                engine_id="gpw_daily",
                decision_id="gpw:2026-09-02",
                decision_at="2026-09-02T08:00:00Z",
                actual_action="FLAT",
                decision_stage="paper_research",
                source_ref="synthetic",
                instrument_id="AAA.WA",
                candidates=[
                    cf.candidate_snapshot(
                        "flat",
                        action="FLAT",
                        selected=True,
                        settlement_mode="flat_zero",
                    ),
                    cf.candidate_snapshot(
                        "long-aaa",
                        action="LONG",
                        selected=False,
                        settlement_mode="directional_market_return",
                        reference_price=100.0,
                        gates=[cf.gate_snapshot("source_gate", passed=False, reason="weak_source")],
                    ),
                ],
            )
            cf.append_snapshot(ledger, snap, existing=set())
            cf.append_candidate_outcome(
                ledger,
                snapshot=snap,
                candidate_id="long-aaa",
                occurred_at="2026-09-03T16:00:00Z",
                return_percent=3.5,
                settlement_mode="directional_market_return",
            )
            diagnostics = cf.build_diagnostics(read_events(ledger))
            self.assertEqual(diagnostics["flat_value"]["missed_opportunity"], 1)
            gate = diagnostics["by_gate"]["gpw_daily:source_gate"]
            self.assertEqual(gate["false_negative"], 1)
            self.assertEqual(gate["true_negative"], 0)
            self.assertEqual(gate["false_negative_rate"], 1.0)

    def test_insufficient_candidate_cannot_be_settled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / cf.LEDGER_FILENAME
            ledger.touch()
            snap = cf.make_snapshot(
                engine_id="us_daily",
                decision_id="us:2026-09-01",
                decision_at="2026-09-01T14:00:00Z",
                actual_action="FLAT",
                decision_stage="paper_research",
                source_ref="synthetic",
                candidates=[
                    cf.candidate_snapshot(
                        "blocked",
                        action="LONG",
                        selected=False,
                        settlement_mode="insufficient_counterfactual_state",
                        gates=[cf.gate_snapshot("risk", passed=False)],
                    )
                ],
            )
            cf.append_snapshot(ledger, snap, existing=set())
            with self.assertRaises(ValueError):
                cf.append_candidate_outcome(
                    ledger,
                    snapshot=snap,
                    candidate_id="blocked",
                    occurred_at="2026-09-02T20:00:00Z",
                    return_percent=5.0,
                )

    def _write_minimal_gpw_repo(self, root: Path, *, resolved: bool) -> tuple[str, str]:
        investments = root / "data" / "investments"
        investments.mkdir(parents=True, exist_ok=True)
        occurred = "2026-09-02T15:00:00Z"
        payload = {
            "date": "2026-09-02",
            "generated_at": "2026-09-02T08:15:00Z",
            "decision": "TRANSAKCJA",
            "selection": {
                "symbol": "AAA.WA",
                "ticker": "AAA",
                "score": 80,
                "reference_price": 100,
                "stop": 98,
                "target": 104,
                "reward_risk": 2.0,
            },
            "data_quality": {},
        }
        if resolved:
            payload["outcome"] = {
                "status": "RESOLVED",
                "return_percent": 4.0,
                "r_multiple": 2.0,
                "exit_reason": "target",
                "resolved_at": occurred,
            }
        (investments / "gpw_daily_pick.json").write_text(json.dumps(payload), encoding="utf-8")
        return "gpw:2026-09-02:AAA.WA", occurred

    def test_same_cycle_pr28_outcome_is_not_bound_until_snapshot_preexists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            state = Path(tmp) / "state"
            state.mkdir()
            ledger = state / cf.LEDGER_FILENAME
            ledger.touch()
            cf.ensure_activation(state, ledger, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
            upstream_id, occurred = self._write_minimal_gpw_repo(root, resolved=False)
            # PR28 can receive the actual outcome in this collector cycle.
            append_event(
                ledger,
                event_type="outcome",
                occurred_at=occurred,
                subject_id=upstream_id,
                source_ref="gpw-daily://outcome/2026-09-02",
                payload={"return_percent": 4.0, "r_multiple": 2.0, "exit_reason": "target"},
            )
            first = cf.run_cycle(state, root)
            self.assertEqual(first["snapshots_appended"], 1)
            self.assertEqual(first["outcomes_appended"], 0)
            second = cf.run_cycle(state, root)
            self.assertEqual(second["outcomes_appended"], 1)
            events = read_events(ledger)
            self.assertEqual(len(cf._outcome_events(events)), 1)

    def test_missing_activation_with_existing_pr29_history_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            ledger = state / cf.LEDGER_FILENAME
            ledger.touch()
            snap = cf.make_snapshot(
                engine_id="gpw_daily",
                decision_id="x",
                decision_at="2026-09-01T00:00:00Z",
                actual_action="FLAT",
                decision_stage="paper_research",
                source_ref="x",
                candidates=[],
            )
            cf.append_snapshot(ledger, snap, existing=set())
            with self.assertRaises(RuntimeError):
                cf.ensure_activation(state, ledger)

    def test_wes_freezes_both_directions_and_settles_same_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            state = Path(tmp) / "state"
            weekly = root / "data" / "investments" / "weekly"
            weekly.mkdir(parents=True)
            state.mkdir()
            ledger = state / cf.LEDGER_FILENAME
            ledger.touch()
            cf.ensure_activation(state, ledger, now=datetime(2026, 8, 31, tzinfo=timezone.utc))
            item = {
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "long",
                "entry_price": 1.10,
                "entry_captured_at": "2026-09-01T07:00:00Z",
                "trade_status": "open",
                "continuous_entry_decision": {
                    "direction": "long",
                    "strategy_id": "trend",
                    "candidates": {
                        "trend": {"direction": "long", "raw_score": 70, "conviction": 7, "utility": 9},
                        "inverse": {"direction": "short", "raw_score": -60, "conviction": 6, "utility": 8},
                    },
                },
                "risk_plan": {"stop_loss_price": 1.09, "take_profit_price": 1.12, "reward_to_risk": 2.0},
            }
            week = {
                "week_id": "2026-W36",
                "forecast_locked_at": "2026-09-01T06:00:00Z",
                "market_window": {"exit_target_local": "2026-09-04T20:00:00Z"},
                "instruments": [item],
            }
            path = weekly / "2026-W36.json"
            path.write_text(json.dumps(week), encoding="utf-8")
            first = cf.run_cycle(state, root)
            self.assertEqual(first["snapshots_appended"], 1)
            item.update(
                {
                    "trade_status": "closed",
                    "exit_price": 1.122,
                    "exit_captured_at": "2026-09-03T15:00:00Z",
                    "exit_reason": "signal",
                    "result_percent": 2.0,
                }
            )
            path.write_text(json.dumps(week), encoding="utf-8")
            second = cf.run_cycle(state, root)
            self.assertEqual(second["outcomes_appended"], 2)
            outcomes = [
                event["payload"]
                for event in read_events(ledger)
                if event.get("event_type") == "learning_observation"
                and (event.get("payload") or {}).get("observation_type") == cf.OUTCOME_OBSERVATION
            ]
            by_action = {row["action"]: row["return_percent"] for row in outcomes}
            self.assertGreater(by_action["LONG"], 0)
            self.assertLess(by_action["SHORT"], 0)

    def test_eurusd_refresh_never_invents_opposite_direction(self) -> None:
        payload = {
            "timestamp": "2026-09-01T20:00:00Z",
            "direction": "FLAT",
            "metadata": {
                "candidate": {
                    "direction": "LONG",
                    "score": 58,
                    "confidence": 0.2,
                    "accepted": False,
                    "gate_reasons": ["below_long_threshold"],
                }
            },
        }
        snapshots = cf.adapt_eurusd(payload)
        self.assertEqual(len(snapshots), 1)
        actions = {row["action"] for row in snapshots[0]["candidates"]}
        self.assertEqual(actions, {"FLAT", "LONG"})
        self.assertNotIn("SHORT", actions)

    def test_brace_portfolio_checks_and_spx_research_gates_use_shared_contract(self) -> None:
        portfolio = {
            "generated_at": "2026-09-01T10:00:00Z",
            "methodology_version": "v-test",
            "recommendations": [],
            "decisions": [
                {
                    "decision_id": "d1",
                    "generated_at": "2026-09-01T10:00:00Z",
                    "action": "ADD",
                    "instrument": "abc",
                    "status": "PROPOSED",
                    "checks": {"confidence": True, "expected_return": False},
                }
            ],
        }
        snaps = cf.adapt_brace_portfolio(portfolio)
        self.assertEqual(len(snaps), 1)
        gates = {row["name"]: row["passed"] for row in snaps[0]["candidates"][0]["gates"]}
        self.assertEqual(gates, {"confidence": True, "expected_return": False})

        spx = {
            "generated_at": "2026-09-01T21:00:00Z",
            "candidate_space_size": 8,
            "development": {"strict_gate_passed": False, "status": "gate_not_passed"},
            "sealed_holdout": {"accessed": False},
            "shadow": {"status": "warming_up", "observations_collected": 20, "warmup_required": 70},
            "governance": {"orders_allowed": False},
        }
        spx_snap = cf.adapt_brace_spx(spx)[0]
        self.assertEqual(spx_snap["decision_stage"], "research_shadow")
        self.assertEqual(spx_snap["candidates"][0]["settlement_mode"], "research_shadow")


if __name__ == "__main__":
    unittest.main()
