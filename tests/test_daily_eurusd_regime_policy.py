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
    def _reversal_facts(self):
        stamp = (NOW - timedelta(hours=1)).isoformat()
        d1 = {
            "available": True, "trend": "DOWN", "near_lower_band": True,
            "near_upper_band": False, "percent_b": -0.11, "close": 1.10249,
            "lower": 1.10544, "upper": 1.13173,
            "last_completed_d1": (NOW - timedelta(days=1)).isoformat(),
        }
        long_h1 = {
            "available": True, "allowed": False, "reason": "h1_trend_not_confirmed",
            "exhaustion": False, "last_closed_h1": stamp, "percent_b": .23,
        }
        short_h1 = {
            "available": True, "allowed": False, "reason": "outer_band_exhaustion",
            "exhaustion": True, "last_closed_h1": stamp, "percent_b": .23,
        }
        return d1, long_h1, short_h1

    def test_d1_oversold_checks_long_even_when_native_belief_short(self):
        d1, long_h1, short_h1 = self._reversal_facts()
        review = p.review_band_reversal("SHORT", long_h1, short_h1, d1, NOW)
        self.assertEqual(review["hypothesis"], "LONG")
        self.assertEqual(review["status"], "H1_EXHAUSTION_BUT_REVERSAL_UNCONFIRMED")
        self.assertFalse(review["native_belief_supports_hypothesis"])
        self.assertFalse(review["h1_reversal_confirmed"])
        for authority in ("direction_authority", "admission_authority", "execution_authority", "automatic_promotion"):
            self.assertFalse(review[authority])

    def test_verified_h1_opposing_reversal_never_mutates_belief_or_trades(self):
        d1, long_h1, short_h1 = self._reversal_facts()
        long_h1.update(allowed=True, reason="aligned_h1_trend")
        review = p.review_band_reversal("SHORT", long_h1, short_h1, d1, NOW)
        self.assertEqual(review["status"], "H1_REVERSAL_CONFIRMED_RECHECK_BELIEF")
        self.assertEqual(review["native_belief_direction"], "SHORT")
        self.assertEqual(review["hypothesis"], "LONG")
        self.assertTrue(review["requires_new_belief_decision"])
        self.assertFalse(review["execution_authority"])

    def test_reversal_does_not_infer_long_on_unverified_or_nonextended_d1(self):
        d1, long_h1, short_h1 = self._reversal_facts()
        self.assertEqual(p.review_band_reversal("SHORT", {}, short_h1, d1, NOW)["status"], "UNVERIFIED")
        d1["near_lower_band"] = False
        review = p.review_band_reversal("SHORT", long_h1, short_h1, d1, NOW)
        self.assertEqual(review["status"], "NO_D1_BAND_EXTENSION")
        self.assertIsNone(review["hypothesis"])

    def test_countertrend_review_refuses_mismatched_or_future_candle_provenance(self):
        d1, long_h1, short_h1 = self._reversal_facts()
        long_h1["last_closed_h1"] = (NOW + timedelta(minutes=1)).isoformat()
        review = p.review_band_reversal("SHORT", long_h1, short_h1, d1, NOW)
        self.assertEqual(review["status"], "UNVERIFIED")
        self.assertEqual(review["reason"], "inconsistent_or_future_h1_d1_timestamps")

    def test_uptrend_upper_band_symmetrically_checks_short_reversal(self):
        d1, long_h1, short_h1 = self._reversal_facts()
        d1.update(trend="UP", near_lower_band=False, near_upper_band=True)
        long_h1.update(exhaustion=True)
        review = p.review_band_reversal("LONG", long_h1, short_h1, d1, NOW)
        self.assertEqual(review["hypothesis"], "SHORT")
        self.assertEqual(review["status"], "H1_EXHAUSTION_BUT_REVERSAL_UNCONFIRMED")

    def _multi_facts(self, direction="SHORT"):
        score = 42.5 if direction == "SHORT" else 57.5
        h1 = {
            "available": True, "allowed": True, "percent_b": .23 if direction == "SHORT" else .77,
            "raw_h1_price_action": {
                "source": "COMPLETED_H1_RAW_CLOSES", "direction": direction,
                "directional_3h_pips": 4.0, "aligned_intervals_3h": 2,
            },
        }
        d1ctx = {
            "available": True, "trend": "DOWN" if direction == "SHORT" else "UP",
            "raw_d1_price_action": {
                "source": "COMPLETED_D1_RAW_CLOSES",
                "change_5_completed_intervals_pips": -20.0 if direction == "SHORT" else 20.0,
            },
        }
        mtf = {"strategy": "STRONG_TREND", "w1": {
            "available": True, "trend": d1ctx["trend"],
        }}
        plan = {"allowed": True}
        priority = {"active": True, "entry_location_allowed": False}
        final = {
            "score": score, "confidence": .40, "coverage_weight": .67,
            "used_beliefs": [{"belief_id": str(i)} for i in range(3)],
        }
        entry = 1.1000
        stop, target = (1.1020, 1.0960) if direction == "SHORT" else (1.0980, 1.1040)
        return final,h1,d1ctx,mtf,plan,priority,{},direction,entry,stop,target,NOW

    def test_entry_review_requires_diverse_non_bollinger_factors_not_only_band(self):
        facts = self._multi_facts("SHORT")
        review = p.multifactor_entry_review(*facts)
        self.assertEqual(review["non_band_passed"], 4)
        self.assertTrue(review["enforce"])
        self.assertEqual(review["status"], "MULTIFACTOR_CONFIRMED")
        self.assertFalse(review["bollinger_vote_in_non_band_quorum"])
        self.assertFalse(review["factors"]["BOLLINGER_H1_LOCATION"]["confirmed"])
        self.assertFalse(review["direction_authority"])

    def test_entry_review_blocks_when_only_belief_and_geometry_confirm(self):
        facts = self._multi_facts("SHORT")
        facts[1]["raw_h1_price_action"]["directional_3h_pips"] = -4.0
        facts[2]["raw_d1_price_action"]["change_5_completed_intervals_pips"] = 20.0
        review = p.multifactor_entry_review(*facts)
        self.assertEqual(review["non_band_passed"], 2)
        self.assertEqual(review["status"], "WAIT_MULTIFACTOR")
        self.assertTrue(review["enforce"])

    def test_multifactor_is_symmetric_for_long_and_does_not_guess_missing_evidence(self):
        facts = self._multi_facts("LONG")
        self.assertEqual(p.multifactor_entry_review(*facts)["status"], "MULTIFACTOR_CONFIRMED")
        facts[1]["raw_h1_price_action"] = {}
        review = p.multifactor_entry_review(*facts)
        self.assertTrue(review["enforce"])
        self.assertEqual(review["status"], "MULTIFACTOR_EVIDENCE_UNVERIFIED")

    def test_non_finite_multifactor_metrics_are_unverified_not_nan_metadata(self):
        facts = self._multi_facts()
        facts[0]["confidence"] = float("nan")
        facts[1]["raw_h1_price_action"]["directional_3h_pips"] = float("inf")
        review = p.multifactor_entry_review(*facts)
        self.assertIsNone(review["factors"]["BELIEF_QUALITY"]["confidence"])
        self.assertIsNone(review["factors"]["H1_RAW_PRICE_ACTION"]["directional_3h_pips"])
        self.assertTrue(review["enforce"])
        self.assertEqual(review["status"], "MULTIFACTOR_EVIDENCE_UNVERIFIED")

    def test_multifactor_uses_raw_completed_d1_not_d1_bollinger_trend(self):
        facts = self._multi_facts("SHORT")
        facts[2]["trend"] = "DOWN"
        facts[2]["raw_d1_price_action"]["change_5_completed_intervals_pips"] = 3.0
        result = p.multifactor_entry_review(*facts)
        self.assertFalse(result["factors"]["HIGHER_TIMEFRAME"]["confirmed"])
        self.assertTrue(result["factors"]["HIGHER_TIMEFRAME"]["d1_bollinger_trend_not_a_vote"])

    def test_completed_d1_raw_momentum_has_source_independent_of_band_geometry(self):
        from scripts import daily_eurusd_bollinger_d1 as d1_module
        series = d1(days=110)
        result = d1_module.evaluate(series, NOW)
        self.assertTrue(result["available"])
        self.assertEqual(result["raw_d1_price_action"]["source"], "COMPLETED_D1_RAW_CLOSES")
        self.assertGreater(result["raw_d1_price_action"]["change_5_completed_intervals_pips"], 0)

    def test_raw_completed_h1_price_impulse_is_not_bollinger_midline_slope(self):
        from scripts import daily_eurusd_bollinger_h1 as bh
        series = h1(complete=44, trend=-.00020)
        outcome = bh.evaluate(series, "SHORT", NOW)
        self.assertTrue(outcome["available"])
        raw = outcome["raw_h1_price_action"]
        self.assertEqual(raw["source"], "COMPLETED_H1_RAW_CLOSES")
        self.assertEqual(raw["direction"], "SHORT")
        self.assertGreater(raw["directional_3h_pips"], 0)
        self.assertEqual(raw["aligned_intervals_3h"], 3)

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

    def _strong_h1_priority_facts(self, direction="SHORT", position=.25):
        h1_at = (NOW-timedelta(hours=1)).isoformat()
        d1_at = (NOW-timedelta(days=1)).isoformat()
        h1_bb = {
            "available": True, "allowed": True, "strong_trend_confirmed": True,
            "exhaustion": False, "range_regime": False,
            "percent_b": position, "last_closed_h1": h1_at,
        }
        mtf = {
            "available": True, "strategy": "STRONG_TREND", "block_new_entry": False,
            "direction": direction,
            "h1_volatility": {"last_completed_h1": h1_at},
        }
        d1_ctx = {
            "available": True, "trend": "UP" if direction == "LONG" else "DOWN",
            "last_completed_d1": d1_at,
        }
        touched = {"available": True, "last_completed_d1": d1_at,
                   "upper_long_blocked": direction == "LONG",
                   "lower_short_blocked": direction == "SHORT"}
        return h1_bb, mtf, d1_ctx, touched

    def test_strong_short_h1_replaces_old_d1_cooldown_after_hourly_pullback(self):
        facts = self._strong_h1_priority_facts("SHORT", .24)
        result = p.strong_h1_entry_priority(*facts, "SHORT", NOW)
        self.assertEqual(result["status"], "H1_PRIMARY_READY")
        self.assertTrue(result["replaces_d1_touch_veto"])
        self.assertTrue(result["entry_location_allowed"])
        self.assertFalse(result["direction_authority"])
        self.assertFalse(result["execution_authority"])
        self.assertFalse(result["d1_touch_state_changed"])

    def test_short_below_hourly_lower_band_waits_for_hourly_not_daily_pullback(self):
        facts = self._strong_h1_priority_facts("SHORT", -.0153)
        result = p.strong_h1_entry_priority(*facts, "SHORT", NOW)
        self.assertEqual(result["status"], "H1_PRIMARY_WAIT_PULLBACK")
        self.assertTrue(result["replaces_d1_touch_veto"])
        self.assertFalse(result["entry_location_allowed"])
        self.assertEqual(result["short_min_percent_b"], .12)

    def test_strong_long_h1_priority_is_symmetric_and_never_chases_upper_band(self):
        facts = self._strong_h1_priority_facts("LONG", .72)
        result = p.strong_h1_entry_priority(*facts, "LONG", NOW)
        self.assertEqual(result["status"], "H1_PRIMARY_READY")
        facts[0]["percent_b"] = 1.03
        result = p.strong_h1_entry_priority(*facts, "LONG", NOW)
        self.assertEqual(result["status"], "H1_PRIMARY_WAIT_PULLBACK")

    def test_weak_or_opposing_h1_keeps_d1_cooldown_in_force(self):
        facts = self._strong_h1_priority_facts("SHORT")
        facts[0]["strong_trend_confirmed"] = False
        self.assertFalse(p.strong_h1_entry_priority(*facts, "SHORT", NOW)["replaces_d1_touch_veto"])
        facts = self._strong_h1_priority_facts("SHORT")
        facts[2]["trend"] = "UP"
        self.assertFalse(p.strong_h1_entry_priority(*facts, "SHORT", NOW)["replaces_d1_touch_veto"])
        facts = self._strong_h1_priority_facts("SHORT")
        facts[0]["exhaustion"] = True
        self.assertFalse(p.strong_h1_entry_priority(*facts, "SHORT", NOW)["replaces_d1_touch_veto"])
        facts = self._strong_h1_priority_facts("SHORT")
        facts[1]["strategy"] = "WAIT_TRANSITION"
        self.assertFalse(p.strong_h1_entry_priority(*facts, "SHORT", NOW)["replaces_d1_touch_veto"])

    def test_unverified_h1_d1_provenance_or_processing_error_cannot_replace_d1(self):
        for change in ("stale", "mismatch", "missing", "invalid_location", "d1_error"):
            facts = self._strong_h1_priority_facts("SHORT", .25)
            with self.subTest(change=change):
                if change == "stale":
                    facts[0]["last_closed_h1"] = (NOW-timedelta(hours=2)).isoformat()
                    facts[1]["h1_volatility"]["last_completed_h1"] = facts[0]["last_closed_h1"]
                elif change == "mismatch":
                    facts[1]["h1_volatility"]["last_completed_h1"] = NOW.isoformat()
                elif change == "missing":
                    facts[0]["available"] = False
                elif change == "invalid_location":
                    facts[0]["percent_b"] = float("nan")
                else:
                    facts[3]["processing_error"] = "invalid_D1_candle"
                result = p.strong_h1_entry_priority(*facts, "SHORT", NOW)
                self.assertFalse(result["replaces_d1_touch_veto"], result)

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
