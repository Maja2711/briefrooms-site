from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import autonomous_policy_observatory as obs
from scripts import autonomous_policy_promotion as ap
from scripts import statistical_promotion_gate as sg


class AutonomousPolicyObservatoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data/investments").mkdir(parents=True)
        (self.root / "data/public").mkdir(parents=True)
        (self.root / "data/investments/gpw_daily_pick_config.json").write_text(json.dumps({
            "policy_version": "gpw-base-v1", "minimum_composite_score": 72
        }), encoding="utf-8")
        (self.root / "data/investments/us_daily_stock_config.json").write_text(json.dumps({
            "policy_version": "us-base-v1", "target_score": 72
        }), encoding="utf-8")
        self.state = self.root / "state"
        self.state.mkdir()
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ap.ensure_activation(self.state, self.now - timedelta(days=30))
        self.registry = ap.ensure_registry(self.state, self.root, self.now - timedelta(days=30))
        sg.save_authorizations(self.state / sg.AUTH_FILENAME, sg._load_authorizations(self.state / sg.AUTH_FILENAME))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_challenger(self) -> None:
        candidate = {
            "candidate_id": "pc1",
            "engine_id": "gpw_daily",
            "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score",
            "from_value": 72.0,
            "to_value": 71.0,
            "created_at": ap._iso(self.now - timedelta(days=10)),
            "validation_start_at": ap._iso(self.now - timedelta(days=9)),
            "status": "SHADOW_VALIDATION",
            "training": {"n": 34},
            "validation": {"n": 12},
            "promotion_gate": {"status": "COLLECTING"},
            "statistical_gate": {
                "status": "COLLECTING",
                "blocking_reasons": ["paired_n_below_25"],
                "metrics": {
                    "n": 12,
                    "paired_net_incremental_mean_percent": 0.31,
                    "paired_net_positive_rate": 0.666667,
                    "bootstrap_ci_low_percent": 0.05,
                    "bootstrap_ci_high_percent": 0.55,
                    "cost_stress_percent": 0.2,
                },
            },
        }
        self.registry["candidates"]["pc1"] = candidate
        self.registry["updated_at"] = ap._iso(self.now)
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)
        ap.append_audit(self.state / ap.AUDIT_FILENAME, "candidate_created", candidate, ap._iso(self.now - timedelta(days=10)))

    def test_baseline_values_are_visible_without_private_ids(self) -> None:
        public, private = obs.build(self.state, self.root)
        by_engine = {row["engine"]: row for row in public["engines"]}
        self.assertEqual(by_engine["gpw_daily"]["active"]["value"], 72)
        self.assertEqual(by_engine["us_daily"]["active"]["value"], 72)
        self.assertTrue(by_engine["gpw_daily"]["active"]["statistically_authorized"])
        self.assertNotIn("policy_id", json.dumps(public))
        self.assertIn("registry", private)

    def test_challenger_progress_and_statistics_are_sanitized(self) -> None:
        self._add_challenger()
        public, _ = obs.build(self.state, self.root)
        gpw = next(row for row in public["engines"] if row["engine"] == "gpw_daily")
        challenger = gpw["challenger"]
        self.assertEqual(challenger["from_value"], 72.0)
        self.assertEqual(challenger["to_value"], 71.0)
        self.assertEqual(challenger["progress"]["paired_n"], 12)
        self.assertEqual(challenger["progress"]["required_n"], 25)
        self.assertEqual(challenger["statistical_status"], "COLLECTING")
        self.assertAlmostEqual(challenger["net_incremental_mean_percent"], 0.31)
        self.assertEqual(public["timeline"][-1]["type"], "candidate_created")

    def test_second_publish_without_state_change_does_not_churn_public_file(self) -> None:
        self._add_challenger()
        first = obs.publish(self.state, self.root)
        before = (self.root / obs.PUBLIC_PATH).read_text(encoding="utf-8")
        second = obs.publish(self.state, self.root)
        after = (self.root / obs.PUBLIC_PATH).read_text(encoding="utf-8")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(before, after)
        self.assertTrue((self.state / obs.PRIVATE_PATH).exists())


if __name__ == "__main__":
    unittest.main()
