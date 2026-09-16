from __future__ import annotations

import unittest

from scripts import stock_trading_v2_admission_ledger as admission
from scripts import stock_trading_v2_contracts as contracts


POLICY = {
    "policy_version": "test-policy",
    "forced_trade_allowed": False,
    "markets": {
        "GPW": {"max_open_positions": 3, "minimum_entry_score": 72, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
        "US": {"max_open_positions": 3, "minimum_entry_score": 72, "minimum_reward_risk": 1.5, "maximum_risk_percent": 0.07},
    },
}


def event(*, selected: bool, conservative_ev: float = 0.1, score: float = 80.0, blocker=None):
    state = {
        "identity": {"name": "Test", "ticker": "TST", "sector": "test"},
        "decision_path": {
            "producer_decision": "TRANSAKCJA" if selected else "BRAK_TRANSAKCJI",
            "selection_mode": "NORMAL",
            "first_blocking_gate": blocker,
        },
        "score_state": {
            "score": score,
            "expected_value": {"conservative_ev_r": conservative_ev},
        },
        "market_state": {
            "historical_data_gate": {"accepted": True},
            "execution_data_gate": {"status": "accepted"},
        },
        "risk_plan": {
            "reference_price": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "risk_percent": 0.05,
            "reward_risk": 2.0,
        },
    }
    return contracts.make_experience_event(
        market="GPW",
        symbol="TST.WA",
        decision_at="2026-09-16T10:00:00+02:00",
        session_date="2026-09-16",
        selected=selected,
        source_engine="test",
        source_schema_version="test-v1",
        source_policy_version="test-policy",
        source_payload_sha256="abc123",
        candidate_state=state,
        recorded_at="2026-09-16T08:00:01Z",
    )


class StockTradingV2AdmissionLedgerTests(unittest.TestCase):
    def test_positive_selected_candidate_is_long(self):
        action, reason, diagnostics = admission.champion_admission(event(selected=True), POLICY)
        self.assertEqual(action, "LONG")
        self.assertEqual(reason, "qualified_high_expectancy_candidate")
        self.assertTrue(diagnostics["portfolio_qualification_ran"])

    def test_non_positive_conservative_ev_is_frozen_as_cash_reason(self):
        action, reason, _ = admission.champion_admission(event(selected=True, conservative_ev=-0.01), POLICY)
        self.assertEqual(action, "CASH")
        self.assertEqual(reason, "non_positive_conservative_expected_value")

    def test_producer_rejection_is_cash_with_original_blocker(self):
        action, reason, diagnostics = admission.champion_admission(event(selected=False, blocker="liquidity"), POLICY)
        self.assertEqual(action, "CASH")
        self.assertEqual(reason, "producer_rejected:liquidity")
        self.assertFalse(diagnostics["portfolio_qualification_ran"])

    def test_observation_is_hashed_and_shadow_only(self):
        payload = admission.make_observation(event(selected=True), policy=POLICY, recorded_at="2026-09-16T08:01:00Z")
        self.assertEqual(payload["champion"]["action"], "LONG")
        self.assertFalse(payload["governance"]["production_decision_influence"])
        admission.validate_observation(payload)
        payload["champion"]["reason"] = "tampered"
        with self.assertRaises(Exception):
            admission.validate_observation(payload)


if __name__ == "__main__":
    unittest.main()
