"""Strict prequential and authority regression tests for FSE Adaptive Learning v1."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import fractal_structure_engine as v1
from scripts import fractal_structure_engine_v2 as fse


def samples(n=60, spacing_hours=1):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    snapshots, resolutions = [], []
    for i in range(n):
        observed = start + timedelta(hours=i * spacing_hours)
        ended = observed + timedelta(hours=4)
        sid = "adaptive-fixture-%d" % i
        snapshots.append({
            "snapshot_id": sid, "instrument": "BTCUSD",
            "methodology_version": fse.METHODOLOGY_VERSION, "prospective_only": True,
            "observed_at": observed.isoformat(),
            "base_p_up": 0.55 if i % 2 else 0.45,
            "phase_p_up": 0.62 if i % 2 else 0.38,
            "challenger_p_up": 0.58 if i % 2 else 0.42,
            "risk_score": 0.30 + (i % 7) / 15,
        })
        resolutions.append({
            "snapshot_id": sid, "instrument": "BTCUSD",
            "methodology_version": fse.METHODOLOGY_VERSION, "prospective_only": True,
            "outcome_up": bool(i % 2),
            "large_move_event": bool(i % 5 == 0),
            "resolved_at": ended.isoformat(),
        })
    return snapshots, resolutions, start


def current_row(cutoff):
    return {
        "instrument": "BTCUSD", "symbol": "BTC-USD",
        "observed_at": cutoff.isoformat(), "reference_price": 100.0,
        "forecast": "UP",
        "memory": {"p_up": 0.55, "analogues_n": 60,
                   "history_candidates": 120, "fingerprint_dimensions": 62},
        "phase_map": {"1h": {"structure_id": "phase-test", "phase": "EXPANSION",
                             "phase_memory": {"p_up_remaining": 0.70,
                                              "phase_progress": 0.70, "top_similarity": 0.8}}},
        "cross_scale_alignment": {"alignment_score": 0.5, "cascade_state": "MIXED"},
        "structural_risk": {"risk_score": 0.65, "regime": "TRANSITION",
                            "atr_1h_fraction": 0.01},
        "p_calibration_challenger": {"active": True, "p_challenger": 0.62},
    }


class TestFSEAdaptiveLearning(unittest.TestCase):
    def test_ensemble_learns_from_independent_settled_blocks(self):
        snaps, resolutions, start = samples()
        current = current_row(start + timedelta(hours=70))
        result = fse.adaptive_ensemble(current, snaps, resolutions)
        self.assertTrue(result["active"])
        self.assertGreaterEqual(result["training_independent_n"], 12)
        self.assertLess(result["training_independent_n"], 60)  # overlapping horizons
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0)
        self.assertTrue(all(w > 0 for w in result["weights"].values()))
        self.assertLessEqual(abs(result["p_adaptive"] - current["memory"]["p_up"]), 0.06 + 1e-12)
        self.assertLess(v1.parse_time(result["trained_through"]), v1.parse_time(current["observed_at"]))
        self.assertFalse(result["production_applied"])

    def test_future_outcomes_and_current_candle_cannot_train(self):
        snaps, results, start = samples(n=60)
        current = current_row(start + timedelta(hours=70))
        before = fse.adaptive_ensemble(current, snaps, results)
        future = dict(snaps[0], snapshot_id="future", observed_at=(start + timedelta(hours=68)).isoformat())
        future_result = dict(results[0], snapshot_id="future",
                             resolved_at=(start + timedelta(hours=72)).isoformat(),
                             outcome_up=False)
        after = fse.adaptive_ensemble(current, snaps + [future], results + [future_result])
        self.assertEqual(before, after)
        early = fse.adaptive_ensemble(current_row(start + timedelta(hours=15)), snaps, results)
        self.assertFalse(early["active"])

    def test_risk_calibrator_uses_actual_event_rate_not_fifty_fifty(self):
        snaps, results, start = samples(n=60)
        row = current_row(start + timedelta(hours=70))
        risk = fse.adaptive_risk_calibrator(row, snaps, results)
        self.assertTrue(risk["active"])
        self.assertEqual(risk["training_n"], 60)
        self.assertGreaterEqual(risk["training_independent_n"], 12)
        self.assertLess(risk["p_empirical_prior"], 0.5)
        self.assertGreaterEqual(risk["slope"], 0)
        self.assertTrue(0 < risk["p_large_move"] < 1)
        self.assertLess(v1.parse_time(risk["trained_through"]), v1.parse_time(row["observed_at"]))
        self.assertFalse(risk["production_applied"])

    def test_risk_calibrator_stays_inactive_without_new_risk_evidence(self):
        snaps, results, start = samples(n=60)
        for s in snaps:
            s.pop("risk_score")
        row = current_row(start + timedelta(hours=70))
        risk = fse.adaptive_risk_calibrator(row, snaps, results)
        self.assertFalse(risk["active"])
        self.assertIsNone(risk["p_large_move"])

    def test_snapshot_is_immutable_and_resolution_scored_only_if_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observed = datetime(2026, 10, 1, tzinfo=timezone.utc)
            row = current_row(observed)
            row["adaptive_learning"] = {
                "ensemble": {"active": True, "p_adaptive": 0.65,
                              "weights": {"deep": .5, "phase": .3, "regime_phase_challenger": .2},
                              "trained_through": (observed-timedelta(hours=1)).isoformat()},
                "risk": {"active": True, "p_large_move": 0.70, "p_empirical_prior": 0.25,
                         "trained_through": (observed-timedelta(hours=1)).isoformat()}
            }
            fse.snapshot(root, row)
            row["adaptive_learning"]["ensemble"]["p_adaptive"] = 0.01
            fse.snapshot(root, row)
            saved = fse.read_jsonl(root / fse.SNAPSHOTS_FILE)
            self.assertEqual(len(saved), 1)
            self.assertAlmostEqual(saved[0]["adaptive_p_up"], 0.65)
            future = [v1.Bar(observed + timedelta(hours=i), 100.0 + i,
                             100.0, 100.0+i, 100.0, 1) for i in range(1, 6)]
            fse.resolve(root, {"BTCUSD": future})
            res = fse.read_jsonl(root / fse.RESOLUTIONS_FILE)
            self.assertEqual(len(res), 1)
            self.assertAlmostEqual(res[0]["adaptive_brier"], (0.65-1)**2)
            self.assertTrue(res[0]["large_move_event"])
            self.assertAlmostEqual(res[0]["adaptive_risk_prior_brier"], (0.25-1)**2)
            self.assertTrue(fse.verify(root)["zero_authority"])

    def test_partial_hour_never_settles_a_future_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observed = datetime(2026, 10, 1, tzinfo=timezone.utc)
            row = current_row(observed)
            row["adaptive_learning"] = {"ensemble": {"active": False},
                                        "risk": {"active": False}}
            fse.snapshot(root, row)
            future = [v1.Bar(observed + timedelta(hours=i), 100.0 + i,
                             100.0, 100.0 + i, 100.0, 1) for i in range(1, 5)]
            fse.resolve(root, {"BTCUSD": future},
                        as_of=(observed + timedelta(hours=4, minutes=30)).isoformat())
            self.assertEqual(fse.read_jsonl(root / fse.RESOLUTIONS_FILE), [])
            fse.resolve(root, {"BTCUSD": future},
                        as_of=(observed + timedelta(hours=5)).isoformat())
            self.assertEqual(len(fse.read_jsonl(root / fse.RESOLUTIONS_FILE)), 1)

    def test_hse2_gets_two_additive_hypotheses_without_changing_phase_contracts(self):
        rows = [{
            "instrument": "BTCUSD", "methodology_version": fse.METHODOLOGY_VERSION,
            "adaptive_version": fse.ADAPTIVE_VERSION,
            "phase_edge_vs_0_5": 0.01, "calibration_brier_improvement_vs_base": 0.01,
            "adaptive_brier_improvement_vs_deep": 0.015,
            "adaptive_risk_improvement_vs_prior": 0.02,
        }]
        proposals = fse.hse_measurements(rows, "BTCUSD")
        self.assertEqual(len(proposals), 4)
        self.assertEqual([x["counter"] for x in proposals], [1, 1, 1, 1])
        self.assertEqual([x["details"]["methodology_version"] for x in proposals],
                         [fse.METHODOLOGY_VERSION, fse.METHODOLOGY_VERSION,
                          fse.ADAPTIVE_VERSION, fse.ADAPTIVE_VERSION])
        self.assertTrue(all(x["target_n"] == 40 for x in proposals))


if __name__ == "__main__":
    unittest.main()
