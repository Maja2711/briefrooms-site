"""Offline proof that Fundamental Stock deployment is automatic but bounded."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import stock_trading_fundamental_policy as policy
from scripts import stock_trading_fundamental_governor as gov
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_production_bridge as bridge


NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


def state():
    return {"schema_version": gov.SCHEMA, "phase": "SHADOW",
            "policy_id": policy.POLICY_ID, "blocked_trial_ids": [],
            "activated_at": None, "formal_proof_sha256": None,
            "active_policy_sha256": None, "kill_switch_enabled": False}


class GovernorTests(unittest.TestCase):
    def test_automatic_promotion_needs_positive_formal_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.json"
            path.write_text(json.dumps(state()))
            audit = Path(tmp) / "audit.jsonl"
            with mock.patch.object(gov, "evaluate", return_value={"status": "COLLECTING", "frozen_sessions": 0}):
                result = gov.promote(Path(tmp), state_path=path, audit_path=audit, now=NOW)
            self.assertEqual(result["status"], "COLLECTING")
            self.assertEqual(gov._read(path)["phase"], "SHADOW")
            with mock.patch.object(gov, "evaluate", return_value={
                "status": "FORMAL_PASS", "proof_sha256": "p"*64,
                "policy_sha256": gov.trials.digest_file(Path(policy.__file__)),
                "episodes": [{"session_date": "2026-11-19"}],
                "quality": {"changed_episodes": 15},
                "horizons": [{"horizon_sessions": 1, "formal_pass": True},
                             {"horizon_sessions": 2, "formal_pass": True}],
            }):
                activated = gov.promote(Path(tmp), state_path=path, audit_path=audit, now=NOW)
            self.assertEqual(activated["status"], "AUTO_PROMOTED")
            self.assertTrue(gov.is_active(gov._read(path)))
            rolled_back = gov.rollback(gov._read(path), "corrupt_live_evidence", now=NOW,
                                       path=path, audit_path=audit)
            self.assertEqual(rolled_back["phase"], "ROLLED_BACK")
            self.assertIn("p"*64, rolled_back["blocked_trial_ids"])
            again = gov.promote(Path(tmp), state_path=path, audit_path=audit, now=NOW)
            self.assertEqual(again["status"], "BLOCKED_AFTER_ROLLBACK")
            audit_rows = [json.loads(line) for line in audit.read_text().splitlines()]
            self.assertEqual([x["event"] for x in audit_rows],
                             ["FUNDAMENTAL_AUTO_PROMOTED", "FUNDAMENTAL_AUTO_ROLLBACK"])

    def test_bootstrap_requires_economic_and_statistical_advantage(self):
        config = {"bootstrap_resamples": 400, "bootstrap_seed": 12,
                  "min_mean_uplift_percent": .10,
                  "min_positive_changed_fraction": .55,
                  "max_single_positive_contribution_share": .50}
        episodes = [{"changed": True, "returns": {1: .5, 2: .4}} for _ in range(30)]
        self.assertTrue(gov._test_horizon(episodes, config, 1)["formal_pass"])
        bad = [{"changed": True, "returns": {1: -.5, 2: -.4}} for _ in range(30)]
        self.assertFalse(gov._test_horizon(bad, config, 1)["formal_pass"])
        # One large winner is insufficient for promotion.
        bad_outlier = [{"changed": True, "returns": {1: 0.0, 2: 0.0}} for _ in range(29)] + [
            {"changed": True, "returns": {1: 10.0, 2: 10.0}}]
        self.assertFalse(gov._test_horizon(bad_outlier, config, 1)["formal_pass"])

    def test_main_evaluator_never_uses_preexisting_in_sample_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "data/investments/stock_trading_v2_fundamental_trial_config.json"
            dest.parent.mkdir(parents=True)
            dest.write_text(gov.PROTOCOL_PATH.read_text())
            script = root / "scripts/stock_trading_fundamental_policy.py"
            script.parent.mkdir(parents=True)
            script.write_bytes(Path(policy.__file__).read_bytes())
            result = gov.evaluate(research_root=root)
            self.assertEqual(result["status"], "COLLECTING")
            self.assertEqual(result["frozen_sessions"], 0)

    def test_live_source_hash_and_fundamentals_binding(self):
        fact = {"symbol": "GOOD", "fundamental_analysis": {
            "status": "COMPLETE", "fundamental_score": 80, "production_decision_influence": False}}
        evidence = {"market": "US", "source_fundamentals_sha256": "f"*64,
                    "fundamental_score_influence": False, "candidates": [fact]}
        evidence["evidence_sha256"] = contracts.payload_sha256(evidence)
        opp = {"market": "US", "generated_at": "2026-11-20T15:00:00Z",
               "source_evidence_sha256": evidence["evidence_sha256"],
               "evaluated_candidates": [{"symbol": "GOOD", "eligible": True,
                                         "fundamental_analysis": fact["fundamental_analysis"]}]}
        opp["opportunity_sha256"] = contracts.payload_sha256(opp)
        self.assertTrue(gov.validate_live_binding(opp, evidence, now=NOW)[0])
        changed = json.loads(json.dumps(evidence))
        changed["candidates"][0]["fundamental_analysis"]["fundamental_score"] = 0
        self.assertFalse(gov.validate_live_binding(opp, changed, now=NOW)[0])

    def test_bridge_gate_filter_cannot_change_cash_edge_or_risk(self):
        metrics = {
            key: low for key, _weight, low, _high in policy.SCORE_FACTORS
        }
        score, coverage = policy.recompute_score(metrics)
        candidate = {
            "symbol": "WEAK", "market_data_symbol": "WEAK",
            "deep_rank": 1, "utility": 80.0, "eligible": True,
            "fundamental_analysis": {
                "status": "COMPLETE", "fundamental_score": score,
                "observed_score_weight": coverage,
                "production_decision_influence": False,
                "fiscal_end": "2025-12-31", "metrics": metrics,
                "source_facts": {"revenue_fy": {"accession": "a", "form": "10-K",
                                               "filed": "2026-01-01", "end": "2025-12-31"}},
            }
        }
        opp = {"market": "US", "generated_at": "2026-11-20T15:00:00Z",
               "cash": {"utility": 50.0, "minimum_new_position_edge_points": 5.0},
               "decision": {"action": "BUY"}, "evaluated_candidates": [candidate]}
        config = {
            "maximum_opportunity_age_minutes": 360,
            "canary_max_open_positions_per_market": 1,
            "full_max_open_positions_per_market": 3,
        }
        with mock.patch.object(bridge.portfolio, "open_positions", return_value=[]):
            _canonical, audits, healthy = bridge.process_market(
                {"unchanged": True}, "US", opp, config=config,
                runtime={"phase": "FULL"}, now_utc=NOW,
                fundamental_active=True)
        self.assertTrue(healthy)
        self.assertEqual(audits[0]["action"], "fundamental_quality_rejected")
        self.assertEqual(audits[-1]["reason"], "fundamental_quality_gate_empty")


if __name__ == "__main__":
    unittest.main()
