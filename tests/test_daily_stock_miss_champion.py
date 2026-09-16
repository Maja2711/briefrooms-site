from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import daily_stock_champion_challenger as champion
from scripts import daily_stock_miss_engine as miss
from scripts import daily_stock_self_improvement as self_improve


def record(
    *,
    day: str = "2026-09-08",
    selected_return: float = -2.0,
    candidate_return: float = 2.0,
    gate: str = "shortlist_rank",
    hard: bool = False,
    opportunity: bool = True,
    evaluable: bool = True,
    settled: bool = True,
    activated: bool = True,
    symbol: str = "AAA.WA",
) -> dict:
    return {
        "schema_version": "gpw-rejected-candidate-outcomes-v1",
        "market": "gpw",
        "decision_date": day,
        "source_snapshot_sha256": f"sha-{day}",
        "source_snapshot": {
            "market": "gpw",
            "governance": {
                "prospective_only": True,
                "historical_backfill": False,
                "decision_influence": False,
                "ranking_writeback": False,
                "gate_writeback": False,
                "production_trade_writeback": False,
                "automatic_learning_writeback": False,
                "automatic_promotion": False,
            },
        },
        "settlement": {
            "status": "RESOLVED" if settled else "PENDING",
            "summary": {
                "status": "RESOLVED" if settled else "PENDING",
                "selected_symbol": "SEL.WA",
                "selected_return_percent": selected_return,
            },
            "contract": {"source_snapshot_immutable": True},
            "rejected_candidates": [
                {
                    "candidate_id": f"gpw:{day}:{symbol}:LONG",
                    "symbol": symbol,
                    "first_blocking_gate": {
                        "name": gate,
                        "hard": hard,
                        "passed": False,
                        "stage": "ranking" if not hard else "quant_screen",
                        "reason": "test",
                    },
                    "hard_blocked": hard,
                    "hard_blocking_gates": [gate] if hard else [],
                    "economically_evaluable": evaluable,
                    "opportunity_candidate": opportunity,
                    "horizons": [
                        {
                            "status": "RESOLVED" if settled else "PENDING",
                            "horizon_sessions": 2,
                            "activated": activated,
                            "net_return_percent": candidate_return,
                        }
                    ],
                }
            ],
        },
    }


def score_report(*, count: int = 3) -> dict:
    records = []
    for index in range(count):
        records.append(record(
            day=f"2026-09-{8 + index:02d}",
            selected_return=-1.0,
            candidate_return=1.0 + index * 0.1,
            gate="minimum_composite_score",
            symbol=f"S{index}.WA",
        ))
    return miss.build_report({"gpw": records}, generated_at="2026-09-16T12:00:00Z")


class DailyStockMissChampionTests(unittest.TestCase):
    def test_01_resolved_legal_winner_is_miss(self):
        result = miss.analyze_record(record())
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["observations"][0]["classification"], "MISS")

    def test_02_pending_record_is_ignored(self):
        result = miss.analyze_record(record(settled=False))
        self.assertEqual(result["status"], "IGNORED")
        self.assertEqual(result["observations"], [])

    def test_03_hard_gate_winner_is_not_actionable_miss(self):
        result = miss.analyze_record(record(gate="liquidity", hard=True, candidate_return=8.0))
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["excluded_hard_gate_candidates"], 1)

    def test_04_soft_loser_is_avoided_loss(self):
        result = miss.analyze_record(record(selected_return=1.0, candidate_return=-2.0))
        self.assertEqual(result["observations"][0]["classification"], "AVOIDED_LOSS")

    def test_05_negative_but_better_candidate_is_relative_improvement_not_miss(self):
        result = miss.analyze_record(record(selected_return=-5.0, candidate_return=-1.0))
        self.assertEqual(result["observations"][0]["classification"], "RELATIVE_IMPROVEMENT")

    def test_06_small_difference_is_neutral(self):
        result = miss.analyze_record(record(selected_return=0.1, candidate_return=0.3))
        self.assertEqual(result["observations"][0]["classification"], "NEUTRAL")

    def test_07_not_activated_zero_return_can_be_neutral(self):
        result = miss.analyze_record(record(selected_return=0.0, candidate_return=0.0, activated=False))
        observation = result["observations"][0]
        self.assertFalse(observation["activated"])
        self.assertEqual(observation["classification"], "NEUTRAL")

    def test_08_patterns_aggregate_by_market_and_gate(self):
        report = score_report(count=3)
        self.assertEqual(len(report["patterns"]), 1)
        pattern = report["patterns"][0]
        self.assertEqual(pattern["gate"], "minimum_composite_score")
        self.assertEqual(pattern["misses"], 3)
        self.assertEqual(pattern["unique_symbols"], 3)

    def test_09_report_hash_detects_tamper(self):
        report = score_report(count=3)
        miss.validate_report(report)
        tampered = copy.deepcopy(report)
        tampered["misses"] += 1
        with self.assertRaises(ValueError):
            miss.validate_report(tampered)

    def test_10_allowlisted_score_pattern_becomes_research_intent_only(self):
        report = score_report(count=3)
        payload = champion.build_intents(report, generated_at="2026-09-16T12:01:00Z")
        champion.validate_intents(payload)
        self.assertEqual(len(payload["challenger_intents"]), 1)
        intent = payload["challenger_intents"][0]
        self.assertEqual(intent["status"], "RESEARCH_INTENT_ONLY")
        self.assertFalse(intent["production_authority"])
        self.assertEqual(intent["from_value"] - intent["to_value"], 1.0)

    def test_11_unsupported_soft_gate_stays_hypothesis_only(self):
        report = miss.build_report({"gpw": [record()]}, generated_at="2026-09-16T12:00:00Z")
        payload = champion.build_intents(report, generated_at="2026-09-16T12:01:00Z", minimum_misses=1)
        self.assertEqual(payload["challenger_intents"], [])
        self.assertEqual(payload["hypothesis_only"][0]["gate"], "shortlist_rank")

    def test_12_insufficient_score_evidence_does_not_create_challenger(self):
        report = score_report(count=2)
        payload = champion.build_intents(report, generated_at="2026-09-16T12:01:00Z")
        self.assertEqual(payload["challenger_intents"], [])
        self.assertEqual(payload["hypothesis_only"][0]["reason"], "insufficient_pattern_support_for_challenger_intent")

    def test_13_production_promotion_enabled_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data/investments"
            path.mkdir(parents=True)
            (path / "statistical_promotion_gate_v2_config.json").write_text(json.dumps({
                "promotion_methodology_version": 2,
                "production_promotion_enabled": True,
            }), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                champion.assert_production_freeze(repo_root=root)

    def test_14_hypothesis_proposal_never_writes_registry_or_production(self):
        report = score_report(count=3)
        intents = champion.build_intents(report, generated_at="2026-09-16T12:01:00Z")
        proposals = self_improve.build_hypothesis_proposals(report, intents, generated_at="2026-09-16T12:02:00Z")
        self_improve.validate_proposals(proposals)
        self.assertGreaterEqual(len(proposals["proposals"]), 1)
        proposal = proposals["proposals"][0]
        self.assertFalse(proposal["automatic_registry_write"])
        self.assertFalse(proposal["production_authority"])
        self.assertFalse(proposals["governance"]["automatic_promotion"])

    def test_15_full_adapter_preserves_pr35_pr36_as_only_promotion_route(self):
        report = score_report(count=3)
        payload = champion.build_intents(report, generated_at="2026-09-16T12:01:00Z")
        intent = payload["challenger_intents"][0]
        self.assertEqual(intent["route"]["candidate_engine"], "autonomous_policy_promotion_v2.PR35")
        self.assertEqual(intent["route"]["confirmation_gate"], "statistical_promotion_gate_v2.PR36")
        self.assertFalse(intent["route"]["production_promotion_enabled"])
        self.assertFalse(intent["route"]["automatic_registry_mutation"])

    def test_16_current_repo_outcomes_parse_and_validate(self):
        report = miss.build_from_repo()
        miss.validate_report(report)
        self.assertGreaterEqual(report["records_seen"], 1)
        self.assertGreaterEqual(report["resolved_records"], 1)
        for pattern in report["patterns"]:
            if pattern["hard_gate"]:
                self.assertFalse(pattern["learnable"])


if __name__ == "__main__":
    unittest.main()
