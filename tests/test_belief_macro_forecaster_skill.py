from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_macro_forecaster_skill import (
    empty_state,
    internal_historical_mae,
    settle,
)

UTC = timezone.utc


def expectations(period: str, event_at: datetime, forecast: float) -> dict:
    return {
        "provider": "test-feed",
        "releases": [{
            "region": "US",
            "indicator": "nonfarm_payrolls_change",
            "period": period,
            "event_at": event_at.isoformat().replace("+00:00", "Z"),
            "unit": "thousands",
            "forecasts": [{
                "institution": "Bank A",
                "forecast": forecast,
                "published_at": (event_at - timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
                "source_ref": "https://example.test/bank-a",
            }],
        }],
    }


def context(event_at: datetime, actual: float) -> dict:
    return {
        "status": "POST_RELEASE",
        "event": {
            "event_at": event_at.isoformat().replace("+00:00", "Z"),
            "title": "Employment Situation",
        },
        "actuals": [{
            "metric": "total_nonfarm_payroll_level",
            "value": 160000.0,
            "unit": "thousands",
            "comparable_metric": "nonfarm_payrolls_change",
            "comparable_value": actual,
            "comparable_unit": "thousands",
        }],
    }


class MacroForecasterSkillTests(unittest.TestCase):
    def test_prospective_errors_build_indicator_specific_mae(self):
        state = empty_state()
        rows = [
            ("2026-07", datetime(2026, 8, 7, 12, 30, tzinfo=UTC), 80.0, 70.0),
            ("2026-08", datetime(2026, 9, 4, 12, 30, tzinfo=UTC), 55.0, 65.0),
            ("2026-09", datetime(2026, 10, 2, 12, 30, tzinfo=UTC), 50.0, 29.0),
        ]
        for period, event_at, forecast, actual in rows:
            state, changed = settle(
                state,
                expectations(period, event_at, forecast),
                context(event_at, actual),
                now=event_at + timedelta(minutes=2),
            )
            self.assertEqual(changed, 1)

        skill = internal_historical_mae(
            state,
            institution="Bank A",
            indicator="nonfarm_payrolls_change",
        )
        self.assertIsNotNone(skill)
        mae, count = skill
        self.assertEqual(count, 3)
        self.assertAlmostEqual(mae, (10.0 + 10.0 + 21.0) / 3.0)

    def test_same_forecast_is_not_settled_twice(self):
        event_at = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)
        state, changed = settle(
            empty_state(),
            expectations("2026-09", event_at, 50.0),
            context(event_at, 29.0),
            now=event_at + timedelta(minutes=2),
        )
        self.assertEqual(changed, 1)
        state2, changed2 = settle(
            state,
            expectations("2026-09", event_at, 50.0),
            context(event_at, 29.0),
            now=event_at + timedelta(minutes=7),
        )
        self.assertEqual(changed2, 0)
        row = next(iter(state2["skills"].values()))
        self.assertEqual(row["settled_count"], 1)

    def test_pre_release_does_not_settle(self):
        event_at = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)
        pre = context(event_at, 29.0)
        pre["status"] = "PRE_RELEASE"
        state, changed = settle(
            empty_state(),
            expectations("2026-09", event_at, 50.0),
            pre,
            now=event_at - timedelta(minutes=5),
        )
        self.assertEqual(changed, 0)
        self.assertEqual(state["skills"], {})


if __name__ == "__main__":
    unittest.main()
