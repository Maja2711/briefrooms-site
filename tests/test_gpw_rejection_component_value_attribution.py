from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import daily_stock_rejected_candidate_freeze as freeze
from scripts import gpw_rejected_candidate_outcomes as outcomes
from scripts import gpw_rejection_component_value_attribution as attribution

UTC = ZoneInfo("UTC")


def gate(name: str, passed: bool, *, hard: bool, stage: str = "test") -> dict:
    return {"name": name, "passed": passed, "hard": hard, "stage": stage, "reason": name}


def frozen_candidate(symbol: str, *, component: str, hard: bool, extra_failed_gate: str | None = None) -> dict:
    gates = [gate("market_data", True, hard=True, stage="market_data")]
    first = gate(component, False, hard=hard, stage="decision")
    gates.append(first)
    if extra_failed_gate:
        gates.append(gate(extra_failed_gate, False, hard=True, stage="risk"))
    row = {
        "candidate_id": f"gpw:2026-09-07:{symbol}:LONG",
        "market": "gpw",
        "symbol": symbol,
        "name": symbol,
        "sector": "test",
        "action": "LONG",
        "selected": False,
        "decision_at": "2026-09-07T10:00:00+02:00",
        "expected_session": "2026-09-04",
        "first_blocking_gate": first,
        "decision_path": {"gates": gates, "explicit_engine_rejection": None, "producer_decision": "TRANSAKCJA", "producer_reason": "test"},
        "score_state": {"rank": 2, "shortlist_limit": 6, "quant_pre_score": 70.0, "composite_score": None, "scores": {}, "returns": {}},
        "market_state": {"last_session": "2026-09-04", "reference_price": 50.0, "median_turnover": 10_000_000, "turnover_threshold": 1_000_000, "risk_percent": 0.05},
        "risk_plan": {"reference_price": 50.0, "entry_zone": [49.0, 51.0], "stop": 47.0, "target": 56.0, "reward_risk": 2.0, "risk_percent": 0.06, "atr": 1.0, "plan_source": "engine_quant_candidate", "activation_rule": "test"},
        "settlement_eligibility": {"eligible": True, "mode": "risk_plan", "reason": "prospectively_frozen_risk_plan"},
        "governance": {"decision_influence": False, "ranking_writeback": False, "gate_writeback": False, "historical_backfill": False, "news_or_llm_rerun": False},
    }
    row["state_sha256"] = freeze._sha(row)
    return row


def make_freeze() -> dict:
    candidates = [
        frozen_candidate("BBB.WA", component="liquidity", hard=True, extra_failed_gate="position_risk"),
        frozen_candidate("CCC.WA", component="final_selection_rank", hard=False),
        frozen_candidate("DDD.WA", component="liquidity", hard=True),
        frozen_candidate("FFF.WA", component="position_risk", hard=True),
    ]
    body = {
        "schema_version": freeze.SCHEMA_VERSION,
        "market": "gpw",
        "date": "2026-09-07",
        "decision_at": "2026-09-07T10:00:00+02:00",
        "frozen_at": "2026-09-07T08:01:00Z",
        "source_payload_sha256": "source",
        "status": "frozen",
        "selected_symbol": "AAA.WA",
        "candidate_count": len(candidates),
        "economically_evaluable_count": len(candidates),
        "candidates": candidates,
        "contract": {"prospective_only": True, "preserve_existing_same_decision": True, "news_or_llm_rerun": False, "source_engine_writeback": False, "decision_influence": False, "future_outcome_requires_preexisting_freeze": True},
    }
    body["freeze_sha256"] = freeze._sha(body)
    return body


def payload() -> dict:
    return {
        "date": "2026-09-07",
        "generated_at": "2026-09-07T10:00:00+02:00",
        "decision": "TRANSAKCJA",
        "reason": "test selection",
        "selection": {"symbol": "AAA.WA", "ticker": "AAA", "name": "AAA", "sector": "test", "entry_zone": [99.0, 101.0], "stop": 95.0, "target": 110.0, "reward_risk": 2.0, "reference_price": 100.0},
        freeze.FIELD: make_freeze(),
    }


def t2(return_percent: float | None = None, *, status: str = "RESOLVED", r_multiple: float | None = None) -> list[dict]:
    row = {"status": status, "horizon_sessions": 2, "session": "2026-09-09"}
    if return_percent is not None:
        row["net_return_percent"] = return_percent
    if r_multiple is not None:
        row["r_multiple"] = r_multiple
    return [row]


def settlement_candidate(symbol: str, *, component: str, hard: bool, ret: float | None, status: str = "RESOLVED", opportunity: bool = False, extra_hard: str | None = None, evaluable: bool = True) -> dict:
    first = gate(component, False, hard=hard, stage="decision")
    hard_names = [component] if hard else []
    if extra_hard:
        hard_names.append(extra_hard)
    return {
        "candidate_id": f"gpw:2026-09-07:{symbol}:LONG",
        "symbol": symbol,
        "first_blocking_gate": first,
        "hard_blocked": bool(hard_names),
        "hard_blocking_gates": hard_names,
        "economically_evaluable": evaluable,
        "opportunity_candidate": opportunity,
        "horizons": t2(ret, status=status, r_multiple=(-0.6 if ret is not None and ret < 0 else 0.8 if ret is not None and ret > 0 else None)),
    }


def settled_record() -> dict:
    record = outcomes.build_record(payload(), config={"non_session_dates": []}, captured_at=datetime(2026, 9, 7, 8, 2, tzinfo=UTC))
    record["settlement"] = {
        "status": "DATA_GAP",
        "last_checked_at": "2026-09-09T18:00:00Z",
        "selected": {"symbol": "AAA.WA", "action": "LONG", "horizons": t2(2.0, r_multiple=0.4)},
        "rejected_candidates": [
            settlement_candidate("BBB.WA", component="liquidity", hard=True, ret=-3.0, extra_hard="position_risk"),
            settlement_candidate("CCC.WA", component="final_selection_rank", hard=False, ret=5.0, opportunity=True),
            settlement_candidate("DDD.WA", component="liquidity", hard=True, ret=4.0),
            settlement_candidate("FFF.WA", component="position_risk", hard=True, ret=None, status="DATA_GAP"),
            settlement_candidate("EEE.WA", component="market_data", hard=True, ret=9.0, evaluable=False),
        ],
        "summary": {"status": "DATA_GAP", "reason": "test"},
        "contract": {"source_snapshot_immutable": True, "historical_backfill_performed": False, "missing_review_is_zero_return": False, "decision_influence": False, "automatic_learning_writeback": False},
    }
    outcomes.verify_record(record)
    return record


class RejectionComponentValueAttributionTests(unittest.TestCase):
    def test_first_blocking_gate_gets_single_primary_attribution(self):
        artifact = attribution.build_attribution([settled_record()], generated_at=datetime(2026, 9, 10, tzinfo=UTC))
        liquidity = next(row for row in artifact["components"] if row["component"] == "liquidity")
        position_risk = next(row for row in artifact["components"] if row["component"] == "position_risk")
        self.assertEqual(liquidity["resolved_t2_count"], 2)
        self.assertEqual(position_risk["resolved_t2_count"], 0)
        self.assertEqual(position_risk["pending_or_data_gap_count"], 1)
        bbb = next(row for row in artifact["recent_observations"] if row["symbol"] == "BBB.WA")
        self.assertEqual(bbb["component"]["name"], "liquidity")
        self.assertFalse(bbb["double_counted"])

    def test_valuable_rejection_missed_winner_and_net_gate_value(self):
        artifact = attribution.build_attribution([settled_record()])
        liquidity = next(row for row in artifact["components"] if row["component"] == "liquidity")
        self.assertEqual(liquidity["valuable_rejection_count"], 1)
        self.assertEqual(liquidity["missed_winner_count"], 1)
        self.assertAlmostEqual(liquidity["avoided_loss_percent_sum"], 3.0)
        self.assertAlmostEqual(liquidity["missed_upside_percent_sum"], 4.0)
        self.assertAlmostEqual(liquidity["net_rejection_value_vs_flat_percent"], -1.0)
        self.assertEqual(liquidity["evidence_status"], "OBSERVING")

    def test_selected_relative_opportunity_cost_only_for_legal_alternative(self):
        artifact = attribution.build_attribution([settled_record()])
        ranking = next(row for row in artifact["components"] if row["component"] == "final_selection_rank")
        self.assertEqual(ranking["selected_relative_comparable_count"], 1)
        self.assertAlmostEqual(ranking["selected_relative_opportunity_cost_percent_sum"], 3.0)
        self.assertAlmostEqual(ranking["selected_relative_selection_advantage_percent_sum"], 0.0)
        liquidity = next(row for row in artifact["components"] if row["component"] == "liquidity")
        self.assertEqual(liquidity["selected_relative_comparable_count"], 0)
        ddd = next(row for row in artifact["recent_observations"] if row["symbol"] == "DDD.WA")
        self.assertFalse(ddd["selected_relative_comparable"])

    def test_data_gap_and_non_evaluable_candidate_are_not_zero_value_evidence(self):
        artifact = attribution.build_attribution([settled_record()])
        self.assertEqual(artifact["overall"]["excluded_not_economically_evaluable_count"], 1)
        self.assertEqual(artifact["overall"]["resolved_t2_observation_count"], 3)
        self.assertEqual(artifact["overall"]["pending_or_data_gap_observation_count"], 1)
        self.assertNotIn("market_data", [row["component"] for row in artifact["components"]])
        fff = next(row for row in artifact["recent_observations"] if row["symbol"] == "FFF.WA")
        self.assertEqual(fff["value_status"], "NOT_RESOLVED")
        self.assertNotIn("avoided_loss_percent", fff)
        self.assertNotIn("missed_upside_percent", fff)

    def test_evidence_thresholds_do_not_issue_automatic_keep_remove_decisions(self):
        base = settled_record()
        observations = []
        for index in range(50):
            row = attribution.observation_from_candidate(
                record={"decision_date": f"2026-10-{(index % 28) + 1:02d}", "source_snapshot_sha256": f"sha-{index}"},
                candidate=settlement_candidate(f"X{index}.WA", component="liquidity", hard=True, ret=-1.0),
                selected_t2_return=2.0,
            )
            observations.append(row)
        aggregate = attribution.aggregate_component("liquidity", observations)
        self.assertEqual(aggregate["evidence_status"], "EVALUABLE")
        self.assertNotIn("recommendation", aggregate)
        self.assertNotIn("keep", aggregate)
        self.assertNotIn("remove", aggregate)
        self.assertIsNotNone(base)  # preserve verified prospective-record fixture coverage

    def test_artifact_hash_and_zero_authority_are_enforced(self):
        artifact = attribution.build_attribution([settled_record()])
        attribution.verify_attribution(artifact)
        self.assertFalse(artifact["governance"]["decision_influence"])
        self.assertFalse(artifact["governance"]["automatic_learning_writeback"])
        self.assertFalse(artifact["governance"]["causal_component_effect_claim"])
        mutated = copy.deepcopy(artifact)
        mutated["governance"]["automatic_component_removal"] = True
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            attribution.verify_attribution(mutated)

    def test_build_store_uses_only_prospective_outcome_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            record = settled_record()
            (store / "2026-09-07.json").write_text(json.dumps(record), encoding="utf-8")
            (store / "index.json").write_text("{}", encoding="utf-8")
            artifact = attribution.build_store(store_dir=store, now=datetime(2026, 9, 10, tzinfo=UTC))
            self.assertEqual(artifact["overall"]["source_record_count"], 1)
            self.assertTrue((store / "component_value_attribution.json").exists())
            attribution.verify_attribution(json.loads((store / "component_value_attribution.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
