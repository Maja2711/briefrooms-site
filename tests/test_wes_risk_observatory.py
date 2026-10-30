import unittest
from datetime import datetime, timedelta, timezone
from scripts.wes_risk_observatory import evaluate

NOW = datetime(2026,10,30,12,tzinfo=timezone.utc)
OPENED = (NOW-timedelta(days=1)).isoformat()
def make_week():
    return {"week_id":"2026-W44","instruments":[
        {"instrument_id":"eurusd","trade_status":"open","direction":"short","entry_price":1.12,
         "entry_captured_at":OPENED,"exit_price":None,"risk_plan":{"stop_loss_price":1.13}},
        {"instrument_id":"btcusd","trade_status":"no_trade","wes_trigger_candidate":{
         "direction":"neutral","directional_admission":{"passed":False}},
         "execution_price_engine":{"status":"NO_FILL","reason":"missing_canonical_live_target_contract"}},
        {"instrument_id":"sp500_futures","trade_status":"no_trade"}]}

class ObserverTests(unittest.TestCase):
    def test_fresh_quote_and_prospective_giveback(self):
        quote={"prices":{"eurusd":{"price":1.115,"timestamp":NOW.isoformat(),"source":"test"}}}
        one=evaluate(make_week(),quote,{},NOW)
        self.assertEqual(one["instruments"]["eurusd"]["peak_favorable_R"],0.5)
        quote["prices"]["eurusd"]["price"]=1.118
        two=evaluate(make_week(),quote,one,NOW+timedelta(seconds=30))
        self.assertIn("PROFIT_GIVEBACK_REVIEW_REQUIRED",two["instruments"]["eurusd"]["warnings"])
        self.assertEqual(two["instruments"]["eurusd"]["peak_favorable_R"],0.5)
        self.assertEqual(two["instruments"]["btcusd"]["entry_funnel"]["admission_passed"],False)
        self.assertIn("EPE_LIVE_TARGET_CONTRACT_MISSING",two["instruments"]["btcusd"]["warnings"])

    def test_stale_never_advances_peak(self):
        old=evaluate(make_week(),{"prices":{"eurusd":{"price":1.115,"timestamp":NOW.isoformat()}}},{},NOW)
        stale=evaluate(make_week(),{"prices":{"eurusd":{"price":1.10,"timestamp":NOW.isoformat()}}},old,NOW+timedelta(hours=2))
        self.assertEqual(stale["instruments"]["eurusd"]["state"],"PRICE_UNVERIFIED")
        self.assertEqual(stale["instruments"]["eurusd"]["peak_favorable_R"],0.5)

    def test_no_cross_position_peak_reuse(self):
        quote={"prices":{"eurusd":{"price":1.115,"timestamp":NOW.isoformat()}}}
        old=evaluate(make_week(),quote,{},NOW)
        other=make_week()
        other["instruments"][0]["entry_price"]=1.119
        fresh=evaluate(other,quote,old,NOW)
        self.assertNotEqual(fresh["instruments"]["eurusd"]["position_identity"],
                            old["instruments"]["eurusd"]["position_identity"])
        self.assertAlmostEqual(fresh["instruments"]["eurusd"]["peak_favorable_R"],0.3636, places=4)

if __name__ == "__main__":
    unittest.main()
