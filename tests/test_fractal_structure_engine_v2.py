import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import fractal_structure_engine as v1
from scripts import fractal_structure_engine_v2 as v2


def bars(n, minutes=60, year=2020, drift=0.0001):
    t = datetime(year, 1, 1, tzinfo=timezone.utc)
    p = 100.0
    out = []
    for i in range(n):
        old = p
        p *= math.exp(drift + math.sin(i / 19) * 0.001 + math.sin(i / 73) * 0.0004)
        out.append(
            v1.Bar(
                t + timedelta(minutes=i * minutes),
                p,
                old,
                max(old, p) * 1.001,
                min(old, p) * 0.999,
                1000,
            )
        )
    return out


class Fake:
    def bars(self, symbol, range_, interval):
        if interval == "1m":
            return bars(1200, 1, 2026)
        if interval == "5m":
            return bars(1000, 5, 2026)
        if interval == "15m":
            return bars(900, 15, 2026)
        if interval == "60m":
            return bars(1800, 60, 2025)
        if interval == "1d":
            return bars(2600, 1440, 2019)
        raise AssertionError((range_, interval))


class TestFSEPhaseEngine(unittest.TestCase):
    def test_geometry_is_rich(self):
        g = v2.geometry_features(bars(900))
        self.assertGreaterEqual(len(g), 18)
        self.assertTrue(all(math.isfinite(x) for x in g))

    def test_phase_memory_estimates_progress_and_probability(self):
        out = v2.phase_analogues(bars(900), top_k=30, max_history=900)
        self.assertEqual(out["source"], "PHASE_MEMORY")
        self.assertGreater(out["analogues_n"], 0)
        self.assertGreaterEqual(out["phase_progress"], 0.40)
        self.assertLessEqual(out["phase_progress"], 0.85)
        self.assertGreaterEqual(out["p_up_remaining"], 0.0)
        self.assertLessEqual(out["p_up_remaining"], 1.0)

    def test_intrabar_and_cross_scale_are_bounded(self):
        scales = {
            "1m": bars(800, 1),
            "5m": bars(500, 5),
            "15m": bars(400, 15),
            "1h": bars(800, 60),
            "4h": v2.resample_minutes(bars(800, 60), 240),
            "1d": bars(700, 1440),
            "1w": v2.resample_weekly(bars(700, 1440)),
        }
        formation = v2.intrabar_formation(scales["1h"], scales["15m"], "1h", "15m")
        self.assertIn("available", formation)
        phase_map = v2.build_phase_map(scales)
        alignment = v2.cross_scale_alignment(phase_map)
        self.assertGreaterEqual(alignment["alignment_score"], 0.0)
        self.assertLessEqual(alignment["alignment_score"], 1.0)
        self.assertIn(alignment["cascade_state"], {"COHERENT", "MIXED", "FRACTURED"})

    def test_calibration_challenger_is_bounded_and_shadow_only(self):
        out = v2.calibration_challenger(0.62, 0.74, 0.8, "TRENDING", True)
        self.assertTrue(out["active"])
        self.assertLessEqual(abs(out["p_challenger"] - out["p_base"]), v2.P_CALIBRATION_MAX_SHIFT + 1e-12)
        self.assertFalse(out["production_applied"])

    def test_deep_memory_searches_many_candidates(self):
        out = v2.deep_historical_analogues(bars(1800, 60, 2025), bars(2600, 1440, 2019), top_k=40)
        self.assertEqual(out["source"], "DEEP_HISTORY")
        self.assertEqual(out["analogues_n"], 40)
        self.assertGreater(out["history_candidates"], 40)
        self.assertGreater(out["fingerprint_dimensions"], 40)
        self.assertGreaterEqual(out["p_up"], 0)
        self.assertLessEqual(out["p_up"], 1)

    def test_new_methodology_produces_prospective_hse_evidence_only_after_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
            v2.append_chain(
                state / v2.SNAPSHOTS_FILE,
                v2.SNAPSHOT_SCHEMA,
                {
                    "snapshot_id": "s1",
                    "instrument": "EURUSD",
                    "symbol": "EURUSD=X",
                    "observed_at": observed.isoformat().replace("+00:00", "Z"),
                    "reference_price": 100.0,
                    "p_up_4h": 0.60,
                    "base_p_up": 0.60,
                    "phase_p_up": 0.70,
                    "challenger_p_up": 0.64,
                    "forecast": "UP",
                    "analogue_n": 40,
                    "history_candidates": 100,
                    "fingerprint_dimensions": 60,
                    "phase_structure_id": "FS-TEST",
                    "phase_label": "EXPANSION",
                    "phase_progress": 0.70,
                    "phase_similarity": 0.80,
                    "cross_scale_alignment": 0.75,
                    "cascade_state": "COHERENT",
                    "regime": "TRENDING",
                    "methodology_version": v2.METHODOLOGY_VERSION,
                    "prospective_only": True,
                    "authority": dict(v2.ZERO_AUTHORITY),
                },
                "snapshot_id",
            )
            future = [
                v1.Bar(observed + timedelta(hours=i), 100.0 + i, 100.0 + i - 1, 100.5 + i, 99.5 + i, 1)
                for i in range(1, 7)
            ]
            v2.resolve(state, {"EURUSD": future})
            rows = v2.read_jsonl(state / v2.RESOLUTIONS_FILE)
            self.assertEqual(len(rows), 1)
            self.assertIsNotNone(rows[0]["phase_brier"])
            self.assertIsNotNone(rows[0]["calibration_brier_improvement_vs_base"])
            hse = v2.hse_measurements(rows, "EURUSD")
            self.assertEqual(hse[0]["counter"], 1)
            self.assertEqual(hse[1]["counter"], 1)


    def test_challenger_performance_uses_only_same_cohort(self):
        m=v2.METHODOLOGY_VERSION
        snaps=[{"snapshot_id":"a","instrument":"EURUSD","prospective_only":True,"methodology_version":m,"base_p_up":.6,"challenger_p_up":.7},
               {"snapshot_id":"b","instrument":"EURUSD","prospective_only":True,"methodology_version":"OLD","base_p_up":.7,"challenger_p_up":.8},
               {"snapshot_id":"c","instrument":"EURUSD","prospective_only":True,"methodology_version":m,"base_p_up":.4,"challenger_p_up":None}]
        results=[{"snapshot_id":"a","instrument":"EURUSD","prospective_only":True,"methodology_version":m,"outcome_up":True},
                 {"snapshot_id":"b","instrument":"EURUSD","prospective_only":True,"methodology_version":"OLD","outcome_up":True},
                 {"snapshot_id":"c","instrument":"EURUSD","prospective_only":True,"methodology_version":m,"outcome_up":False}]
        challenger=v1.directional_performance(results,snaps,"EURUSD",probability_field="challenger_p_up",
            methodology_version=m,paired_base_field="base_p_up")
        self.assertEqual(challenger["resolved_n"],1)
        self.assertEqual(challenger["signal_n"],1)
        self.assertAlmostEqual(challenger["delta_brier_vs_base"],.16-.09)

    def test_shadow_cycle_exposes_full_pipeline_and_zero_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            public = root / "v2.json"
            out = v2.run_cycle(root, state, public, {"EURUSD": "EURUSD=X"}, Fake(), "2026-10-06T05:00:00Z")
            self.assertEqual(out["mode"], "SHADOW_ONLY")
            self.assertFalse(out["production_impact"])
            self.assertEqual(out["authority"], v2.ZERO_AUTHORITY)
            self.assertEqual(out["methodology_version"], v2.METHODOLOGY_VERSION)
            self.assertEqual(out["errors"], {})
            self.assertEqual(len(out["instruments"]), 1)
            row = out["instruments"][0]
            self.assertIn("phase_map", row)
            self.assertIn("intrabar_formation", row)
            self.assertIn("cross_scale_alignment", row)
            self.assertIn("p_calibration_challenger", row)
            self.assertEqual(len(out["hse_measurements"]), 2)
            self.assertEqual(out["directional_performance"][0]["deep"]["resolved_n"],0)
            self.assertEqual(out["directional_performance"][0]["challenger"]["resolved_n"],0)
            self.assertFalse(out["promotion_gate"]["automatic_promotion"])
            self.assertTrue(v2.verify(state)["zero_authority"])


if __name__ == "__main__":
    unittest.main()
