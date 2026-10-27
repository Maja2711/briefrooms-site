"""Production-contract tests for H1-led EURUSD Daily regime allocation."""
from __future__ import annotations
import unittest
from datetime import datetime,timedelta,timezone
from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_regime_policy as p
from scripts import daily_eurusd_spot_v14 as v14

NOW=datetime(2026,10,26,18,0,tzinfo=timezone.utc)

def bar(t,v,range_pips=8):
    w=range_pips*.00005
    return Bar(timestamp=t,open=v,high=v+w,low=v-w,close=v)

def h1(complete=48,trend=0):
    start=NOW-timedelta(hours=complete)
    out=[]
    for i in range(complete):
        center=1.1000+i*trend
        out.append(bar(start+timedelta(hours=i),center,10))
        out.append(bar(start+timedelta(hours=i,minutes=30),center,10))
    return out

def d1(days=110):
    start=NOW-timedelta(days=days+1)
    return [bar(start+timedelta(days=i),1.10+i*.00003,30)
            for i in range(days) if (start+timedelta(days=i)).weekday()<5]

def ctx(regime="STRONG_TREND",side="LONG",d1trend="UP"):
    bb={"available":True, "allowed":True, "regime":"TREND",
        "range_structure":regime=="RANGE",
        "strong_trend_confirmed":regime=="STRONG_TREND",
        "range_edge_entry":{"allowed":regime=="RANGE"},
        "middle":1.1015,"lower":1.1000,"upper":1.1030}
    db={"available":True,"trend":d1trend,
        "middle_slope":.0002 if d1trend=="UP" else -.0002,
        "percent_b":.8 if d1trend=="UP" else .2,
        "last_completed_d1":(NOW-timedelta(days=1)).isoformat()}
    return bb,db

class MultiTimeframeTests(unittest.TestCase):
    def test_strong_trend_routes_to_h1_primary_with_d1_and_weekly_background(self):
        bb,db=ctx()
        result=p.evaluate(h1(),d1(),bb,db,"LONG",NOW)
        self.assertEqual(result["strategy"],"STRONG_TREND")
        self.assertTrue(result["available"])
        self.assertTrue(result["w1"]["available"])
        self.assertFalse(result["w1"]["veto_authority"])
        self.assertEqual(result["h1_volatility"]["sample_closed_hours"],24)
        self.assertAlmostEqual(result["h1_volatility"]["mean_high_low_pips"],10,places=1)
        self.assertEqual(result["roles"]["H1"],"PRIMARY")

    def test_daily_strong_opposite_direction_vetoes_only_admission(self):
        bb,db=ctx(d1trend="DOWN")
        result=p.evaluate(h1(),d1(),bb,db,"LONG",NOW)
        self.assertEqual(result["strategy"],"WAIT_D1_CONFLICT")
        self.assertTrue(result["block_new_entry"])
        self.assertFalse(result["direction_authority"])
        self.assertEqual(result["direction"],"LONG")

    def test_range_edge_uses_distinct_management_and_early_midpoint_target(self):
        bb,db=ctx(regime="RANGE")
        result=p.evaluate(h1(),d1(),bb,db,"LONG",NOW)
        self.assertEqual(result["strategy"],"RANGE_EDGE")
        risk=p.geometry(result,1.10030,1.09730,1.10570,"LONG",bb)
        self.assertTrue(risk["applied"],risk)
        self.assertLess(risk["stop"],1.10030)
        self.assertGreater(risk["target"],1.10030)
        self.assertLess(risk["target"],bb["upper"])
        self.assertLessEqual(risk["new_risk_pips"],risk["previous_max_risk_pips"])
        self.assertEqual(result["position_management"],"RANGE_H1_REVERSION")

    def test_short_stop_target_preserve_side_and_never_widen_hard_stop(self):
        bb,db=ctx(regime="RANGE",side="SHORT")
        result=p.evaluate(h1(),d1(),bb,db,"SHORT",NOW)
        risk=p.geometry(result,1.10280,1.10580,1.09700,"SHORT",bb)
        self.assertTrue(risk["applied"],risk)
        self.assertGreater(risk["stop"],1.10280)
        self.assertLess(risk["target"],1.10280)
        self.assertLessEqual(risk["new_risk_pips"],risk["previous_max_risk_pips"])

    def test_range_entry_denied_when_midpoint_reward_too_small(self):
        bb,db=ctx(regime="RANGE")
        result=p.evaluate(h1(),d1(),bb,db,"LONG",NOW)
        risk=p.geometry(result,1.10195,1.09895,1.10735,"LONG",bb)
        self.assertFalse(risk["allowed"])
        self.assertEqual(risk["reason"],"range_reward_below_volatility_risk")

    def test_transition_is_no_new_entry_without_changing_native_belief(self):
        bb,db=ctx()
        bb.update(strong_trend_confirmed=False,allowed=False,regime="TRANSITION")
        result=p.evaluate(h1(),d1(),bb,db,"SHORT",NOW)
        self.assertTrue(result["block_new_entry"])
        self.assertEqual(result["strategy"],"WAIT_TRANSITION")
        self.assertEqual(result["direction"],"SHORT")

    def test_partial_hour_not_used_and_stale_source_fails_closed(self):
        bb,db=ctx()
        series=h1()
        a=p.evaluate(series,d1(),bb,db,"LONG",NOW)
        b=p.evaluate(series+[bar(NOW,1.1700,100)],d1(),bb,db,"LONG",NOW)
        self.assertEqual(a,b)
        stale=p.evaluate(h1(complete=48)[:-8],d1(),bb,db,"LONG",NOW)
        self.assertFalse(stale["available"])

    def test_missing_d1_or_w1_does_not_invent_trend(self):
        bb,db=ctx()
        result=p.evaluate(h1(),[],bb,{"available":False},"LONG",NOW)
        self.assertEqual(result["d1"]["trend"],"UNVERIFIED")
        self.assertFalse(result["w1"]["available"])

    def test_frozen_trend_noise_grace_requires_strong_provenance_and_small_move(self):
        bb,db=ctx()
        profile=p.evaluate(h1(),d1(),bb,db,"LONG",NOW)
        opened=NOW-timedelta(minutes=15)
        pos={"direction":"LONG","entry":1.101,"stop":1.099,
             "opened_at":opened.isoformat(),
             "decision_manifest":{"execution_admission":{
                 "allowed":True,"multi_timeframe":profile}}}
        bars=[bar(NOW-timedelta(minutes=k),1.1004) for k in (3,2,1)]
        self.assertTrue(v14._mtf_strong_trend_noise_grace(pos,bars,NOW,-.4))
        self.assertFalse(v14._mtf_strong_trend_noise_grace(pos,bars,NOW,-.61))
        pos["decision_manifest"]={}
        self.assertFalse(v14._mtf_strong_trend_noise_grace(pos,bars,NOW,-.4))

    def test_oct27_145838_short_soft_exit_not_triggered_after_126_seconds(self):
        """Regression: actual 2026-10-27 EURUSD SHORT, -0.4692R at 126 sec.

        Entry-time H1/D1/W1 all agreed SHORT; the old three-contiguous-minute
        gate was impossible to satisfy reliably at this position age.
        Only the early *soft* risk exit may be deferred; hard stops remain.
        """
        now=datetime(2026,10,27,15,0,44,tzinfo=timezone.utc)
        opened=datetime(2026,10,27,14,58,38,tzinfo=timezone.utc)
        mtf={"version":"MTF_H1_REGIME_V1","strategy":"STRONG_TREND",
             "direction":"SHORT","h1_volatility":{
                "mean_high_low_pips":7.692,
                "last_h1_high":1.1155734062194824,
                "last_h1_low":1.1145787239074707}}
        pos={"status":"OPEN", "trade_id":"eurusd:20261027T145838Z:SHORT",
             "direction":"SHORT","opened_at":opened.isoformat(),
             "entry":1.11474,"stop":1.11604,"target":1.11281,
             "decision_manifest":{"execution_admission":{
                 "allowed":True,"multi_timeframe":mtf}}}
        # The recorded Yahoo 1m monitor had sparse/nonuniform recent bars.
        # Two OHLC observations are sufficient only in the first 5 minutes.
        rows=[bar(now-timedelta(minutes=1,seconds=14),1.11505),
              bar(now,1.115200161933899)]
        self.assertTrue(v14._mtf_strong_trend_noise_grace(pos,rows,now,-.4692))
        self.assertIsNone(v14.evaluate_position(pos,rows,now))

    def test_early_strong_trend_grace_never_overrides_hard_risk_or_stale_quotes(self):
        now=datetime(2026,10,27,15,0,44,tzinfo=timezone.utc)
        opened=now-timedelta(seconds=126)
        mtf={"version":"MTF_H1_REGIME_V1","strategy":"STRONG_TREND",
             "direction":"SHORT","h1_volatility":{
                "mean_high_low_pips":7.692,
                "last_h1_high":1.1155734062194824,
                "last_h1_low":1.1145787239074707}}
        pos={"status":"OPEN", "trade_id":"early-short-risk-contract",
             "direction":"SHORT","opened_at":opened.isoformat(),
             "entry":1.11474,"stop":1.11604,"target":1.11281,
             "decision_manifest":{"execution_admission":{
                 "allowed":True,"multi_timeframe":mtf}}}
        recent=[bar(now,1.115200161933899)]
        self.assertFalse(v14._mtf_strong_trend_noise_grace(pos,recent,now,-.61))
        self.assertFalse(v14._mtf_strong_trend_noise_grace(
            pos,[bar(now-timedelta(minutes=3),1.11520)],now,-.4692))
        self.assertFalse(v14._mtf_strong_trend_noise_grace(
            pos,[bar(now,1.11620)],now,-.4692))
        pos["decision_manifest"]={}
        self.assertFalse(v14._mtf_strong_trend_noise_grace(pos,recent,now,-.4692))

    def test_preexisting_trend_geometry_remains_unchanged_without_h1_proof(self):
        self.assertFalse(p.geometry({"available":False},1.1,1.097,1.105,"LONG",{})["applied"])

if __name__=="__main__":
    unittest.main()
