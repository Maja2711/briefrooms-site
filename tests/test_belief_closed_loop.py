from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_closed_loop import prospective_evaluation, run, transform_probability
from belief_core import BeliefCore, BeliefDefinition


class BeliefClosedLoopTests(unittest.TestCase):
    def test_transform_is_versioned_probability_overlay(self):
        self.assertAlmostEqual(
            transform_probability(.80, {"type":"logit_affine_v1","intercept":0.0,"slope":1.0}),
            .80,
            places=6,
        )
        self.assertAlmostEqual(
            transform_probability(.80, {"type":"logit_affine_v1","intercept":0.0,"slope":0.0}),
            .50,
            places=6,
        )

    def test_prospective_gate_requires_real_future_sample_and_stability(self):
        frozen = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for i in range(60):
            rows.append({
                "forecast_at": (frozen + timedelta(hours=i+1)).isoformat().replace("+00:00","Z"),
                "raw_probability": .90,
                "production_probability": .90,
                "outcome": i % 2,
            })
        result = prospective_evaluation(
            rows,
            frozen.isoformat().replace("+00:00","Z"),
            {"type":"logit_affine_v1","intercept":0.0,"slope":0.0},
        )
        self.assertEqual(result["n"], 60)
        self.assertTrue(result["pass"])
        self.assertGreaterEqual(result["stability"]["improved"], 3)
        self.assertGreater(result["brier_relative_improvement"], .05)

    def test_run_can_promote_only_after_frozen_prospective_challenger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            frozen = datetime(2026, 1, 1, tzinfo=timezone.utc)
            forecasts, verifications = [], []
            for i in range(60):
                at = frozen + timedelta(hours=i+1)
                fid = f"f{i}"
                forecasts.append({
                    "forecast_id": fid,
                    "belief_id": "x.test",
                    "predicted_probability": .90,
                    "forecast_at": at.isoformat().replace("+00:00","Z"),
                    "target_at": (at+timedelta(hours=1)).isoformat().replace("+00:00","Z"),
                    "metadata": {"raw_probability": .90},
                })
                verifications.append({
                    "verification_id": f"v{i}",
                    "forecast_id": fid,
                    "belief_id": "x.test",
                    "predicted_probability": .90,
                    "outcome": bool(i % 2),
                    "forecast_at": at.isoformat().replace("+00:00","Z"),
                    "target_at": (at+timedelta(hours=1)).isoformat().replace("+00:00","Z"),
                    "verified_at": (at+timedelta(hours=1)).isoformat().replace("+00:00","Z"),
                    "calibration_eligible": True,
                })
            (state_dir/"state.json").write_text(json.dumps({
                "definitions":[{"belief_id":"x.test","claim":"test","tags":[]}],
                "forecasts":forecasts,
                "verifications":verifications,
            }))
            (state_dir/"BELIEF_CLOSED_LOOP.json").write_text(json.dumps({
                "challengers":{
                    "x.test":{
                        "belief_id":"x.test",
                        "status":"prospective_shadow",
                        "frozen_at":frozen.isoformat().replace("+00:00","Z"),
                        "transform":{"type":"logit_affine_v1","intercept":0.0,"slope":0.0},
                    }
                }
            }))
            report = root/"report.json"
            report.write_text(json.dumps({"belief_calibration":{"hypothesis_intelligence":{}}}))
            policy = root/"policy.json"
            policy.write_text(json.dumps({
                "schema_version":"belief-core-production-overrides-v1",
                "updated_at":None,
                "authority":{},
                "overrides":{},
                "history":[],
            }))
            output = root/"loop.json"
            payload = run(state_dir, report, policy, output)
            self.assertIn("x.test", payload["active_production_overrides"])
            saved = json.loads(policy.read_text())
            self.assertTrue(saved["overrides"]["x.test"]["active"])
            self.assertEqual(saved["history"][-1]["event"], "AUTO_PROMOTION")

    def test_global_challenger_starts_from_pooled_v2_history_but_does_not_promote_retroactively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            start = datetime(2025, 1, 1, tzinfo=timezone.utc)
            forecasts, verifications = [], []
            for i in range(120):
                at = start + timedelta(hours=i)
                fid = f"g{i}"
                forecasts.append({
                    "forecast_id": fid,
                    "belief_id": "x.test",
                    "predicted_probability": .90,
                    "forecast_at": at.isoformat().replace("+00:00","Z"),
                    "target_at": (at+timedelta(hours=1)).isoformat().replace("+00:00","Z"),
                    "metadata": {},
                })
                verifications.append({
                    "verification_id": f"gv{i}",
                    "forecast_id": fid,
                    "belief_id": "x.test",
                    "predicted_probability": .90,
                    "outcome": bool(i % 2),
                    "forecast_at": at.isoformat().replace("+00:00","Z"),
                    "target_at": (at+timedelta(hours=1)).isoformat().replace("+00:00","Z"),
                    "verified_at": (at+timedelta(hours=1)).isoformat().replace("+00:00","Z"),
                    "calibration_eligible": True,
                })
            (state_dir/"state.json").write_text(json.dumps({
                "definitions":[{"belief_id":"x.test","claim":"test","tags":[]}],
                "forecasts":forecasts,
                "verifications":verifications,
            }))
            report = root/"report.json"
            report.write_text(json.dumps({"belief_calibration":{"hypothesis_intelligence":{}}}))
            policy = root/"policy.json"
            policy.write_text(json.dumps({
                "schema_version":"belief-core-production-overrides-v1",
                "updated_at":None,"authority":{},"overrides":{},"history":[],
            }))
            output = root/"loop.json"
            payload = run(state_dir, report, policy, output)
            self.assertIn("__GLOBAL__", payload["challengers"])
            self.assertEqual(payload["challengers"]["__GLOBAL__"]["status"], "prospective_shadow")
            self.assertEqual(payload["challengers"]["__GLOBAL__"]["prospective"]["n"], 0)
            self.assertEqual(payload["active_production_overrides"], [])

    def test_forecast_keeps_raw_control_when_overlay_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = BeliefCore(tmp)
            core.register_beliefs([BeliefDefinition("x.test","test",prior_probability=.60)])
            when = datetime(2026,1,1,tzinfo=timezone.utc)
            core.recompute(when)
            snap = core.capture_forecast(
                "x.test",
                as_of=when,
                target_at=when+timedelta(hours=1),
                predicted_probability_override=.55,
                metadata={"production_overlay_version":"test-v1"},
            )
            self.assertAlmostEqual(snap.predicted_probability,.55)
            self.assertAlmostEqual(snap.metadata["raw_probability"],.60)
            self.assertAlmostEqual(snap.metadata["production_probability"],.55)
            self.assertTrue(snap.metadata["production_overlay_applied"])


if __name__ == "__main__":
    unittest.main()
