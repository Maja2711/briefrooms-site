import unittest
from scripts.l3_experience_acquisition import build
class TestL3ExperienceAcquisition(unittest.TestCase):
 def test_closes_intent_loop_without_decision_authority(self):
  q={"mode":"production","contract_version":"belief-question-engine-v1","authority":{"probability_override":False,"trade_execution":False},"questions":[
   {"question_id":"q1","belief_id":"b1","question_type":"resolve_contradiction","question":"What discriminates?","expected_information_value":.9},
   {"question_id":"q2","belief_id":"b2","question_type":"challenge_move","question":"What falsifies?","expected_information_value":.7}]}
  x=build(q,now="2026-10-05T16:00:00Z")
  self.assertEqual(len(x["intents"]),2); self.assertTrue(x["authority"]["automatic_research_routing"])
  self.assertFalse(x["authority"]["belief_probability_override"]); self.assertFalse(x["authority"]["code_mutation"])
  self.assertEqual(x["intents"][0]["route"],"approved_market_evidence_research")
 def test_rejects_unsafe_upstream_authority(self):
  with self.assertRaises(ValueError): build({"mode":"production","authority":{"probability_override":True,"trade_execution":False},"questions":[]})
if __name__=="__main__": unittest.main()
