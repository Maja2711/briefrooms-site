import json
import tempfile
import unittest
from pathlib import Path

from scripts.experiment_registry import ALLOWED_CATEGORIES, ALLOWED_STATUSES, build_registry


class ExperimentRegistryTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, payload: dict) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_registry_is_logical_inventory_not_workflow_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(Path(tmp))
        ids = {row["id"] for row in registry["experiments"]}
        self.assertEqual(len(ids), 8)
        self.assertIn("eurusd-abc-live-shadow", ids)
        self.assertIn("belief-aris-shadow", ids)
        self.assertFalse(any("validation" in item.lower() for item in ids))
        self.assertTrue(all(row["system_class"] == "LAB" for row in registry["experiments"]))
        self.assertTrue(all(row["production_impact"] is False for row in registry["experiments"]))
        self.assertTrue(registry["authority"]["read_only"])
        self.assertFalse(registry["authority"]["automatic_promotion"])

    def test_summary_and_taxonomy_are_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(Path(tmp))
        rows = registry["experiments"]
        self.assertTrue(all(row["status"] in ALLOWED_STATUSES for row in rows))
        self.assertTrue(all(row["category"] in ALLOWED_CATEGORIES for row in rows))
        summary = registry["summary"]
        self.assertEqual(summary["total"], len(rows))
        self.assertEqual(
            summary["total"],
            summary["active"]
            + summary["awaiting_evidence"]
            + summary["promotion_candidates"]
            + summary["parked_or_killed"]
            + summary["errors"],
        )

    def test_timesfm_does_not_promote_small_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "data/investments/timesfm_shadow_public_pl.json",
                {
                    "generated_at": "2026-09-02T12:00:00Z",
                    "experiment": {
                        "activated_at": "2026-08-25T20:47:07Z",
                        "model_id": "google/timesfm",
                        "research_only": True,
                        "decision_influence": False,
                    },
                    "history": [
                        {"horizons": {"1h": {"direction_correct": True}}},
                        {"horizons": {"1h": {"direction_correct": False}}},
                        {"horizons": {"1h": {"direction_correct": True}}},
                    ],
                },
            )
            registry = build_registry(root)
        row = next(x for x in registry["experiments"] if x["id"] == "timesfm-shadow")
        self.assertEqual(row["sample_count"], 3)
        self.assertAlmostEqual(row["primary_metric"]["value"], 2 / 3)
        self.assertEqual(row["status"], "INSUFFICIENT_DATA")
        self.assertNotEqual(row["status"], "PROMOTE")

    def test_gse_candidate_requires_human_review_and_stays_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "data/gse/gse_v2_lab_public.json",
                {
                    "activity": {"projection_generated_at": "2026-09-02T14:32:22Z"},
                    "engine": {"decision_influence": False},
                    "best_horizon": {
                        "n": 94,
                        "label": "30d",
                        "baseline_brier": 0.26,
                        "brier_improvement_pct": 7.35,
                        "hit_rate": 0.596,
                    },
                    "challenger": {
                        "status": "eligible_for_human_shadow_review",
                        "automatically_applied": False,
                    },
                },
            )
            registry = build_registry(root)
        row = next(x for x in registry["experiments"] if x["id"] == "gse-v2-learning-lab")
        self.assertEqual(row["status"], "CONTINUE")
        self.assertFalse(row["automatic_promotion"])
        self.assertFalse(row["production_impact"])

    def test_wes_uses_prospective_pair_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "data/investments/wes_incremental_alpha_report.json",
                {
                    "overall": {"resolved_pairs": 0, "mean_incremental_alpha_percent": None},
                    "sample": {
                        "economic_decisions": 0,
                        "minimum_before_descriptive_analysis": 12,
                        "status": "collecting_prospective_pairs",
                    },
                },
            )
            registry = build_registry(root)
        row = next(x for x in registry["experiments"] if x["id"] == "wes-incremental-alpha")
        self.assertEqual(row["minimum_sample"], 12)
        self.assertEqual(row["sample_count"], 0)
        self.assertEqual(row["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
