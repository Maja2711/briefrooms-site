import math, tempfile, unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from scripts import fractal_structure_engine as v1
from scripts import fractal_structure_engine_v2 as v2

def bars(n,minutes=60,year=2000):
    t=datetime(year,1,1,tzinfo=timezone.utc); p=100.; out=[]
    for i in range(n):
        old=p; p*=math.exp(.0001+math.sin(i/19)*.001+math.sin(i/73)*.0004)
        out.append(v1.Bar(t+timedelta(minutes=i*minutes),p,old,max(old,p)*1.001,min(old,p)*.999,1000))
    return out

class Fake:
    def bars(self,symbol,range_,interval):
        if interval=="60m": return bars(1800,60)
        if interval=="1d": return bars(6000,1440,1990)
        raise AssertionError((range_,interval))

class TestFSEv2(unittest.TestCase):
    def test_geometry_is_rich(self):
        g=v2.geometry_features(bars(900))
        self.assertGreaterEqual(len(g),18)
        self.assertTrue(all(math.isfinite(x) for x in g))
    def test_deep_memory_searches_many_candidates(self):
        out=v2.deep_historical_analogues(bars(1800),bars(6000,1440,1990),top_k=40)
        self.assertEqual(out["source"],"DEEP_HISTORY")
        self.assertEqual(out["analogues_n"],40)
        self.assertGreater(out["history_candidates"],40)
        self.assertGreater(out["fingerprint_dimensions"],40)
        self.assertGreaterEqual(out["p_up"],0); self.assertLessEqual(out["p_up"],1)
    def test_shadow_cycle_zero_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"state"; public=root/"v2.json"
            out=v2.run_cycle(root,state,public,{"EURUSD":"EURUSD=X"},Fake(),"2026-10-03T20:00:00Z")
            self.assertEqual(out["mode"],"SHADOW_ONLY")
            self.assertFalse(out["production_impact"])
            self.assertEqual(out["authority"],v2.ZERO_AUTHORITY)
            self.assertEqual(out["errors"],{})
            self.assertEqual(len(out["instruments"]),1)
            self.assertTrue(v2.verify(state)["zero_authority"])
if __name__=="__main__": unittest.main()
