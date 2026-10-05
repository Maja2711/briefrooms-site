import unittest
from scripts.l3a_experience_lineage import attribution, forecast_metadata, settle
class TestL3ALineage(unittest.TestCase):
 def test_records_delta_and_future_brier_gain(self):
  i={"intent_id":"i1","question_id":"q1","belief_id":"b1"}
  a=attribution(i,evidence_ids=["e2","e1"],p_before=.55,p_after=.65,forecast_id="f1")
  self.assertEqual(a["delta_p"],.1)
  m=forecast_metadata(i,a); self.assertEqual(m["l3_question_id"],"q1")
  s=settle(a,{"verification_id":"v1","forecast_id":"f1","outcome":True,"brier_score":.1225,"log_loss":.43})
  self.assertAlmostEqual(s["future_metrics"]["pre_research_counterfactual_brier"],.2025)
  self.assertAlmostEqual(s["future_metrics"]["brier_gain_vs_pre_research"],.08)
  self.assertEqual(s["experience_value_status"],"POSITIVE")
if __name__=="__main__": unittest.main()
