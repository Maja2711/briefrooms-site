import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
                "bar_open_at":observed.isoformat(),
                "frozen_at":(observed+timedelta(minutes=20)).isoformat(),
                "methodology_version":fse.METHODOLOGY_VERSION,
                "prospective_only":True,"authority":dict(fse.ZERO_AUTHORITY),
            }
            fse.append_chain(state/fse.SNAPSHOTS_FILE,fse.SNAPSHOT_SCHEMA,snap,"snapshot_id")
            fse.resolve_snapshots(state,{"EURUSD":h},as_of=h[14].timestamp+timedelta(minutes=59))
            self.assertEqual(fse.read_jsonl(state/fse.RESOLUTIONS_FILE),[])
            fse.resolve_snapshots(state,{"EURUSD":h},as_of=h[15].timestamp)
            rows=fse.read_jsonl(state/fse.RESOLUTIONS_FILE)
            self.assertEqual(len(rows),1)
            self.assertGreater(fse.parse_time(rows[0]["resolved_at"]),observed)
            self.assertAlmostEqual(rows[0]["directional_brier_edge_vs_0_5"],0.25-rows[0]["directional_brier"])
            self.assertTrue(rows[0]["prospective_only"])
            self.assertTrue(fse.verify(state)["zero_authority"])

    def test_zero_probability_stays_zero_and_scores_correctly(self):
        d={tf:bars(240,int(spec["minutes"]),drift=-.0005) for tf,spec in fse.TIMEFRAMES.items()}
        d["4h"]=fse.resample_fixed(d["1h"],4)
        state=fse.instrument_state("EURUSD","EURUSD=X",d,[],{})
        self.assertEqual(state["fractal_memory"]["p_up"],0)
        self.assertEqual(state["p_up_4h"],0)
        self.assertEqual(state["forecast"],"DOWN")
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp); current=bars(80,drift=-.0005)
            state["observed_at"]=state["bar_open_at"]=current[10].timestamp.isoformat()
            state["reference_price"]=current[10].close
            fse.create_snapshot(path,state,frozen_at=(current[10].timestamp+timedelta(minutes=20)).isoformat())
            fse.resolve_snapshots(path,{"EURUSD":current},as_of=current[15].timestamp)
            self.assertEqual(fse.read_jsonl(path/fse.RESOLUTIONS_FILE)[0]["directional_brier"],0)

    def test_legacy_cohort_is_retained_but_not_settled_or_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); legacy={"snapshot_id":"old","instrument":"EURUSD","observed_at":"2026-01-01T00:00:00Z","authority":dict(fse.ZERO_AUTHORITY)}
            fse.append_chain(root/fse.SNAPSHOTS_FILE,fse.SNAPSHOT_SCHEMA,legacy,"snapshot_id")
            before=(root/fse.SNAPSHOTS_FILE).read_bytes()
            fse.resolve_snapshots(root,{"EURUSD":bars(40)},as_of="2026-02-01T00:00:00Z")
            self.assertEqual(fse.read_jsonl(root/fse.RESOLUTIONS_FILE),[])
            self.assertEqual((root/fse.SNAPSHOTS_FILE).read_bytes(),before)
        old_result={"instrument":"EURUSD","directional_brier_edge_vs_0_5":.2,"risk_brier_edge_vs_0_5":.2}
        self.assertTrue(all(m["counter"]==0 for m in fse.measurements([old_result],"EURUSD")))
        self.assertTrue(all(m["details"]["methodology_version"]==fse.METHODOLOGY_VERSION for m in fse.measurements([],"EURUSD")))

    def test_snapshot_identity_uses_version_and_bar_not_live_quote(self):
        d={tf:bars(240,int(spec["minutes"])) for tf,spec in fse.TIMEFRAMES.items()}; d["4h"]=fse.resample_fixed(d["1h"],4)
        state=fse.instrument_state("EURUSD","EURUSD=X",d,[],{})
        capture=fse.parse_time(state["bar_open_at"])+timedelta(minutes=30)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            fse.create_snapshot(path,state,frozen_at=capture.isoformat())
            state["observed_at"]=(capture-timedelta(minutes=1)).isoformat()
            state["p_up_4h"]=.9
            fse.create_snapshot(path,state,frozen_at=(capture+timedelta(minutes=1)).isoformat())
            rows=fse.read_jsonl(path/fse.SNAPSHOTS_FILE)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["frozen_at"],capture.isoformat())
            self.assertEqual(rows[0]["methodology_version"],"FSE-CORE-2.0")

    def test_repeated_hour_publishes_frozen_forecast_and_dates_live_features(self):
        class UpdatingClient(FakeClient):
            update=0
            def bars(self,symbol,range_,interval):
                rows=super().bars(symbol,range_,interval)
                if interval=="60m":
                    last=rows[-1]; price=last.close*(1+self.update*.01)
                    rows[-1]=fse.Bar(last.timestamp,price,last.open,max(last.high,price),min(last.low,price),last.volume,last.timestamp+timedelta(minutes=10+self.update*10))
                return rows
        client=UpdatingClient(); opened=client.bars("EURUSD=X","2y","60m")[-1].timestamp
        with tempfile.TemporaryDirectory() as tmp, patch.object(fse,"historical_analogues",side_effect=[{"source":"HISTORICAL_BOOTSTRAP","analogues_n":20,"p_up":.2},{"source":"HISTORICAL_BOOTSTRAP","analogues_n":20,"p_up":.9}]):
            root=Path(tmp)
            first=fse.run_cycle(root,root/"state",root/"public.json",{"EURUSD":"EURUSD=X"},client,at=(opened+timedelta(minutes=15)).isoformat())
            client.update=1
            second=fse.run_cycle(root,root/"state",root/"public.json",{"EURUSD":"EURUSD=X"},client,at=(opened+timedelta(minutes=25)).isoformat())
            a=first["instruments"][0]; b=second["instruments"][0]
            self.assertEqual(b["fractal_memory"]["p_up_4h"],.2)
            self.assertEqual(b["frozen_forecast"],a["frozen_forecast"])
            self.assertNotEqual(b["reference_price"],b["frozen_forecast"]["reference_price"])
            self.assertEqual(b["latest_source_observed_at"],(opened+timedelta(minutes=20)).isoformat().replace("+00:00","Z"))
            self.assertEqual(b["live_features_observed_at"],b["latest_source_observed_at"])
            self.assertEqual(len(fse.read_jsonl(root/"state"/fse.SNAPSHOTS_FILE)),1)


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
