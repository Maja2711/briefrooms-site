"""Profit-protection research and first-seen signal capture contracts."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_profit_protection_lab as lab
from scripts import daily_eurusd_exit_intelligence_v2 as observer

T0=datetime(2026,10,5,10,0,tzinfo=timezone.utc)


def b(i, close, op=None, high=None, low=None):
    return {"time":T0+timedelta(minutes=i), "open":close if op is None else op,
            "close":close, "high":close if high is None else high,
            "low":close if low is None else low}


def trade():
    return {
        "trade_id":"T01", "opened_at":lab.ts(T0),
        "closed_at":lab.ts(T0+timedelta(minutes=42)),
        "entry":1.1200, "stop":1.1240, "target":1.1110,
        "exit_price":1.1194, "direction":"SHORT",
        "r_multiple":0.15, "exit_reason":"DYNAMIC_PROFIT_EXIT",
        "engine_version":"eurusd-daily-spot-v1.9.0",
        "execution_price_engine":{"synthetic_half_spread_pips":1.5},
    }


def market():
    bars=[]
    for i in range(43):
        if i<=20:
            px=1.1200-i*0.00009 # 18p gain at 20m
        elif i<=29:
            px=1.1182+(i-20)*0.00010 # giveback 9p
        else:
            px=1.1191+(i-29)*0.000022
        bars.append(b(i,px))
    return bars


class ProfitProtectionLabTests(unittest.TestCase):
    def test_momentum_strategy_never_uses_future(self):
        t=trade()
        bars=market()
        first=lab.simulate(t,bars,"MOMENTUM_5_15")
        self.assertEqual(first["status"],"EARLIER_RESEARCH_EXIT")
        self.assertTrue(first["signal_available_before_fill"])
        signal=lab.parse(first["signal"]["signal_at_bar_close"])
        fill=lab.parse(first["simulated_exit_at"])
        self.assertLessEqual(signal,fill)
        self.assertFalse(first["signal"]["future_used"])
        self.assertFalse(first["execution_proven"])
        later=bars+[b(43,1.5),b(44,1.6)]
        self.assertEqual(first,lab.simulate(t,later,"MOMENTUM_5_15"))

    def test_three_policy_shadow_comparison_and_spread(self):
        t=trade()
        values=[lab.simulate(t,market(),policy) for policy in lab.STRATEGIES]
        self.assertEqual(len(values),3)
        for value in values:
            self.assertEqual(value["status"],"EARLIER_RESEARCH_EXIT")
            self.assertTrue(value["epe_half_spread_recorded"])
            self.assertEqual(value["cost_half_spread_pips"],1.5)
            self.assertGreater(value["simulated_exit_at"],t["opened_at"])
            self.assertLess(value["simulated_exit_at"],t["closed_at"])

    def test_stop_prioritized_over_pending_next_open_and_tp(self):
        t=trade()
        bars=market()
        bars[26] = b(26,1.119,op=1.1188,high=1.125,low=1.110)
        out=lab.simulate(t,bars,"GIVEBACK_MOMENTUM")
        self.assertEqual(out["status"],"INTRABAR_RISK_BARRIER")
        self.assertEqual(out["exit_reason"],"STOP_LOSS")
        self.assertTrue(out["conservative_same_bar"])

    def test_missing_minutes_censored_and_never_synthetic_success(self):
        t=trade()
        bars=[x for x in market() if x["time"] != T0+timedelta(minutes=8)]
        for policy in lab.STRATEGIES:
            self.assertEqual(lab.simulate(t,bars,policy)["status"],"CENSORED_MARKET_DATA_GAP")
        self.assertEqual(lab.simulate(t,market()[:10],"CLOSE_TRAIL_035R")["status"],
                         "CENSORED_NO_FULL_ENTRY_TO_EXIT_PATH")

    def test_no_simulated_epe_in_baseline(self):
        t=trade()
        t["r_multiple"]=0.27
        t["exit_reason"]="DYNAMIC_RISK_EXIT"
        original=copy.deepcopy(t)
        result=lab.step({},{"trades":[t]},{"snapshots":[]},market(),T0+timedelta(hours=1))
        self.assertEqual(result["comparisons"][0]["baseline"]["actual_r"],0.27)
        self.assertEqual(result["comparisons"][0]["baseline"]["exit_reason"],"DYNAMIC_RISK_EXIT")
        self.assertTrue(result["comparisons"][0]["baseline"]["not_a_replay"])
        self.assertEqual(t,original)
        self.assertFalse(result["authority"]["execution"])
        self.assertFalse(result["authority"]["daily_exit_mutation"])
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(result,lab.step(result,{"trades":[t]},{"snapshots":[]},market(),T0+timedelta(hours=1)))

    def test_missing_actual_spread_does_not_qualify_paired_evidence(self):
        t=trade()
        t.pop("execution_price_engine")
        result=lab.step({},{"trades":[t]},{"snapshots":[]},market(),T0+timedelta(hours=1))
        self.assertEqual(result["paired_comparable_trade_count"],0)
        self.assertEqual(lab.spread(t),(0.75,False))

    def test_absence_of_timeline_does_not_claim_live_exit_signal(self):
        t=trade()
        c=lab.coverage_for_trade(t,[])
        self.assertEqual(c["evidence_status"],"UNKNOWN_NO_TIMELY_SIGNALS")
        self.assertTrue(c["no_hindsight_promotion"])

    def test_open_position_captures_minutes_with_true_first_seen(self):
        t=trade()
        t["status"]="OPEN"
        now=T0+timedelta(minutes=22)
        fx=[{"timestamp":observer.iso(z["time"]), "open":z["open"],
             "close":z["close"], "high":z["high"], "low":z["low"]}
            for z in market()[:22]]
        spot={"metadata":{"position":t}}
        history={"trades":[]}
        j,r=observer.step(spot,history,{}, {},fx,[],now)
        self.assertEqual(j["monitor_health"]["status"],"OK")
        self.assertGreater(len(j["snapshots"]),2)
        self.assertLessEqual(len(j["snapshots"]),10)
        self.assertTrue(all(x["first_seen_at"]==observer.iso(now) for x in j["snapshots"]))
        self.assertTrue(any(x["mode"]=="DELAYED_BATCH_RECONSTRUCTION" for x in j["snapshots"]))
        self.assertTrue(all(x["exit_authority"] is False for x in j["snapshots"]))
        after,_=observer.step(spot,history,j,r,fx,[],now)
        self.assertEqual(after["snapshots"],j["snapshots"])

    def test_stale_bar_produces_health_failure_not_false_signal(self):
        t=trade()
        t["status"]="OPEN"
        now=T0+timedelta(minutes=30)
        fx=[{"timestamp":observer.iso(z["time"]), "open":z["open"],
             "close":z["close"], "high":z["high"], "low":z["low"]}
            for z in market()[:20]]
        journal,_=observer.step({"metadata":{"position":t}},{"trades":[]},{},{},fx,[],now)
        self.assertEqual(journal["monitor_health"]["status"],"STALE_OR_MISSING_FX_1M_BAR")
        self.assertFalse(any(x.get("timely_observation") for x in journal["snapshots"]))

    def test_not_triggered_is_included_as_noop_in_denominator(self):
        t=trade()
        m=[b(i,1.12-0.00001*i) for i in range(43)]
        x=lab.step({},{"trades":[t]},{"snapshots":[]},m,T0+timedelta(hours=1))
        scoreboard=x["scoreboard"]
        self.assertTrue(all(s["n_paired_comparable_trades"]==1 for s in scoreboard.values()))
        self.assertTrue(all(s["mean_incremental_r"]==0.0 for s in scoreboard.values()))


if __name__=="__main__":
    unittest.main()
