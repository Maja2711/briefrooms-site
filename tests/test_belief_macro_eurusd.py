from __future__ import annotations
import sys, unittest
from datetime import datetime, timezone
from pathlib import Path
SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0,str(SCRIPTS))
from belief_macro_calendar_adapter import parse_eu_calendar_text
from belief_macro_release_adapter import MacroRelease, release_evidence
UTC=timezone.utc

class MacroEURUSDContractTest(unittest.TestCase):
    def test_eurostat_calendar_promotes_high_impact_eu_event(self):
        now=datetime(2026,9,30,10,tzinfo=UTC)
        text="<html><body>2026-10-01 Euro area HICP inflation flash estimate 2026-10-05 Retail trade</body></html>"
        rows=parse_eu_calendar_text(text,now=now,source="Eurostat",source_ref="test://eurostat")
        self.assertTrue(rows)
        self.assertEqual(rows[0].metadata["region"],"EU")
        self.assertEqual(rows[0].importance,"high")

    def test_us_hot_inflation_is_eurusd_headwind(self):
        row=MacroRelease("US","core_pce_yoy","2026-08",3.1,2.9,2.9,None,"percent","BEA","test://bea",datetime(2026,9,30,12,30,tzinfo=UTC))
        _,_,ev=release_evidence(row)
        self.assertEqual(ev.belief_id,"eurusd.macro_surprise.supportive")
        self.assertEqual(ev.direction,-1)
        self.assertAlmostEqual(ev.metadata["surprise"],.2)

    def test_eu_hot_inflation_is_eurusd_tailwind(self):
        row=MacroRelease("EU","hicp_yoy","2026-09",2.4,2.1,2.2,None,"percent","Eurostat","test://estat",datetime(2026,9,30,9,tzinfo=UTC))
        _,_,ev=release_evidence(row)
        self.assertEqual(ev.direction,1)

    def test_missing_consensus_uses_actual_previous_without_fake_surprise(self):
        row=MacroRelease("US","gdp_qoq","2026-Q2",2.0,2.4,None,-.1,"percent","BEA","test://bea",datetime(2026,9,30,12,30,tzinfo=UTC))
        p,_,ev=release_evidence(row)
        self.assertIsNone(p.metadata["consensus"])
        self.assertIsNone(p.metadata["surprise"])
        self.assertTrue(ev.metadata["previous"] is not None)

if __name__=="__main__": unittest.main()
