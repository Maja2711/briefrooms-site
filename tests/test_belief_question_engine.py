import unittest
from scripts.belief_question_engine import build

class TestBeliefQuestionEngine(unittest.TestCase):
    def test_high_contradiction_is_prioritized_with_safe_production_boundaries(self):
        e={"contract_version":"belief-epistemic-state-v1","states":{
          "a":{"state_id":"1","topic":"SPX bullish","probability":.55,"confidence":.45,"contradiction":.8,"freshness":.9,
               "drilldown_reasons":["low_confidence","high_contradiction"],"dominant_support_evidence_ids":["s"],"dominant_opposition_evidence_ids":["o"]},
          "b":{"state_id":"2","topic":"volatility benign","probability":.8,"confidence":.8,"contradiction":.1,"freshness":.95,"drilldown_reasons":[]}
        }}
        out=build(e)
        self.assertEqual(out["selected_count"],1)
        self.assertEqual(out["questions"][0]["question_type"],"resolve_contradiction")
        self.assertEqual(out["mode"],"production")\n        self.assertTrue(out["authority"]["automatic_research"])\n        self.assertFalse(out["authority"]["belief_core_writeback"])
        self.assertFalse(out["authority"]["trade_execution"])

    def test_stable_ids(self):
        e={"states":{"a":{"state_id":"x","topic":"A","probability":.5,"confidence":.2,"contradiction":.1,"freshness":.5,"drilldown_reasons":["low_confidence"]}}}
        self.assertEqual(build(e)["questions"][0]["question_id"],build(e)["questions"][0]["question_id"])

if __name__=="__main__": unittest.main()
