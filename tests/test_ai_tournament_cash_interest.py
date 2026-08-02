import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_tournament_cash_interest import CashInterestError, accrue_cash, load_policy


class CashInterestTests(unittest.TestCase):
    def test_iorb_policy_is_daily_act_365(self):
        policy = load_policy()
        self.assertEqual(policy["benchmark"], "IORB")
        self.assertEqual(policy["day_count"], "ACT/365")
        self.assertEqual(policy["compounding"], "daily")
        self.assertEqual(policy["accrual_calendar"], "calendar_days")

    def test_weekend_accrues_three_calendar_days(self):
        policy = {
            "schema_version": "ai-tournament-cash-rate-policy-v1",
            "benchmark": "IORB",
            "day_count": "ACT/365",
            "compounding": "daily",
            "rate_schedule": [{"effective_date": "2026-01-01", "annual_rate_pct": 3.65}],
        }
        result = accrue_cash(1000.0, "2026-08-07", "2026-08-10", policy)
        expected = 1000.0 * (1.0 + 0.0365 / 365.0) ** 3
        self.assertEqual(result.calendar_days, 3)
        self.assertAlmostEqual(result.closing_balance, expected, places=10)

    def test_rate_change_is_applied_from_effective_date(self):
        policy = {
            "schema_version": "ai-tournament-cash-rate-policy-v1",
            "benchmark": "IORB",
            "day_count": "ACT/365",
            "compounding": "daily",
            "rate_schedule": [
                {"effective_date": "2026-01-01", "annual_rate_pct": 3.65},
                {"effective_date": "2026-08-05", "annual_rate_pct": 3.40},
            ],
        }
        result = accrue_cash(1000.0, "2026-08-03", "2026-08-07", policy)
        expected = 1000.0 * (1.0 + 0.0365 / 365.0) ** 2 * (1.0 + 0.0340 / 365.0) ** 2
        self.assertAlmostEqual(result.closing_balance, expected, places=10)
        self.assertEqual([segment["days"] for segment in result.segments], [2, 2])

    def test_first_close_has_no_overnight_interest(self):
        result = accrue_cash(1000.0, "2026-08-03", "2026-08-03")
        self.assertEqual(result.calendar_days, 0)
        self.assertEqual(result.interest_earned, 0.0)

    def test_negative_balance_is_rejected(self):
        with self.assertRaises(CashInterestError):
            accrue_cash(-1.0, "2026-08-03", "2026-08-04")


if __name__ == "__main__":
    unittest.main()
