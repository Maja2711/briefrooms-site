from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import gpw_daily_control_loop as loop
from scripts import gpw_daily_pick as gpw


WARSAW = ZoneInfo("Europe/Warsaw")


def resolved_trade(*, day: str, sector: str = "banki", r: float = 1.0, exit_reason: str = "target") -> dict:
    return {
        "date": day,
        "decision": "TRANSAKCJA",
        "selection": {
            "symbol": "PKO.WA",
            "ticker": "PKO",
            "sector": sector,
            "scores": {
                "catalyst": 80,
                "relative_momentum": 75,
                "market_context": 70,
            },
        },
        "outcome": {
            "status": "RESOLVED",
            "activated": True,
            "return_percent": r,
            "r_multiple": r,
            "exit_reason": exit_reason,
        },
    }


class GpwDailyControlLoopTests(unittest.TestCase):
    def setUp(self):
        self.config = gpw.load_config()
        loop._ACTIVE_LEARNING_CONFIG = dict(self.config["learning"])

    def test_learning_is_neutral_before_sample_and_bounded_after_sample(self):
        minimum = self.config["learning"]["minimum_resolved_trades_for_adaptation"]
        history = [resolved_trade(day=f"2026-07-{index + 1:02d}") for index in range(minimum)]
        neutral, sample = loop.adaptive_history_expectancy_score(history[:-1], "banki", minimum)
        learned, learned_sample = loop.adaptive_history_expectancy_score(history, "banki", minimum)
        self.assertEqual(neutral, 50.0)
        self.assertEqual(sample, minimum - 1)
        self.assertGreater(learned, 50.0)
        self.assertEqual(learned_sample, minimum)
        self.assertLessEqual(abs(learned - 50), self.config["learning"]["max_historical_score_adjustment"])

    def test_learning_snapshot_records_last_resolved_lesson_without_mutating_weights(self):
        history = [
            resolved_trade(day="2026-08-01", r=-1.0, exit_reason="stop"),
            resolved_trade(day="2026-08-04", r=1.8, exit_reason="target"),
        ]
        snapshot = loop.build_learning_snapshot(
            history,
            self.config,
            now=datetime(2026, 8, 13, 7, 30, tzinfo=WARSAW),
        )
        self.assertFalse(snapshot["automatic_weight_changes"])
        self.assertTrue(snapshot["guardrails"]["weights_frozen"])
        self.assertEqual(snapshot["last_lesson"]["symbol"], "PKO")
        self.assertIn("cel", snapshot["last_lesson"]["lesson"].lower())

    def test_after_cutoff_stale_state_is_replaced_with_today_fail_closed_record(self):
        now = datetime(2026, 8, 13, 11, 0, tzinfo=WARSAW)
        snapshot = loop.build_learning_snapshot([], self.config, now=now)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            public = base / "current.json"
            history_dir = base / "history"
            metrics = base / "metrics.json"
            audit = base / "audit"
            learning = base / "learning.json"
            with (
                patch.object(gpw, "PUBLIC_PATH", public),
                patch.object(gpw, "HISTORY_DIR", history_dir),
                patch.object(gpw, "METRICS_PATH", metrics),
                patch.object(gpw, "AUDIT_DIR", audit),
                patch.object(loop, "LEARNING_PATH", learning),
                patch.object(loop, "_refresh_learning", return_value=(snapshot, None)),
            ):
                payload = loop.control_once(now=now)
                saved = gpw.load_json(public)
        self.assertEqual(payload["date"], "2026-08-13")
        self.assertEqual(payload["decision"], "AWARIA_DANYCH")
        self.assertEqual(saved["data_quality"]["failed_stage"], "missed_cutoff_guardian")
        self.assertTrue(saved["locked"])


if __name__ == "__main__":
    unittest.main()
