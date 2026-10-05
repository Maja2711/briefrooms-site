import json
import tempfile
import unittest
from pathlib import Path

from scripts.l3a_research_executor import new_evidence_ids
from scripts.l3a_experience_lineage import attribution, settle

class TestL3AExecutor(unittest.TestCase):
    def test_diff_attributes_only_new_evidence_for_same_belief(self):
        before={"evidence":[
            {"evidence_id":"old-a","belief_id":"b1"},
            {"evidence_id":"old-b","belief_id":"b2"},
        ]}
        after={"evidence":[
            {"evidence_id":"old-a","belief_id":"b1"},
            {"evidence_id":"new-a","belief_id":"b1"},
            {"evidence_id":"new-b","belief_id":"b2"},
        ]}
        self.assertEqual(new_evidence_ids(before,after,"b1"),["new-a"])

    def test_brier_gain_uses_pre_research_probability_as_counterfactual(self):
        intent={"intent_id":"i1","question_id":"q1","belief_id":"b1"}
        a=attribution(intent,evidence_ids=["e1"],p_before=.55,p_after=.65,forecast_id="f1")
        out=settle(a,{"verification_id":"v1","forecast_id":"f1","outcome":True,"brier_score":.1225,"log_loss":.43})
        self.assertAlmostEqual(out["future_metrics"]["pre_research_counterfactual_brier"],.2025)
        self.assertAlmostEqual(out["future_metrics"]["brier_gain_vs_pre_research"],.08)
        self.assertEqual(out["experience_value_status"],"POSITIVE")

if __name__=="__main__":
    unittest.main()
