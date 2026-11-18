"""Offline tests for immutable US fundamental prospective trial freeze."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

from scripts import stock_trading_fundamental_policy as policy
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_experience_store as store
from scripts import stock_trading_v2_fundamental_trial as trials


DECISION = "2026-11-20T15:00:00Z"
NOW = datetime(2026, 11, 20, 15, 1, tzinfo=timezone.utc)


def fundamentals(high: bool):
    metrics = {
        key: upper if high else lower
        for key, _weight, lower, upper in policy.SCORE_FACTORS
    }
    score, coverage = policy.recompute_score(metrics)
    return {
        "status": "COMPLETE", "fundamental_score": score,
        "observed_score_weight": coverage, "metrics": metrics,
        "fiscal_end": "2025-12-31", "production_decision_influence": False,
        "source_facts": {"revenue_fy": {
            "accession": "0000001-26-0001", "filed": "2026-02-10",
            "end": "2025-12-31", "form": "10-K"}},
    }


def sample():
    rows = [
        {"symbol": "WEAK", "market_data_symbol": "WEAK", "utility": 80,
         "eligible": True, "already_held": False, "deep_rank": 1,
         "fundamental_analysis": fundamentals(False),
         "research_risk_plan": {"status": "VALID_RESEARCH_REFERENCE",
                                "reference_price": 100, "stop": 95, "target": 110}},
        {"symbol": "GOOD", "market_data_symbol": "GOOD", "utility": 70,
         "eligible": True, "already_held": False, "deep_rank": 2,
         "fundamental_analysis": fundamentals(True),
         "research_risk_plan": {"status": "VALID_RESEARCH_REFERENCE",
                                "reference_price": 100, "stop": 95, "target": 110}},
    ]
    opp = {
        "market": "US", "generated_at": DECISION, "opportunity_sha256": "o" * 64,
        "source_evidence_sha256": "e" * 64, "cash": {
            "utility": 50.0, "minimum_new_position_edge_points": 5.0},
        "evaluated_candidates": rows,
    }
    evidence = {
        "market": "US", "evidence_sha256": "e" * 64,
        "source_fundamentals_sha256": "f" * 64,
        "fundamental_score_influence": False,
        "candidates": [
            {"symbol": r["symbol"], "fundamental_analysis": r["fundamental_analysis"]}
            for r in rows],
    }
    protocol = {
        "schema_version": "stock-trading-v2-fundamental-trial-config-v1",
        "policy_id": policy.POLICY_ID, "fixed_n": 30,
        "experiment_not_before": "2026-11-19T00:00:00Z",
    }
    return opp, evidence, protocol


class TrialTests(unittest.TestCase):
    def test_quality_gate_cannot_add_candidates(self):
        opp, _ev, _cfg = sample()
        self.assertEqual(policy.select_one(opp, apply_fundamentals=False)["symbol"], "WEAK")
        self.assertEqual(policy.select_one(opp, apply_fundamentals=True)["symbol"], "GOOD")
        accepted, rejected = policy.filter_live(opp["evaluated_candidates"], decision_at=DECISION)
        self.assertEqual([row["symbol"] for row in accepted], ["GOOD"])
        self.assertEqual(rejected[0]["reason"], "below_fundamental_quality_floor")
        self.assertEqual(accepted[0]["utility"], 70)

    def test_freeze_exact_event_and_no_same_day_overwrite(self):
        opp, evidence, protocol = sample()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "trials"
            experiences = Path(tmp) / "experiences"
            frozen_events = []
            for row in opp["evaluated_candidates"]:
                event = contracts.make_experience_event(
                    market="US", symbol=row["symbol"], decision_at=DECISION,
                    session_date="2026-11-20", selected=row["symbol"] == "WEAK",
                    source_engine="stock-trading-v2-opportunity-engine",
                    source_schema_version="stock-trading-v2-portfolio-opportunity-v1",
                    source_policy_version="shadow", source_payload_sha256="o"*64,
                    candidate_state={"risk_plan": row["research_risk_plan"]},
                    recorded_at=DECISION)
                store.persist_event(experiences, event)
                frozen_events.append(event)
            with mock.patch.object(trials.opportunity_events, "events_from_snapshots",
                                   return_value=frozen_events):
                result = trials.freeze(
                    opp, evidence, protocol=protocol, root=root, experience_root=experiences, frozen_at=NOW)
                self.assertEqual(result["status"], "FROZEN")
                self.assertTrue(result["changed"])
                self.assertEqual(trials.verify_root(root, protocol)["frozen_trials"], 1)
                other = dict(opp)
                other["opportunity_sha256"] = "x"*64
                second = trials.freeze(
                    other, evidence, protocol=protocol, root=root, experience_root=experiences,
                    frozen_at=NOW + timedelta(minutes=1))
                self.assertEqual(second["status"], "ALREADY_FROZEN")
            stored = json.loads((root / "2026-11-20.json").read_text())
            self.assertEqual(stored["baseline"]["candidate_symbol"], "WEAK")
            self.assertEqual(stored["challenger"]["candidate_symbol"], "GOOD")
            stored["baseline"]["candidate_symbol"] = "GOOD"
            with self.assertRaises(ValueError):
                trials.validate_receipt(stored, protocol)

    def test_late_freeze_rejected(self):
        opp, evidence, protocol = sample()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(trials.freeze(
                opp, evidence, protocol=protocol,
                root=Path(tmp), frozen_at=NOW + timedelta(hours=2)
            )["status"], "LATE_OR_FUTURE_DECISION")

    def test_missing_or_after_decision_facts_fail_closed(self):
        opp, _ev, _cfg = sample()
        weak = opp["evaluated_candidates"][0]
        weak["fundamental_analysis"]["source_facts"]["revenue_fy"]["filed"] = "2026-11-20"
        self.assertFalse(policy.verify_fundamental(weak, decision_at=DECISION)[0])
        weak["fundamental_analysis"] = {}
        allowed, _rej = policy.filter_live(opp["evaluated_candidates"], decision_at=DECISION)
        self.assertEqual([r["symbol"] for r in allowed], ["GOOD"])


if __name__ == "__main__":
    unittest.main()
