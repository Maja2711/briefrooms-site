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
        self.assertEqual(len(ids), 6)
        self.assertIn("eurusd-abc-live-shadow", ids)
        self.assertIn("eurusd-x-adaptive-shadow", ids)
        self.assertNotIn("ai-tournament-2026-02", ids)
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
        self.assertNotIn("benchmark", ALLOWED_CATEGORIES)
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

    def test_benchmark_policy_is_asset_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(Path(tmp))
        policy = registry["benchmark_policy"]
        self.assertFalse(policy["benchmark_is_experiment_category"])
        self.assertEqual(policy["fx"]["applicability"], "NOT_APPLICABLE")
        self.assertEqual(policy["crypto"]["applicability"], "NOT_APPLICABLE")
        self.assertEqual(policy["equities"]["applicability"], "WHEN_ECONOMICALLY_MEANINGFUL")
        self.assertEqual(policy["forecasting_and_learning"]["applicability"], "BASELINE_NOT_MARKET_BENCHMARK")


    def test_eurusd_x_reads_current_public_schema_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "data/investments/eurusd_x_public_pl.json",
                {
                    "schema_version": "eurusd-x-public-pl-v1",
                    "engine_version": "eurusd-x-v1.0.0",
                    "generated_at": "2026-10-01T08:59:08.868545Z",
                    "capture_count": 22,
                    "resolved_4h": 21,
                    "champion_setup_id": "X-BASE",
                    "active_challenger_id": "X-BELIEF",
                    "setups": [
                        {
                            "setup_id": "X-BASE",
                            "n": 21,
                            "brier": 0.200465,
                            "hit_rate": 0.789474,
                            "mean_signed_return_bps": 3.6338,
                            "signal_n": 19,
                        },
                        {
                            "setup_id": "X-BELIEF",
                            "n": 21,
                            "brier": 0.205492,
                            "hit_rate": 0.777778,
                        },
                    ],
                },
            )
            registry = build_registry(root)

        row = next(x for x in registry["experiments"] if x["id"] == "eurusd-x-adaptive-shadow")
        self.assertEqual(row["sample_count"], 21)
        self.assertEqual(row["minimum_sample"], 30)
        self.assertEqual(row["sample_unit"], "resolved_champion_4h_outcomes")
        self.assertEqual(row["status"], "INSUFFICIENT_DATA")
        self.assertEqual(row["version"], "eurusd-x-v1.0.0")
        self.assertEqual(row["details"]["captures"], 22)
        self.assertEqual(row["details"]["resolved"], 21)
        self.assertEqual(row["details"]["champion"], "X-BASE")
        self.assertEqual(row["details"]["challenger"], "X-BELIEF")
        self.assertAlmostEqual(row["primary_metric"]["value"], 0.200465)
        self.assertEqual(row["primary_metric"]["label"], "Brier Championa (4h)")

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
