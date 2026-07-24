import unittest

import pandas as pd

from scripts.brace_spx_macro_events import (
    PointInTimeEvent,
    available_before,
    macro_surprise,
    monthly_event_features,
)


class MacroEventTests(unittest.TestCase):
    def macro_event(self, public_time="2024-01-10T13:30:00Z"):
        return PointInTimeEvent(
            event_id="cpi-2024-01",
            event_type="macro_release",
            schedule_type="scheduled",
            occurred_at_utc="2024-01-10T13:30:00Z",
            first_public_at_utc=public_time,
            ingested_at_utc="2024-01-10T13:30:10Z",
            source_url="https://example.test/cpi",
            source_name="official-statistics",
            source_tier=1,
            headline="CPI first release",
            direction="negative",
            intensity=0.7,
            novelty=0.8,
            confidence=1.0,
            affected_assets=("SPY",),
            actual_value=3.4,
            consensus_value=3.2,
            prior_value_as_known=3.1,
            revision_vintage="first_release",
        )

    def test_future_publication_is_excluded(self):
        event = self.macro_event()
        before = available_before([event], "2024-01-10T13:29:59Z")
        after = available_before([event], "2024-01-10T13:30:00Z")
        self.assertEqual(before, [])
        self.assertEqual(after, [event])

    def test_macro_surprise_uses_actual_minus_consensus(self):
        event = self.macro_event()
        self.assertAlmostEqual(macro_surprise(event, scale=1.0), 0.2)

    def test_unscheduled_event_cannot_be_known_before_occurrence(self):
        event = PointInTimeEvent(
            event_id="shock",
            event_type="geopolitical_shock",
            schedule_type="unscheduled",
            occurred_at_utc="2024-02-01T12:00:00Z",
            first_public_at_utc="2024-02-01T11:59:00Z",
            ingested_at_utc="2024-02-01T12:01:00Z",
            source_url="https://example.test/shock",
            source_name="wire",
            source_tier=2,
            headline="Shock",
        )
        with self.assertRaises(ValueError):
            event.validate()

    def test_monthly_features_use_only_prior_31_days(self):
        recent = self.macro_event()
        old = PointInTimeEvent(
            event_id="old-event",
            event_type="policy_announcement",
            schedule_type="scheduled",
            occurred_at_utc="2023-10-01T12:00:00Z",
            first_public_at_utc="2023-10-01T12:00:00Z",
            ingested_at_utc="2023-10-01T12:00:01Z",
            source_url="https://example.test/old",
            source_name="official",
            source_tier=1,
            headline="Old policy",
            direction="positive",
            intensity=1.0,
            novelty=1.0,
            confidence=1.0,
        )
        index = pd.DatetimeIndex(["2024-01-31"])
        features = monthly_event_features([recent, old], index)
        self.assertEqual(features.iloc[0]["event_count_31d"], 1.0)
        self.assertEqual(features.iloc[0]["macro_release_count_31d"], 1.0)
        self.assertGreater(features.iloc[0]["negative_event_pressure_31d"], 0.0)


if __name__ == "__main__":
    unittest.main()
