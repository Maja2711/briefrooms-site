"""EURUSD post-close audit: immutable source, live provenance, challenger discipline."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scripts import daily_eurusd_posttrade_audit as a

NOW=datetime(2026,10,9,8,0,45,tzinfo=timezone.utc)
START=NOW-timedelta(minutes=20,seconds=45)


def trade(tid="EURUSD:T1",direction="LONG"):
    return {
        "trade_id":tid,"instrument":"EUR/USD","direction":direction,
        "opened_at":a.iso(START),"closed_at":a.iso(NOW),
        "entry":1.1200,"stop":1.1160 if direction=="LONG" else 1.1240,
        "target":1.1270 if direction=="LONG" else 1.1130,
        "exit_price":1.1220 if direction=="LONG" else 1.1180,
        "r_multiple":0.5,"exit_reason":"DYNAMIC_PROFIT_EXIT",
        "mfe_pips":25,"mae_pips":-2,
        "monitor":{"dynamic_exit":{"policy":"R_PACE_V1",
                                    "mfe_r":0.625,"giveback_r":0.125,
                                    "current_r":0.5,"hold_score":-0.15,
                                    "tp_feasibility_ratio":0.2},
                   "execution_price_basis":"MID_PROXY"},
        "execution_price_engine":{"status":"VERIFIED_FILL",
                                  "paper_trading_only":True,
                                  "synthetic_half_spread_pips":1.5},
    }


def sample(i,seen_lag=30,tid="EURUSD:T1",with_first_seen=True):
    bar=START+timedelta(minutes=i)
    finished=bar+timedelta(minutes=1)
    seen=finished+timedelta(seconds=seen_lag)
    s={"trade_id":tid,"market_bar_at":a.iso(bar),
       "market_bar_closed_at":a.iso(finished),
       "captured_at":a.iso(seen),
       "timely_observation":seen_lag<=180,
       "signals":{"momentum_5m_exhaustion":i in (5,10)},
       "current_indicative_pips":i/3}
    if with_first_seen:
        s["first_seen_at"]=a.iso(seen)
    return s


def labs(tid="EURUSD:T1"):
    values={
        "MOMENTUM_5_15":{
            "status":"EARLIER_RESEARCH_EXIT","epe_half_spread_recorded":True,
            "execution_proven":False,"signal_available_before_fill":True,
            "vs_actual_r":-0.2,"simulated_r":0.3,
            "simulated_exit_at":a.iso(NOW-timedelta(minutes=10))
        },
        "GIVEBACK_MOMENTUM":{
            "status":"NOT_TRIGGERED_BEFORE_BASELINE",
            "epe_half_spread_recorded":True
        },
        "CLOSE_TRAIL_035R":{
            "status":"CENSORED_MARKET_DATA_GAP",
            "epe_half_spread_recorded":False,
            "execution_proven":False
        },
    }
    return {"comparisons":[{
        "trade_id":tid,"baseline":{"actual_r":0.5,
                     "exit_reason":"DYNAMIC_PROFIT_EXIT","not_a_replay":True},
        "challengers":values,
    }]}


def reviews(tid="EURUSD:T1"):
    return {"reviews":[{"trade_id":tid,"closed_at":a.iso(NOW),
                        "actual_exit_reason":"DYNAMIC_PROFIT_EXIT",
                        "actual_r":0.5,"data_audit":{
                            "profit_snapshots_at_least_8p":3}}]}


class TestPostTradeAudit(unittest.TestCase):
    def test_reconciles_recorded_r_and_r_pace_exit(self):
        x=a.pnl_and_exit(trade())
        self.assertEqual(x["status"],"CONSISTENT_WITH_RECORDED_R")
        self.assertEqual(x["actual_pips"],20.0)
        self.assertEqual(x["actual_r"],0.5)
        self.assertEqual(x["exit_category"],"R_PACE_DYNAMIC")
        self.assertEqual(x["recorded_dynamic_exit"]["status"],"OBSERVED_R_PACE_V1")
        self.assertTrue(x["pricing_provenance"]["paper_trading_only"])
        self.assertFalse(x["pricing_provenance"]["exit_is_executable_quote"])

    def test_95_percent_timely_full_life_and_signals(self):
        # Complete bars START+0 ... START+19; actual close is 45s into next minute.
        x=a.timeline_audit(trade(),[sample(i) for i in range(20)])
        self.assertEqual(x["status"],"NEAR_COMPLETE_TIMELY_LIVE_EVIDENCE")
        self.assertEqual(x["expected_full_minutes"],20)
        self.assertEqual(x["timely_minutes"],20)
        self.assertEqual(x["missing_or_late_minutes"],0)
        self.assertFalse(x["gap_alert"])
        self.assertEqual(x["signal_bearing_timely_minutes"],2)

    def test_late_and_post_close_are_never_live_evidence(self):
        s=[sample(i) for i in range(20)]
        s[0]=sample(0,seen_lag=400) # late but first_seen during position
        s[1]=sample(1,seen_lag=4000) # first_seen after actual close
        s[2]=sample(2,with_first_seen=False)
        s[3]["first_seen_at"]=a.iso(START+timedelta(minutes=2))
        x=a.timeline_audit(trade(),s)
        self.assertEqual(x["status"],"PARTIAL_TIMELY_LIVE_EVIDENCE")
        self.assertEqual(x["timely_minutes"],16)
        self.assertEqual(x["missing_or_late_minutes"],4)
        self.assertEqual(x["late_sample_count"],1)
        self.assertEqual(x["postclose_backfill_count"],1)
        self.assertEqual(x["samples_missing_first_seen"],1)
        self.assertEqual(x["clock_invalid_count"],1)

    def test_all_evidence_missing_is_red_flag_not_success(self):
        x=a.timeline_audit(trade(),[])
        self.assertEqual(x["status"],"MISSING_TIMELY_LIVE_EVIDENCE")
        self.assertEqual(x["timely_minutes"],0)
        self.assertEqual(x["missing_or_late_minutes"],20)
        self.assertTrue(x["gap_alert"])

    def test_comparison_includes_no_trigger_and_censored(self):
        x=a.challenger_audit(trade(),labs()["comparisons"][0])
        self.assertEqual(x["status"],"RESEARCH_COMPARISON_AVAILABLE")
        self.assertEqual(x["results"]["MOMENTUM_5_15"]["incremental_r_vs_canonical"],-0.2)
        self.assertEqual(x["results"]["GIVEBACK_MOMENTUM"]["incremental_r_vs_canonical"],0)
        self.assertIsNone(x["results"]["CLOSE_TRAIL_035R"]["incremental_r_vs_canonical"])
        self.assertIsNone(x["claimed_winner"])

    def test_baseline_mismatch_never_pretends_comparison(self):
        bad=copy.deepcopy(labs()["comparisons"][0])
        bad["baseline"]["actual_r"]=0.8
        x=a.challenger_audit(trade(),bad)
        self.assertEqual(x["status"],"BASELINE_MISMATCH")
        self.assertEqual(x["results"],{})

    def test_unfinished_shadow_report_is_pending_then_recovers(self):
        t=trade()
        previous={}
        h={"trades":[t]}
        original=copy.deepcopy((h,labs(),reviews()))
        missing=a.build_report(previous,h,{}, {},{}, [],NOW)
        self.assertEqual(missing["summary"]["waiting_for_challengers"],1)
        self.assertEqual(missing["audits"][0]["overall_status"],"NEEDS_DATA_OR_REVIEW")
        ready=a.build_report(missing,h,{"snapshots":[sample(i) for i in range(1,20)]},
                             labs(),reviews(),[],NOW+timedelta(minutes=15))
        self.assertEqual(ready["audits"][0]["overall_status"],"AUDITED")
        self.assertEqual(ready["audits"][0]["shadow_challengers"]["comparable_count"],2)
        self.assertEqual((h,labs(),reviews()),original)

    def test_repeat_audit_is_content_and_timestamp_idempotent(self):
        h={"trades":[trade()]}
        j={"snapshots":[sample(i) for i in range(1,20)]}
        first=a.build_report({},h,j,labs(),reviews(),[],NOW)
        second=a.build_report(first,h,j,labs(),reviews(),[],NOW+timedelta(hours=3))
        self.assertEqual(first,second)

    def test_reaudit_only_changes_when_new_live_data_arrives(self):
        h={"trades":[trade()]}
        old=a.build_report({},h,{},labs(),reviews(),[],NOW)
        new=a.build_report(old,h,{"snapshots":[sample(i) for i in range(1,20)]},
                           labs(),reviews(),[],NOW+timedelta(hours=1))
        self.assertNotEqual(new["updated_at"],old["updated_at"])
        self.assertGreater(new["audits"][0]["live_telemetry"]["timely_minutes"],0)

    def test_only_closed_eurusd_is_audited_in_order(self):
        one=trade("EURUSD:T1")
        two=trade("EURUSD:T2")
        two["closed_at"]=a.iso(NOW+timedelta(minutes=1))
        invalid=trade("BTC:T3")
        invalid["instrument"]="BTC/USD"
        opened=trade("EURUSD:OPEN")
        opened.pop("closed_at")
        result=a.build_report({},{"trades":[two,opened,invalid,one]},
                              {},{}, {},[],NOW+timedelta(minutes=2))
        self.assertEqual(result["summary"]["audited_trades"],2)
        self.assertEqual(result["latest_trade_id"],"EURUSD:T2")
        self.assertFalse(result["authority"]["trade_execution"])
        self.assertFalse(result["authority"]["canonical_history_write"])
        self.assertFalse(result["authority"]["automatic_promotion"])

    def test_no_future_close_is_audited_before_actual_close_timestamp(self):
        future=trade("EURUSD:FUTURE")
        future["closed_at"]=a.iso(NOW+timedelta(minutes=2))
        result=a.build_report({},{"trades":[future]}, {},{}, {},[],NOW)
        self.assertEqual(result["summary"]["audited_trades"],0)
        matured=a.build_report(result,{"trades":[future]},{},{},{},[],
                               NOW+timedelta(minutes=3))
        self.assertEqual(matured["summary"]["audited_trades"],1)

    def test_archive_and_hot_duplicates_prefer_earliest_seen(self):
        original=sample(5,seen_lag=35)
        newer=sample(5,seen_lag=140)
        now=NOW
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            folder=root/"20261009"
            folder.mkdir()
            (folder/"06.json").write_text(json.dumps({
                "authority":"SHADOW_OBSERVATION_ONLY",
                "snapshots":[original]}))
            source,metadata=a.gather_evidence({"snapshots":[newer]},root)
            self.assertEqual(len(source),1)
            self.assertEqual(source[0]["first_seen_at"],original["first_seen_at"])
            self.assertEqual(metadata["validated_archive_shards"],1)

    def test_short_exit_r_geometry(self):
        t=trade(direction="SHORT")
        z=a.pnl_and_exit(t)
        self.assertEqual(z["actual_pips"],20)
        self.assertEqual(z["actual_r"],0.5)


if __name__=="__main__":
    unittest.main()
