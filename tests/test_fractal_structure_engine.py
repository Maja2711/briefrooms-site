import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import fractal_structure_engine as fse


def bars(n=700, minutes=60, drift=0.0003):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    price=100.0
    out=[]
    for i in range(n):
        prev=price
        ret=drift + math.sin(i/7.0)*0.00015 + math.sin(i/31.0)*0.00008
        price*=math.exp(ret)
        out.append(fse.Bar(
            start+timedelta(minutes=minutes*i), price, prev,
            max(prev,price)*1.0015, min(prev,price)*0.9985, 1000.0
        ))
    return out


class FakeClient:
    def bars(self,symbol,range_,interval):
        if interval=="5m": return bars(700,5)
        if interval=="15m": return bars(700,15)
        if interval=="60m": return bars(1200,60)
        if interval=="1d": return bars(700,1440)
        raise AssertionError(interval)


class FSETests(unittest.TestCase):
    def test_multiscale_risk_is_bounded_and_non_authoritative(self):
        h=bars(900,60)
        scales={
            "5m":fse.scale_features(bars(500,5),5),
            "15m":fse.scale_features(bars(500,15),15),
            "1h":fse.scale_features(h,60),
            "4h":fse.scale_features(fse.resample_fixed(h,4),240),
            "1d":fse.scale_features(bars(500,1440),1440),
        }
        risk=fse.structural_risk(scales)
        self.assertGreaterEqual(risk["risk_score"],0.0)
        self.assertLessEqual(risk["risk_score"],1.0)
        self.assertIn(risk["regime"],{"STABLE","TRENDING","MEAN_REVERTING","TRANSITION","TURBULENT"})
        self.assertFalse(risk["research_candidate"]["production_applied"])
        self.assertEqual(len(fse.feature_vector(scales,risk)),22)

    def test_historical_memory_builds_distribution(self):
        out=fse.historical_analogues(bars(1200,60),top_k=30)
        self.assertEqual(out["source"],"HISTORICAL_BOOTSTRAP")
        self.assertEqual(out["analogues_n"],30)
        self.assertGreaterEqual(out["p_up"],0.0)
        self.assertLessEqual(out["p_up"],1.0)
        self.assertIsNotNone(out["top_similarity"])

    def test_resolution_scores_only_future_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            h=bars(40,60)
            observed=h[10].timestamp
            snap={
                "snapshot_id":"fsesnap-test","instrument":"EURUSD","symbol":"EURUSD=X",
                "observed_at":observed.isoformat().replace("+00:00","Z"),
                "reference_price":h[10].close,"risk_score":0.8,"regime":"TURBULENT",
                "p_up_4h":0.7,"analogue_source":"HISTORICAL_BOOTSTRAP","analogue_n":20,
                "atr_1h_fraction":0.002,"feature_vector":[0.1]*22,
                "prospective_only":True,"authority":dict(fse.ZERO_AUTHORITY),
            }
            fse.append_chain(state/fse.SNAPSHOTS_FILE,fse.SNAPSHOT_SCHEMA,snap,"snapshot_id")
            fse.resolve_snapshots(state,{"EURUSD":h})
            rows=fse.read_jsonl(state/fse.RESOLUTIONS_FILE)
            self.assertEqual(len(rows),1)
            self.assertGreater(fse.parse_time(rows[0]["resolved_at"]),observed)
            self.assertAlmostEqual(rows[0]["directional_brier_edge_vs_0_5"],0.25-rows[0]["directional_brier"])
            self.assertTrue(rows[0]["prospective_only"])
            self.assertTrue(fse.verify(state)["zero_authority"])


    def test_directional_performance_scores_frozen_and_excludes_neutral(self):
        snaps=[{"snapshot_id":"a","instrument":"EURUSD","prospective_only":True,"p_up_4h":.7},
               {"snapshot_id":"b","instrument":"EURUSD","prospective_only":True,"p_up_4h":.35},
               {"snapshot_id":"c","instrument":"EURUSD","prospective_only":True,"p_up_4h":.5}]
        results=[{"snapshot_id":"a","instrument":"EURUSD","prospective_only":True,"outcome_up":True},
                 {"snapshot_id":"b","instrument":"EURUSD","prospective_only":True,"outcome_up":True},
                 {"snapshot_id":"c","instrument":"EURUSD","prospective_only":True,"outcome_up":False}]
        score=fse.directional_performance(results,snaps,"EURUSD")
        self.assertEqual((score["resolved_n"],score["signal_n"],score["correct_n"]),(3,2,1))
        self.assertAlmostEqual(score["accuracy"],.5)
        self.assertAlmostEqual(score["mean_brier"],(.09+.4225+.25)/3)

    def test_cycle_exports_hse_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"state"; public=root/"fse.json"
            out=fse.run_cycle(root,state,public,instruments={"EURUSD":"EURUSD=X"},client=FakeClient(),at="2026-09-29T09:00:00Z")
            self.assertEqual(out["schema_version"],fse.PUBLIC_SCHEMA)
            self.assertEqual(out["module_id"],"IN-09")
            self.assertEqual(out["methodology_version"],fse.METHODOLOGY_VERSION)
            self.assertEqual(out["mode"],"SHADOW_ONLY")
            self.assertFalse(out["production_impact"])
            self.assertEqual(out["authority"],fse.ZERO_AUTHORITY)
            self.assertEqual(len(out["instruments"]),1)
            self.assertEqual(len(out["hse_measurements"]),2)
            self.assertEqual(out["directional_performance"][0]["resolved_n"],0)
            self.assertEqual(json.loads(public.read_text())["engine"],"FSE — Fractal Structure Engine")


if __name__=="__main__":
    unittest.main()
