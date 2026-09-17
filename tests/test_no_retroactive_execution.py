#!/usr/bin/env python3
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.no_retroactive_execution import (
    RetroactiveExecutionError,
    assert_live_decision,
    assert_live_fill,
    assert_recovery_execution_allowed,
    assert_shadow_cannot_publish_live,
)

UTC = timezone.utc
RUN = datetime(2026, 9, 17, 19, 19, tzinfo=UTC)


class NoRetroactiveExecutionTests(unittest.TestCase):
    def test_exact_sp500_incident_is_blocked(self):
        with self.assertRaises(RetroactiveExecutionError):
            assert_live_fill(
                decision_at="2026-09-14T17:40:55+02:00",
                entry_at="2026-09-14T17:45:00+02:00",
                run_started_at=RUN,
                fill_persisted_before_run=False,
                decision_persisted_before_run=False,
                mode="LIVE",
            )

    def test_old_preexisting_decision_cannot_receive_historical_fill_later(self):
        with self.assertRaises(RetroactiveExecutionError):
            assert_live_fill(
                decision_at="2026-09-14T17:40:55+02:00",
                entry_at="2026-09-14T17:45:00+02:00",
                run_started_at=RUN,
                fill_persisted_before_run=False,
                decision_persisted_before_run=True,
                mode="LIVE",
            )

    def test_old_preexisting_decision_may_execute_prospectively(self):
        assert_live_fill(
            decision_at="2026-09-14T17:40:55+02:00",
            entry_at="2026-09-17T21:10:00+02:00",
            run_started_at=RUN,
            fill_persisted_before_run=False,
            decision_persisted_before_run=True,
            mode="LIVE",
        )

    def test_current_decision_and_current_fill_pass(self):
        assert_live_fill(
            decision_at="2026-09-17T21:18:00+02:00",
            entry_at="2026-09-17T21:20:00+02:00",
            run_started_at=RUN,
            fill_persisted_before_run=False,
            decision_persisted_before_run=False,
            mode="LIVE",
        )

    def test_new_old_decision_is_blocked_even_before_fill(self):
        with self.assertRaises(RetroactiveExecutionError):
            assert_live_decision(
                decision_at="2026-09-14T17:40:55+02:00",
                run_started_at=RUN,
                persisted_before_run=False,
                mode="LIVE",
            )

    def test_existing_fill_is_grandfathered(self):
        assert_live_fill(
            decision_at="2026-09-14T17:40:55+02:00",
            entry_at="2026-09-14T17:45:00+02:00",
            run_started_at=RUN,
            fill_persisted_before_run=True,
            decision_persisted_before_run=True,
            mode="LIVE",
        )

    def test_shadow_replay_can_use_historical_time(self):
        assert_live_fill(
            decision_at="2020-01-01T10:00:00+00:00",
            entry_at="2020-01-01T10:05:00+00:00",
            run_started_at=RUN,
            fill_persisted_before_run=False,
            decision_persisted_before_run=False,
            mode="SHADOW",
        )

    def test_shadow_cannot_publish_live(self):
        with self.assertRaises(RetroactiveExecutionError):
            assert_shadow_cannot_publish_live(source_mode="SHADOW", target_mode="LIVE")

    def test_recovery_cannot_create_new_live_fill(self):
        with self.assertRaises(RetroactiveExecutionError):
            assert_recovery_execution_allowed(recovery_mode=True, fill_persisted_before_run=False, mode="LIVE")

    def test_entry_before_decision_is_blocked(self):
        with self.assertRaises(RetroactiveExecutionError):
            assert_live_fill(
                decision_at="2026-09-17T21:18:00+02:00",
                entry_at="2026-09-17T21:17:00+02:00",
                run_started_at=RUN,
                fill_persisted_before_run=False,
                decision_persisted_before_run=False,
                mode="LIVE",
            )


if __name__ == "__main__":
    unittest.main()
