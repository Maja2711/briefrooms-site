from __future__ import annotations

import unittest

from scripts import stock_trading_v2_admission_ledger as admission
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_outcome_replay as outcome
from scripts import stock_trading_v2_regret_engine as regret


POLICY = {
    "policy_version": "test",
    "forced_trade_allowed": False,
    "markets": {
        "GPW": {"max_open_positions": 3, "minimum_entry_score": 72, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
        "US": {"max_open_positions": 3, "minimum_entry_score": 72, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
    },
}

CONFIG = {
    "horizons_sessions": [2],
    "regret": {"minimum_comparable_candidates": 2},
    "learning": {
        "minimum_observations_for_hypothesis": 1,
        "minimum_observations_for_challenger": 2,
        "minimum_unique_symbols": 1,
        "learnable_components": ["entry_score_below_threshold", "non_positive_conservative_expected_value"],
    },
}


def make_event(symbol: str, *, selected: bool, blocker=None, score=80.0):
    return contracts.make_experience_event(
        market="US",
        symbol=symbol,
        decision_at="2026-09-10T10:00:00-04:00",
        session_date="2026-09-10",
        selected=selected,
        source_engine="test",
        source_schema_version="test-v1",
        source_policy_version="test",
        source_payload_sha256="same-group",
        candidate_state={
            "decision_path": {
                "producer_decision": "TRADE" if selected else "NO_TRADE",
                "selection_mode": "NORMAL",
                "first_blocking_gate": blocker,
            },
            "score_state": {"score": score, "expected_value": {"conservative_ev_r": 0.1}},
            "market_state": {"historical_data_gate": {"accepted": True}, "execution_data_gate": {"status": "accepted"}},
            "risk_plan": {"reference_price": 100, "stop": 95, "target": 110, "risk_percent": 0.05, "reward_risk": 2.0},
        },
        recorded_at="2026-09-10T14:00:01Z",
    )


def make_outcome(event, net_r: float):
    replay = {"status": "SETTLED", "horizon_sessions": 2, "net_r": net_r}
    return outcome.make_outcome(event, replay, replay_version="test", settled_at="2026-09-13T00:00:00Z")


class StockTradingV2RegretEngineTests(unittest.TestCase):
    def test_missed_rejected_winner_creates_false_negative_regret_and_hypothesis(self):
        chosen = make_event("CHOSEN", selected=True)
        rejected = make_event("MISSED", selected=False, blocker="entry_score_below_threshold", score=70)
        admissions = [
            admission.make_observation(chosen, policy=POLICY, recorded_at="2026-09-10T14:01:00Z"),
            admission.make_observation(rejected, policy=POLICY, recorded_at="2026-09-10T14:01:00Z"),
        ]
        report = regret.build_report(
            [chosen, rejected],
            admissions,
            [make_outcome(chosen, -0.5), make_outcome(rejected, 1.0)],
            CONFIG,
            generated_at="2026-09-14T00:00:00Z",
        )
        comparison = report["horizons"]["2"]["comparisons"][0]
        self.assertEqual(comparison["champion_net_r"], -0.5)
        self.assertEqual(comparison["best_rejected_net_r"], 1.0)
        self.assertEqual(comparison["opportunity_regret_r"], 1.5)
        self.assertEqual(comparison["false_positive_cost_r"], 0.5)
        self.assertEqual(comparison["false_negative_cost_r"], 1.5)
        self.assertEqual(report["challenger_hypotheses"][0]["component"], "entry_score_below_threshold")
        self.assertFalse(report["challenger_hypotheses"][0]["automatic_production_change"])
        regret.validate_report(report)

    def test_cash_beats_all_negative_candidates_without_fake_regret(self):
        a = make_event("A", selected=False, blocker="entry_score_below_threshold")
        b = make_event("B", selected=False, blocker="entry_score_below_threshold")
        admissions = [
            admission.make_observation(a, policy=POLICY),
            admission.make_observation(b, policy=POLICY),
        ]
        report = regret.build_report(
            [a, b], admissions, [make_outcome(a, -0.4), make_outcome(b, -0.2)], CONFIG
        )
        comparison = report["horizons"]["2"]["comparisons"][0]
        self.assertEqual(comparison["champion_net_r"], 0.0)
        self.assertEqual(comparison["best_legal_event_id"], "CASH")
        self.assertEqual(comparison["opportunity_regret_r"], 0.0)


if __name__ == "__main__":
    unittest.main()
