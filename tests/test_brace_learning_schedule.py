from datetime import datetime, timezone

from scripts.brace_portfolio_user_control import weekly_learning_schedule


def test_weekly_learning_schedule_detects_two_missed_reviews():
    now = datetime(2026, 8, 18, 16, 45, tzinfo=timezone.utc)
    schedule = weekly_learning_schedule(now, "2026-08-02T20:00:31+00:00")
    assert schedule["previous_scheduled_review_at"] == "2026-08-16T08:40:00+00:00"
    assert schedule["next_scheduled_review_at"] == "2026-08-23T08:40:00+00:00"
    assert schedule["overdue"] is True
    assert schedule["missed_scheduled_reviews"] == 2


def test_weekly_learning_schedule_is_current_after_sunday_review():
    now = datetime(2026, 8, 18, 16, 45, tzinfo=timezone.utc)
    schedule = weekly_learning_schedule(now, "2026-08-16T09:05:00+00:00")
    assert schedule["overdue"] is False
    assert schedule["missed_scheduled_reviews"] == 0
