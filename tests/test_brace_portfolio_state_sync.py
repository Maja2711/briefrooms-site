from __future__ import annotations

import unittest

from scripts.brace_portfolio_state_sync import (
    active_position_ids,
    filter_position_recommendations,
    live_analysis_portfolio,
    reconcile_public_decisions,
)


class PortfolioStateSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paper = {
            "positions": [
                {"id": "fwia", "status": "active", "quantity": 10},
                {"id": "jpm", "status": "paper_active", "quantity": 3},
            ],
            "closed_positions": [{"id": "spgi", "quantity": 5}],
            "transactions": [],
        }

    def test_live_control_uses_paper_holdings(self) -> None:
        baseline = {"positions": [{"id": "spgi", "status": "active", "quantity": 5}]}
        registry = {"controller_state": "PROBATIONARY_CONTROL"}
        selected = live_analysis_portfolio(registry, baseline, self.paper)
        self.assertIs(selected, self.paper)
        self.assertEqual(active_position_ids(selected), {"fwia", "jpm"})

    def test_closed_position_is_not_published_as_live_assessment(self) -> None:
        rows = [
            {"instrument": "spgi", "action": "WATCH"},
            {"instrument": "jpm", "action": "HOLD"},
        ]
        self.assertEqual(filter_position_recommendations(rows, self.paper), [{"instrument": "jpm", "action": "HOLD"}])

    def test_decision_queue_is_reconciled_with_actual_portfolio_state(self) -> None:
        decisions = [
            {"decision_id": "replace-spgi", "action": "REPLACE", "instrument": "spgi", "replacement_instrument": "jpm"},
            {"decision_id": "expired-add", "action": "ADD", "instrument": "sxr8"},
            {"decision_id": "waiting-add", "action": "ADD", "instrument": "sxrz"},
        ]
        orders = {"orders": [
            {"decision_id": "replace-spgi", "status": "FAILED", "failure_reason": "INSTRUMENT_NOT_AVAILABLE"},
            {"decision_id": "expired-add", "status": "EXPIRED", "failure_reason": "SIGNAL_EXPIRED"},
            {"decision_id": "waiting-add", "status": "WAITING_FOR_MARKET", "failure_reason": "MARKET_CLOSED"},
        ]}
        result = reconcile_public_decisions(decisions, self.paper, orders)
        by_id = {item["decision_id"]: item for item in result}
        self.assertEqual(by_id["replace-spgi"]["execution_status"], "ALREADY_APPLIED")
        self.assertEqual(by_id["waiting-add"]["execution_status"], "PENDING")
        self.assertNotIn("expired-add", by_id)


if __name__ == "__main__":
    unittest.main()
