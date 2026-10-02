from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_adapter_contract import Observation
from daily_eurusd_macro_fast import (
    _actual_payload,
    _macro_actuals_for_event,
    _previous_month,
    build_context,
)

UTC = timezone.utc


def observation(
    *,
    adapter: str,
    metric: str,
    value,
    metadata: dict,
    source: str = "test",
    source_ref: str = "https://example.test/source",
    unit: str = "unknown",
) -> Observation:
    return Observation.make(
        adapter=adapter,
        metric=metric,
        entity="US_MACRO" if adapter != "macro_expectations" else "EURUSD",
        observed_at="2026-10-02T12:31:00Z",
        value=value,
        unit=unit,
        source=source,
        source_type="primary" if adapter != "macro_expectations" else "secondary",
        source_ref=source_ref,
        reliability=0.99,
        independence_cluster=f"test:{adapter}:{metric}",
        tags=("test",),
        metadata=metadata,
    )


class StaticAdapter:
    def __init__(self, observations=()):
        self.observations = tuple(observations)

    def run(self, now):
        return SimpleNamespace(observations=self.observations, evidence=())


class FakeInterpreter:
    available = True

    def __init__(self):
        self.calls = 0

    def interpret(self, obs):
        self.calls += 1
        interpretation = SimpleNamespace(
            belief_id="eurusd.macro_surprise.supportive",
            direction=1,
            strength=0.8,
            confidence=0.9,
            materiality=0.95,
            horizon_hours=6,
            summary="Payroll release is EUR/USD supportive relative to the supplied expectations.",
            alternative_hypothesis="Revisions or rates repricing could offset the initial impulse.",
            model="test-model",
        )
        return SimpleNamespace(interpretation=interpretation)


class DailyEURUSDMacroFastTests(unittest.TestCase):
    def test_bls_previous_month_period_contract_matches_macro_data_adapter(self):
        event_at = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)
        self.assertEqual(_previous_month(event_at), "2026-M09")

    def test_payroll_actual_is_matched_and_exposes_comparable_monthly_change(self):
        event = {
            "title": "Employment Situation",
            "event_at": "2026-10-02T12:30:00Z",
        }
        payroll = observation(
            adapter="macro_data",
            metric="total_nonfarm_payroll_level",
            value=159800.0,
            unit="thousands",
            metadata={
                "data_period": "2026-M09",
                "latest_month_change_thousands": 42.0,
                "three_month_average_change_thousands": 61.0,
            },
        )
        unemployment = observation(
            adapter="macro_data",
            metric="unemployment_rate",
            value=4.5,
            unit="percent",
            metadata={
                "data_period": "2026-M09",
                "latest_month_change_percentage_points": 0.1,
            },
        )

        actuals = _macro_actuals_for_event((payroll, unemployment), event)
        self.assertEqual(len(actuals), 2)
        payload = _actual_payload(payroll)
        self.assertEqual(payload["comparable_metric"], "nonfarm_payrolls_change")
        self.assertEqual(payload["comparable_value"], 42.0)

    def test_post_release_official_payrolls_trigger_fresh_llm_interpretation(self):
        now = datetime(2026, 10, 2, 12, 31, tzinfo=UTC)
        calendar_obs = observation(
            adapter="macro_event_calendar",
            metric="scheduled_macro_event",
            value=-1.0 / 60.0,
            metadata={
                "uid": "employment-2026-10-02",
                "title": "Employment Situation",
                "event_at": "2026-10-02T12:30:00Z",
                "importance": "high",
            },
            source="U.S. Bureau of Labor Statistics",
            source_ref="https://www.bls.gov/schedule/",
            unit="hours_until_event",
        )
        payroll = observation(
            adapter="macro_data",
            metric="total_nonfarm_payroll_level",
            value=159800.0,
            unit="thousands",
            metadata={
                "data_period": "2026-M09",
                "latest_month_change_thousands": 42.0,
            },
            source="U.S. Bureau of Labor Statistics Public Data API",
            source_ref="https://api.bls.gov/publicAPI/v2/timeseries/data/",
        )
        unemployment = observation(
            adapter="macro_data",
            metric="unemployment_rate",
            value=4.5,
            unit="percent",
            metadata={"data_period": "2026-M09"},
            source="U.S. Bureau of Labor Statistics Public Data API",
            source_ref="https://api.bls.gov/publicAPI/v2/timeseries/data/",
        )
        interpreter = FakeInterpreter()

        context = build_context(
            now,
            calendar=StaticAdapter((calendar_obs,)),
            macro_data=StaticAdapter((payroll, unemployment)),
            expectations=StaticAdapter(()),
            interpreter=interpreter,
            previous={},
        )

        self.assertEqual(context["status"], "POST_RELEASE")
        self.assertEqual(context["actuals_status"], "READY")
        self.assertEqual(len(context["actuals"]), 2)
        self.assertTrue(context["post_release_ready"])
        self.assertTrue(context["decision_influence"])
        self.assertEqual(interpreter.calls, 1)
        self.assertEqual(
            context["actuals"][0]["comparable_metric"],
            "nonfarm_payrolls_change",
        )

    def test_pre_release_without_sourced_expectations_fails_closed(self):
        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        calendar_obs = observation(
            adapter="macro_event_calendar",
            metric="scheduled_macro_event",
            value=0.5,
            metadata={
                "uid": "employment-2026-10-02",
                "title": "Employment Situation",
                "event_at": "2026-10-02T12:30:00Z",
                "importance": "high",
            },
            source="U.S. Bureau of Labor Statistics",
            source_ref="https://www.bls.gov/schedule/",
            unit="hours_until_event",
        )
        interpreter = FakeInterpreter()

        context = build_context(
            now,
            calendar=StaticAdapter((calendar_obs,)),
            macro_data=StaticAdapter(()),
            expectations=StaticAdapter(()),
            interpreter=interpreter,
            previous={},
        )

        self.assertEqual(context["status"], "PRE_RELEASE")
        self.assertEqual(context["expectations_status"], "MISSING")
        self.assertFalse(context["decision_influence"])
        self.assertIsNone(context["llm"])
        self.assertEqual(interpreter.calls, 0)


if __name__ == "__main__":
    unittest.main()
