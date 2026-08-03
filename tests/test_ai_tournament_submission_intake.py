from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ai_tournament_submission_intake as intake


class SubmissionIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaign = json.loads(
            (ROOT / "data" / "ai_tournament" / "intake_campaign.json").read_text(encoding="utf-8")
        )

    def valid_submission(self):
        return {
            "schema_version": "ai-tournament-submission-v2",
            "campaign_id": "briefrooms-ai-tournament-2026-02",
            "participant_id": "claude",
            "participant_display_name": "Claude",
            "decision_date": "2026-08-03",
            "strategy": "three_month_buy_and_hold",
            "execution_policy": "first_eligible_us_regular_session_open_after_all_submissions_are_validated_and_locked",
            "same_allocations_for_pln_and_usd": True,
            "allocations": [
                {"ticker": "MSFT", "company": "Microsoft", "weight_pct": 25, "selection_reason": "Reason 1"},
                {"ticker": "AMZN", "company": "Amazon", "weight_pct": 25, "selection_reason": "Reason 2"},
                {"ticker": "JPM", "company": "JPMorgan Chase", "weight_pct": 20, "selection_reason": "Reason 3"},
                {"ticker": "XOM", "company": "Exxon Mobil", "weight_pct": 20, "selection_reason": "Reason 4"},
            ],
            "cash_weight_pct": 10,
            "portfolio_thesis": "Balanced thesis.",
            "expected_three_month_driver": "Earnings and market breadth.",
            "biggest_portfolio_risk": "Broad risk-off reversal.",
            "expected_best_performer": "MSFT",
            "expected_highest_risk_position": "XOM",
            "confidence_pct": 70,
            "final_decision_locked": True,
        }

    def test_accepts_valid_locked_submission(self):
        value = intake.validate_submission(self.valid_submission(), self.campaign)
        self.assertEqual(value["participant_id"], "claude")
        self.assertEqual(sum(row["weight_pct"] for row in value["allocations"]) + value["cash_weight_pct"], 100)

    def test_rejects_duplicate_ticker(self):
        value = self.valid_submission()
        value["allocations"][1]["ticker"] = "MSFT"
        with self.assertRaisesRegex(intake.IntakeError, "Duplicate"):
            intake.validate_submission(value, self.campaign)

    def test_rejects_wrong_total(self):
        value = self.valid_submission()
        value["cash_weight_pct"] = 5
        with self.assertRaisesRegex(intake.IntakeError, "equal 100"):
            intake.validate_submission(value, self.campaign)

    def test_rejects_unlisted_ticker(self):
        value = self.valid_submission()
        value["allocations"][0]["ticker"] = "SPY"
        with self.assertRaisesRegex(intake.IntakeError, "outside allowed universe"):
            intake.validate_submission(value, self.campaign)

    def test_rejects_post_lock_edits_by_hash(self):
        first = self.valid_submission()
        second = self.valid_submission()
        second["confidence_pct"] = 71
        self.assertNotEqual(
            intake.sha256_text(intake.canonical_json(first)),
            intake.sha256_text(intake.canonical_json(second)),
        )


if __name__ == "__main__":
    unittest.main()
