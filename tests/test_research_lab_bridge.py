import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.investments_research_bridge as bridge


class ResearchLabBridgeTest(unittest.TestCase):
    def test_unapproved_candidate_cannot_change_conviction(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'policy.json'; r=Path(td)/'registry.json'
            p.write_text(json.dumps({'enabled':True,'scope':['eurusd'],'governance':{'allowed_runtime_statuses':['approved_for_paper'],'max_runtime_adjustment_points':2}}))
            r.write_text(json.dumps({'candidates':[{'instrument_id':'eurusd','strategy_id':'ema_mean_reversion','status':'research_candidate','runtime_adjustment_points':99}]}))
            with patch.object(bridge,'POLICY',p), patch.object(bridge,'REGISTRY',r):
                out,audit=bridge.apply('eurusd',{'ema_mean_reversion':{'direction':'short','conviction':10}})
            self.assertEqual(out['ema_mean_reversion']['conviction'],10)
            self.assertEqual(audit['applied'],[])

    def test_approved_adjustment_is_clamped(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'policy.json'; r=Path(td)/'registry.json'
            p.write_text(json.dumps({'enabled':True,'scope':['eurusd'],'governance':{'allowed_runtime_statuses':['approved_for_paper'],'max_runtime_adjustment_points':2}}))
            r.write_text(json.dumps({'candidates':[{'candidate_id':'x','instrument_id':'eurusd','strategy_id':'ema_mean_reversion','status':'approved_for_paper','runtime_adjustment_points':99}]}))
            with patch.object(bridge,'POLICY',p), patch.object(bridge,'REGISTRY',r):
                out,audit=bridge.apply('eurusd',{'ema_mean_reversion':{'direction':'short','conviction':10}})
            self.assertEqual(out['ema_mean_reversion']['conviction'],12)
            self.assertEqual(audit['applied'][0]['adjustment'],2)

if __name__=='__main__': unittest.main()
