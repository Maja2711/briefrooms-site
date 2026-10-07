import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import fractal_structure_engine as v1
from scripts import fse_intraday_fast as fast


def bars(n=1200, minutes=1):
    t=datetime(2026,10,1,tzinfo=timezone.utc)
    p=100.0
    out=[]
    for i in range(n):
        old=p
        p*=math.exp(0.00002+math.sin(i/17)*0.0005)
        out.append(v1.Bar(t+timedelta(minutes=i*minutes),p,old,max(old,p),min(old,p),1))
    return out


class Fake:
    def bars(self, symbol, range_, interval):
        self.last=(symbol,range_,interval)
        return bars()


ROOT = Path(__file__).resolve().parents[1]


class TestFSEIntradayFast(unittest.TestCase):
    def test_builds_fast_projection_without_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"data/investments").mkdir(parents=True)
            (root/"data/investments/fse_v2_public.json").write_text(json.dumps({
                "instruments":[{
                    "instrument":"EURUSD",
                    "phase_map":{
                        "4h":{"available":True,"direction":"DOWN","phase":"TRANSITION","confidence":0.4,"efficiency":0.3,"volatility_ratio":1.1,"curvature":0.1},
                        "1d":{"available":True,"direction":"FLAT","phase":"DEVELOPMENT","confidence":0.3,"efficiency":0.2,"volatility_ratio":1.0,"curvature":0.0},
                        "1w":{"available":True,"direction":"DOWN","phase":"CONSOLIDATION","confidence":0.3,"efficiency":0.2,"volatility_ratio":0.9,"curvature":0.0}
                    }
                }]
            }),encoding="utf-8")
            out=fast.run(root,root/"data/investments/fse_intraday_public.json",Fake(),"2026-10-06T11:20:00Z")
            self.assertEqual(out["mode"],"SHADOW_ONLY")
            self.assertFalse(out["production_impact"])
            self.assertTrue(all(v is False for v in out["authority"].values()))
            self.assertEqual(out["cadence_minutes"],15)
            row=out["instruments"][0]
            for tf in ("1m","5m","15m","1h"):
                self.assertTrue(row["phase_map"][tf]["fast_refreshed"])
                self.assertIn(row["phase_map"][tf]["direction"],{"UP","DOWN","FLAT"})
            self.assertFalse(row["phase_map"]["4h"]["fast_refreshed"])
            self.assertIn("alignment_score",row["cross_scale_alignment"])

    def test_fast_workflow_is_independent_from_wes(self):
        fse_workflow=(ROOT/".github/workflows/fse-intraday-fast.yml").read_text(encoding="utf-8")
        weekly=(ROOT/".github/workflows/weekly-live-prices.yml").read_text(encoding="utf-8")
        self.assertIn('name: FSE Intraday Fast Shadow',fse_workflow)
        self.assertIn('cron: "2,17,32,47 * * * *"',fse_workflow)
        self.assertIn('cancel-in-progress: false',fse_workflow)
        self.assertIn("if: github.event_name != 'schedule'",fse_workflow)
        self.assertIn("needs.test.result == 'skipped'",fse_workflow)
        self.assertIn('data/investments/fse_intraday_public.json',fse_workflow)
        self.assertNotIn('fse_intraday_fast.py',weekly)
        self.assertNotIn('fse_intraday_public.json',weekly)

    def test_output_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"data/investments").mkdir(parents=True)
            p=root/"data/investments/fse_intraday_public.json"
            fast.run(root,p,Fake(),"2026-10-06T11:20:00Z")
            data=json.loads(p.read_text())
            self.assertEqual(data["schema_version"],fast.SCHEMA)
            self.assertEqual(data["stale_after_minutes"],25)


if __name__=="__main__":
    unittest.main()
