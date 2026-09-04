from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import daily_review_completeness as drc


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Warsaw")
POLICY = {
    "enabled": True,
    "timezone": "Europe/Warsaw",
    "review_time_local": "23:00",
    "review_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "completeness": {
        "strict_enforcement_from": "2026-09-07",
    },
}


def review(day: str, *, mode: str = "LIVE", trigger: str = "scheduled_daily_model_review") -> dict:
    return {
        "review_date": day,
        "reviewed_at": f"{day}T23:10:00+02:00",
        "review_trigger": trigger,
        "decision": "keep",
        "reason": "keep_original_thesis_not_invalidated",
        "observation_mode": mode,
    }


def base_week(reviews: list[dict] | None = None, *, exit_at: str = "2026-08-28T22:00:00+02:00") -> dict:
    return {
        "week_id": "2026-W35",
        "forecast_for_week_start": "2026-08-24",
        "forecast_for_week_end": "2026-08-28",
        "instruments": [
            {
                "instrument_id": "sp500_futures",
                "direction": "long",
                "entry_price": 6400.0,
                "entry_captured_at": "2026-08-24T08:25:00+02:00",
                "exit_price": 6450.0 if exit_at else None,
                "exit_captured_at": exit_at or None,
                "daily_reviews": reviews or [],
            }
        ],
    }


class DailyReviewCompletenessTests(unittest.TestCase):
    def test_w35_shape_detects_missing_26_and_27_as_data_gap_not_hold(self) -> None:
        week = base_week([review("2026-08-24"), review("2026-08-25")])
        audit = drc.audit_week(week, POLICY, as_of=datetime(2026, 8, 29, 0, 1, tzinfo=TZ))
        self.assertEqual(audit["status"], "DATA_GAP")
        missing_dates = {row["session_date"] for row in audit["missing_reviews"]}
        self.assertTrue({"2026-08-26", "2026-08-27"}.issubset(missing_dates))
        self.assertTrue(all(row["interpretation"] == "DATA_GAP_NOT_HOLD" for row in audit["missing_reviews"]))
        self.assertFalse(audit["historical_backfill_performed"])
        self.assertFalse(audit["formal_learning_eligible"])

    def test_exactly_one_review_per_open_session_passes(self) -> None:
        week = base_week([
            review("2026-08-24"),
            review("2026-08-25"),
            review("2026-08-26"),
            review("2026-08-27"),
        ])
        audit = drc.audit_week(week, POLICY, as_of=datetime(2026, 8, 29, 0, 1, tzinfo=TZ))
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["missing_reviews"], [])
        self.assertEqual(audit["duplicate_reviews"], [])
        self.assertTrue(audit["formal_learning_eligible"])

    def test_duplicate_scheduled_review_fails_closed(self) -> None:
        week = base_week([
            review("2026-08-24"), review("2026-08-25"),
            review("2026-08-26"), review("2026-08-26"),
            review("2026-08-27"),
        ])
        session = drc.evaluate_session(
            week,
            datetime(2026, 8, 26, tzinfo=TZ).date(),
            POLICY,
            now=datetime(2026, 8, 26, 23, 30, tzinfo=TZ),
        )
        self.assertEqual(session["status"], "DATA_GAP")
        self.assertEqual(len(session["duplicates"]), 1)
        self.assertFalse(session["formal_learning_eligible"])

    def test_material_event_review_does_not_masquerade_as_daily_review(self) -> None:
        week = base_week([
            review("2026-08-26", trigger="material_event_request"),
        ], exit_at=None)
        session = drc.evaluate_session(
            week,
            datetime(2026, 8, 26, tzinfo=TZ).date(),
            POLICY,
            now=datetime(2026, 8, 26, 23, 30, tzinfo=TZ),
        )
        self.assertEqual(session["status"], "DATA_GAP")
        self.assertEqual(session["reviewed_count"], 0)
        self.assertEqual(session["missing"][0]["interpretation"], "DATA_GAP_NOT_HOLD")

    def test_position_closed_before_review_cutoff_does_not_require_review(self) -> None:
        week = base_week([], exit_at="2026-08-26T18:00:00+02:00")
        session = drc.evaluate_session(
            week,
            datetime(2026, 8, 26, tzinfo=TZ).date(),
            POLICY,
            now=datetime(2026, 8, 26, 23, 30, tzinfo=TZ),
        )
        self.assertEqual(session["status"], "PASS")
        self.assertEqual(session["expected_count"], 0)

    def test_reconstructed_review_never_becomes_formal_learning_evidence(self) -> None:
        week = base_week([review("2026-08-26", mode="RECONSTRUCTED")], exit_at=None)
        session = drc.evaluate_session(
            week,
            datetime(2026, 8, 26, tzinfo=TZ).date(),
            POLICY,
            now=datetime(2026, 8, 26, 23, 30, tzinfo=TZ),
        )
        self.assertEqual(session["status"], "PASS")
        self.assertFalse(session["formal_learning_eligible"])
        self.assertTrue(session["reconstructed_review_present"])

    def test_gap_clears_today_complete_marker_so_hourly_retry_can_run(self) -> None:
        week = base_week([], exit_at=None)
        week["daily_position_review"] = {
            "enabled": True,
            "last_review_date": "2026-08-26",
            "last_reviewed_at": "2026-08-26T23:05:00+02:00",
        }
        session = drc.apply_current_state(
            week,
            POLICY,
            now=datetime(2026, 8, 26, 23, 15, tzinfo=TZ),
        )
        self.assertEqual(session["status"], "DATA_GAP")
        state = week["daily_position_review"]
        self.assertNotIn("last_review_date", state)
        self.assertEqual(state["last_attempt_date"], "2026-08-26")
        self.assertFalse(state["missing_review_is_hold"])
        self.assertEqual(state["session_history"][-1]["status"], "DATA_GAP")

    def test_successful_retry_upserts_same_session_instead_of_duplicating_history(self) -> None:
        week = base_week([], exit_at=None)
        week["daily_position_review"] = {
            "session_history": [{
                "schema_version": drc.SCHEMA_VERSION,
                "session_date": "2026-08-26",
                "status": "DATA_GAP",
            }]
        }
        week["instruments"][0]["daily_reviews"] = [review("2026-08-26")]
        session = drc.apply_current_state(
            week,
            POLICY,
            now=datetime(2026, 8, 26, 23, 25, tzinfo=TZ),
        )
        self.assertEqual(session["status"], "PASS")
        history = week["daily_position_review"]["session_history"]
        self.assertEqual(len([row for row in history if row["session_date"] == "2026-08-26"]), 1)
        self.assertEqual(history[-1]["status"], "PASS")
        row = week["instruments"][0]["daily_reviews"][0]
        self.assertEqual(row["observation_mode"], "LIVE")
        self.assertTrue(row["formal_learning_eligible"])
        self.assertFalse(row["missing_review_is_hold"])

    def test_strict_enforcement_is_prospective(self) -> None:
        self.assertFalse(drc.enforcement_active(datetime(2026, 8, 27).date(), POLICY))
        self.assertTrue(drc.enforcement_active(datetime(2026, 9, 7).date(), POLICY))

    def test_repository_w35_retains_historical_gap_without_backfill(self) -> None:
        policy = drc.load_policy(ROOT / "data/investments/daily_review_policy.json")
        week = json.loads((ROOT / "data/investments/weekly/2026-W35.json").read_text(encoding="utf-8"))
        audit = drc.audit_week(week, policy, as_of=datetime(2026, 8, 29, 0, 1, tzinfo=TZ))
        missing_dates = {row["session_date"] for row in audit["missing_reviews"]}
        self.assertIn("2026-08-26", missing_dates)
        self.assertIn("2026-08-27", missing_dates)
        self.assertFalse(audit["historical_backfill_performed"])

    def test_workflow_checkpoints_review_before_downstream_restore_and_enforcement(self) -> None:
        text = (ROOT / ".github/workflows/investments-weekly.yml").read_text(encoding="utf-8")
        review_i = text.index("python scripts/daily_position_review.py")
        checkpoint_i = text.index("Checkpoint daily review durably")
        enforce_i = text.index("--enforce")
        restore_i = text.index("--mode ensure-exposure")
        self.assertLess(review_i, checkpoint_i)
        self.assertLess(checkpoint_i, enforce_i)
        self.assertLess(enforce_i, restore_i)


if __name__ == "__main__":
    unittest.main()
