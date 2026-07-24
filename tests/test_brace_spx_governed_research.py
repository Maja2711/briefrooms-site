import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("governed", SCRIPTS / "brace_spx_governed_research.py")
governed = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(governed)


class GovernedResearchTests(unittest.TestCase):
    def _experiment(self, candidate_id: str, objective: float, fold_values):
        return {
            "candidate_id": candidate_id,
            "walk_forward": {
                "objective": objective,
                "fold_metrics": [
                    {
                        "sharpe_zero_rf": sharpe,
                        "objective_advantage": advantage,
                    }
                    for sharpe, advantage in fold_values
                ],
            },
        }

    def test_fold_dsr_proxy_is_bounded(self):
        experiments = [
            self._experiment("a", 1.0, [(1.2, 0.2), (1.0, 0.1), (1.1, 0.2), (0.9, 0.1)]),
            self._experiment("b", 0.8, [(0.8, 0.1), (0.7, 0.0), (0.9, 0.1), (0.6, -0.1)]),
            self._experiment("c", 0.5, [(0.4, -0.1), (0.5, 0.0), (0.3, -0.2), (0.6, 0.0)]),
        ]
        result = governed.fold_dsr_proxy(experiments)
        self.assertTrue(result["available"])
        self.assertGreaterEqual(result["probability_skill_after_selection"], 0.0)
        self.assertLessEqual(result["probability_skill_after_selection"], 1.0)
        self.assertEqual(result["trials"], 3)

    def test_pbo_proxy_detects_unstable_champion(self):
        experiments = [
            self._experiment("a", 1.0, [(1.2, -0.5), (1.0, -0.4), (1.1, 0.6), (0.9, 0.5)]),
            self._experiment("b", 0.8, [(0.8, 0.2), (0.7, 0.3), (0.9, 0.1), (0.6, 0.0)]),
            self._experiment("c", 0.5, [(0.4, 0.1), (0.5, 0.2), (0.3, -0.1), (0.6, -0.2)]),
        ]
        result = governed.pbo_rank_proxy(experiments)
        self.assertTrue(result["available"])
        self.assertEqual(result["folds"], 4)
        self.assertGreater(result["pbo_proxy"], 0.0)

    def test_release_gate_requires_all_predeclared_conditions(self):
        robust = {"passed": True}
        dsr = {"available": True, "probability_skill_after_selection": 0.96}
        pbo = {"available": True, "pbo_proxy": 0.10}
        self.assertTrue(governed._release_allowed(robust, dsr, pbo))
        self.assertFalse(governed._release_allowed({"passed": False}, dsr, pbo))
        self.assertFalse(governed._release_allowed(robust, {**dsr, "probability_skill_after_selection": 0.94}, pbo))
        self.assertFalse(governed._release_allowed(robust, dsr, {**pbo, "pbo_proxy": 0.21}))

    def test_generation_id_is_deterministic(self):
        index = pd.date_range("2000-01-31", periods=180, freq="ME")
        frame = pd.DataFrame({"x": np.arange(len(index))}, index=index)
        first = governed.generation_id(frame, 270)
        second = governed.generation_id(frame.copy(), 270)
        self.assertEqual(first, second)
        self.assertNotEqual(first, governed.generation_id(frame, 271))

    def test_registry_payload_can_record_single_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            payload = {
                "schema_version": "1.0.0",
                "generations": {
                    "g": {"holdout_accessed": True, "holdout_access_count": 1}
                },
            }
            governed._write(path, payload)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(loaded["generations"]["g"]["holdout_accessed"])
            self.assertEqual(loaded["generations"]["g"]["holdout_access_count"], 1)


if __name__ == "__main__":
    unittest.main()
