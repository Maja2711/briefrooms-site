from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import autonomous_policy_promotion as ap
from scripts.policy_repo_materializer import materialize


class PolicyRepoMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data/investments").mkdir(parents=True)
        baselines = {
            "schema_version": "briefrooms-autonomous-policy-baselines-v1",
            "engines": {
                "gpw_daily": {
                    "config_path": "data/investments/gpw_daily_pick_config.json",
                    "baseline_policy_version": "gpw-base",
                    "parameter": "minimum_composite_score",
                    "baseline_value": 72,
                    "minimum_allowed": 68,
                    "maximum_allowed": 76,
                },
                "us_daily": {
                    "config_path": "data/investments/us_daily_stock_config.json",
                    "baseline_policy_version": "us-base",
                    "parameter": "target_score",
                    "baseline_value": 72,
                    "minimum_allowed": 68,
                    "maximum_allowed": 76,
                },
            },
        }
        (self.root / "data/investments/autonomous_policy_baselines.json").write_text(json.dumps(baselines), encoding="utf-8")
        (self.root / "data/investments/gpw_daily_pick_config.json").write_text(json.dumps({"policy_version": "gpw-base", "minimum_composite_score": 72, "other": 9}), encoding="utf-8")
        (self.root / "data/investments/us_daily_stock_config.json").write_text(json.dumps({"policy_version": "us-base", "target_score": 72, "other": 8}), encoding="utf-8")
        self.state = self.root / "state"
        self.state.mkdir()
        registry = {
            "schema_version": ap.REGISTRY_SCHEMA,
            "updated_at": ap._iso(datetime(2026, 9, 1, tzinfo=timezone.utc)),
            "controls": dict(ap.CONTROLS),
            "engines": {
                "gpw_daily": {
                    "engine_id": "gpw_daily", "status": "ACTIVE", "policy_id": "p1", "revision": 1,
                    "baseline_policy_version": "gpw-base", "effective_policy_version": "gpw-base+auto1",
                    "overrides": {"minimum_composite_score": 71.0}, "activated_at": "2026-09-01T00:00:00Z",
                    "source_candidate_id": "c1", "parent": None, "blocked_until": None,
                },
                "us_daily": {
                    "engine_id": "us_daily", "status": "ACTIVE", "policy_id": "u0", "revision": 0,
                    "baseline_policy_version": "us-base", "effective_policy_version": "us-base",
                    "overrides": {}, "activated_at": None, "source_candidate_id": None, "parent": None, "blocked_until": None,
                },
            },
            "candidates": {}, "rejected_transitions": [],
        }
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_materializes_only_allowlisted_threshold_and_version(self) -> None:
        result = materialize(self.state / ap.REGISTRY_FILENAME, self.root)
        self.assertTrue(result["changed"])
        gpw = json.loads((self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        us = json.loads((self.root / "data/investments/us_daily_stock_config.json").read_text())
        self.assertEqual(gpw["minimum_composite_score"], 71)
        self.assertEqual(gpw["policy_version"], "gpw-base+auto1")
        self.assertEqual(gpw["other"], 9)
        self.assertEqual(us["target_score"], 72)
        self.assertEqual(us["other"], 8)

    def test_refuses_manual_policy_version_divergence(self) -> None:
        path = self.root / "data/investments/gpw_daily_pick_config.json"
        payload = json.loads(path.read_text())
        payload["policy_version"] = "manual-v99"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            materialize(self.state / ap.REGISTRY_FILENAME, self.root)


if __name__ == "__main__":
    unittest.main()
