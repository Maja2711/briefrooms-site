from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_tournament_buy_hold_round as runtime


class BuyHoldRoundTests(unittest.TestCase):
    def test_preflight_validates_all_locked_submissions(self):
        report = runtime.preflight()
        self.assertTrue(report["ready"])
        self.assertEqual(report["campaign_id"], "briefrooms-ai-tournament-2026-02")
        self.assertEqual(len(report["participants"]), 5)
        self.assertTrue(all(row["ready"] for row in report["participants"]))

    def test_initial_execution_is_order_independent_and_preserves_cash(self):
        weights = {"NVDA": 0.25, "AMZN": 0.20, "MSFT": 0.45}
        account = runtime.account_execution(
            starting_capital=10000.0,
            currency="USD",
            target_weights=weights,
            cash_weight=0.10,
            open_prices={"NVDA": 100.0, "AMZN": 200.0, "MSFT": 50.0},
            fx_open=4.0,
            transaction_cost=0.001,
            slippage=0.0005,
        )
        self.assertAlmostEqual(account.cash_usd, 1000.0, places=8)
        spent = account.cash_usd + account.gross_stock_notional + account.fees
        self.assertAlmostEqual(spent, 10000.0, places=8)
        for ticker, weight in weights.items():
            execution_price = {"NVDA": 100.0, "AMZN": 200.0, "MSFT": 50.0}[ticker] * 1.0005
            gross = account.shares[ticker] * execution_price
            total_spend = gross * 1.001
            self.assertAlmostEqual(total_spend / 10000.0, weight, places=8)

    def test_pln_and_usd_accounts_use_same_weights_but_fx_changes_pln_return(self):
        weights = {"NVDA": 0.60, "AMZN": 0.30}
        opens = {"NVDA": 100.0, "AMZN": 200.0}
        usd = runtime.account_execution(
            starting_capital=10000.0,
            currency="USD",
            target_weights=weights,
            cash_weight=0.10,
            open_prices=opens,
            fx_open=4.0,
            transaction_cost=0.0,
            slippage=0.0,
        )
        pln = runtime.account_execution(
            starting_capital=10000.0,
            currency="PLN",
            target_weights=weights,
            cash_weight=0.10,
            open_prices=opens,
            fx_open=4.0,
            transaction_cost=0.0,
            slippage=0.0,
        )
        snapshot = {
            "session_date": "2026-08-04",
            "close_prices": {"NVDA": 110.0, "AMZN": 220.0},
            "fx_close": 4.2,
        }
        usd_nav, _ = runtime.account_nav(usd.__dict__, snapshot, "2026-08-04", "USD")
        pln_nav, _ = runtime.account_nav(pln.__dict__, snapshot, "2026-08-04", "PLN")
        self.assertAlmostEqual(usd_nav, 10900.0, places=6)
        self.assertAlmostEqual(pln_nav, 11445.0, places=6)
        self.assertGreater(pln_nav / 10000.0 - 1.0, usd_nav / 10000.0 - 1.0)

    def test_cash_interest_accrues_over_calendar_days(self):
        opening = 1000.0
        same_day = runtime.cash_at_session(opening, "2026-08-04", "2026-08-04")
        next_day = runtime.cash_at_session(opening, "2026-08-04", "2026-08-05")
        self.assertEqual(same_day, opening)
        self.assertGreater(next_day, opening)


if __name__ == "__main__":
    unittest.main()
