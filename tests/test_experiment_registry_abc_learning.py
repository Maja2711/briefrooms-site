from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.experiment_registry_learning_extension import build_registry


class ExperimentRegistryABCLearningTests(unittest.TestCase):
    def test_learning_enriches_existing_abc_experiment_without_creating_eighth_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path = root / "data/investments/eurusd_abc_public_pl.json"
            base_path.parent.mkdir(parents=True, exist_ok=True)
            base_path.write_text(json.dumps({
                "generated_at": "2026-09-03T09:00:00Z",
                "engine_version": "eurusd-daily-abc-v1.3.0",
                "mode": "LIVE_SHADOW",
                "sample": {"captures": 12},
                "history": [],
            }), encoding="utf-8")
            learning_path = root / "data/investments/eurusd_abc_learning_public.json"
            learning_path.write_text(json.dumps({
                "schema_version": "eurusd-abc-learning-public-v1",
                "generated_at": "2026-09-03T09:00:00Z",
                "experiment_id": "eurusd-abc-live-shadow",
                "mode": "PROSPECTIVE_SHARED_LEARNING_LOOP",
                "episode_count": 9,
                "prospective_only": True,
                "historical_backfill": False,
                "decision_influence": False,
                "automatic_policy_mutation": False,
                "cross_arm_writeback": False,
                "arms": {
                    "A": {"episode_count": 3, "mean_r": -0.2, "hit_rate": 0.333, "dominant_error": "DIRECTION_OR_TIMING_FAILURE", "error_recurrence_rate": 1.0, "policy_stability": 1.0, "lesson_candidate": {"eligible": False}},
                    "B": {"episode_count": 3, "mean_r": 0.4, "hit_rate": 0.667, "dominant_error": "FOLLOW_THROUGH_FAILURE", "error_recurrence_rate": 1.0, "policy_stability": 1.0, "lesson_candidate": {"eligible": False}},
                    "C": {"episode_count": 3, "mean_r": 0.1, "hit_rate": 0.667, "dominant_error": None, "error_recurrence_rate": None, "policy_stability": 1.0, "lesson_candidate": {"eligible": False}},
                },
            }), encoding="utf-8")
            registry = build_registry(root)
        self.assertEqual(len(registry["experiments"]), 7)
        row = next(item for item in registry["experiments"] if item["id"] == "eurusd-abc-live-shadow")
        learning = row["details"]["learning_loop"]
        self.assertTrue(learning["available"])
        self.assertEqual(learning["episode_count"], 9)
        self.assertEqual(set(learning["arms"]), {"A", "B", "C"})
        self.assertFalse(learning["automatic_policy_mutation"])
        self.assertFalse(learning["decision_influence"])
        self.assertFalse(row["production_impact"])
        self.assertFalse(row["automatic_promotion"])


if __name__ == "__main__":
    unittest.main()
