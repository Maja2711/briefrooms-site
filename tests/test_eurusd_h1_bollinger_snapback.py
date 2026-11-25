"""Closed H1 Bollinger 30/2.5 snapback: Daily and WES execution isolation."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from scripts import eurusd_h1_bollinger_snapback as signal
from scripts import daily_eurusd_bollinger_h1 as daily_h1
from scripts import daily_eurusd_range_exit as daily_exit
from scripts import daily_eurusd_lifecycle as daily_lifecycle
from scripts import daily_eurusd_spot_v14 as daily_v14
from scripts import investments_weekly_ma_structure as weekly_ma
from scripts import investments_wes_lifecycle as weekly_lifecycle
from scripts import investments_weekly_v4 as weekly_v4
from scripts import investments_weekly_v5 as weekly_v5
from scripts.belief_market_data_adapter import Bar

UTC = timezone.utc
START = datetime(2026, 11, 18, 0, tzinfo=UTC)


def price(hour):
    return 1.1000 + (0.00015 if hour % 2 else -0.00015)


def hourly_rebound(*, upper=False):
    rows = []
    for i in range(35):
        p = price(i)
        rows.append((START + timedelta(hours=i+1), p, p+0.0001, p-0.0001, p))
    # Wick pierces the previously-known 30/2.5 band, next H1 recovers.
    p = 1.104 if upper else 1.096
    rows.append((START + timedelta(hours=36),
                 p, 1.106 if upper else p+0.0001,
                 p-0.0001 if upper else 1.094, p))
    p = 1.1013 if upper else 1.0988
    rows.append((START + timedelta(hours=37), p, p+0.0001, p-0.0001, p))
    return rows


def as_minute_bars(*, last_price=1.0991):
    result = []
    for hour in range(37):
        if hour == 35:
            p = 1.096
        elif hour == 36:
            p = 1.0988
        else:
            p = price(hour)
        for minute in range(60):
            v = p
            if hour == 36 and minute < 44:
                v = 1.0970
            low = 1.094 if hour == 35 and minute == 30 else v
            result.append(Bar(
                timestamp=START + timedelta(hours=hour, minutes=minute),
                open=v, high=v + 0.00002, low=low, close=v))
    for minute in range(2):
        result.append(Bar(timestamp=START + timedelta(hours=37, minutes=minute),
                          open=last_price, high=last_price+0.00002,
                          low=last_price, close=last_price))
    return result


def short_position(entry=1.102):
    return {
        "trade_id": "h1-band-test-001", "direction": "SHORT",
        "status": "OPEN", "opened_at": (START+timedelta(hours=20)).isoformat(),
        "expires_at": (START+timedelta(hours=60)).isoformat(),
        "entry": entry, "stop": 1.112, "target": 1.088,
        "entry_score": 82, "entry_confidence": 0.7,
    }


class PureH1SnapbackTests(unittest.TestCase):
    def test_lower_band_breach_then_reentry_is_strong_but_not_trade_authority(self):
        result = signal.assess_closed_h1(
            hourly_rebound(), START + timedelta(hours=37, minutes=2), source="TEST")
        self.assertTrue(result["confirmed"], result)
        self.assertEqual("LOWER_FAST_REENTRY", result["pattern"])
        self.assertTrue(signal.blocks_direction(result, "SHORT"))
        self.assertFalse(signal.blocks_direction(result, "LONG"))
        self.assertFalse(result["execution_authority"])
        self.assertGreater(result["breach_depth_pips"], 4)
        self.assertEqual(30, result["period"])
        self.assertEqual(2.5, result["deviations"])

    def test_upper_band_is_symmetric(self):
        result = signal.assess_closed_h1(
            hourly_rebound(upper=True),
            START+timedelta(hours=37, minutes=2), source="TEST")
        self.assertTrue(result["confirmed"], result)
        self.assertEqual("UPPER_FAST_REENTRY", result["pattern"])
        self.assertTrue(signal.blocks_direction(result, "LONG"))
        self.assertFalse(signal.blocks_direction(result, "SHORT"))

    def test_no_bounce_no_signal(self):
        rows = hourly_rebound()
        t, o, h, l, _ = rows[-1]
        rows[-1] = (t, 1.0955, 1.0956, 1.0954, 1.0955)
        out = signal.assess_closed_h1(
            rows, START+timedelta(hours=37, minutes=2), source="TEST")
        self.assertTrue(out["available"])
        self.assertFalse(out["confirmed"])

    def test_stale_gapped_and_future_candles_cannot_certify(self):
        rows = hourly_rebound()
        self.assertEqual("stale_closed_h1", signal.assess_closed_h1(
            rows, START+timedelta(hours=40), source="TEST")["reason"])
        broken = list(rows)
        broken.pop(-2)  # A truly missing completed hour, not a malformed timestamp.
        self.assertEqual("gapped_recovery_h1", signal.assess_closed_h1(
            broken, START+timedelta(hours=37, minutes=2), source="TEST")["reason"])
        self.assertEqual("future_or_unclosed_h1", signal.assess_closed_h1(
            rows, START+timedelta(hours=36, minutes=59), source="TEST")["reason"])


class DailyH1IntegrationTests(unittest.TestCase):
    def test_daily_short_admission_is_vetoed_by_lower_band_snapback(self):
        bars = []
        for end, o, h, l, c in hourly_rebound():
            at = end - timedelta(hours=1)
            for half in (0, 30):
                bars.append(SimpleNamespace(
                    timestamp=at+timedelta(minutes=half),
                    open=o, high=h, low=l, close=c))
        outcome = daily_h1.evaluate(
            bars, "SHORT", START+timedelta(hours=37, minutes=2))
        self.assertTrue(outcome["available"])
        self.assertTrue(outcome["outer_band_snapback"]["confirmed"])
        self.assertFalse(outcome["allowed"])
        self.assertEqual("outer_band_fast_reentry_against_entry", outcome["reason"])

    def test_daily_profit_exit_after_closed_h1_and_fresh_1m(self):
        bars = as_minute_bars()
        now = START+timedelta(hours=37, minutes=2)
        evidence = daily_exit.assess_snapback(
            short_position(), bars, now,
            exit_price_at_mid=daily_lifecycle.execution_risk_price)
        self.assertTrue(evidence["close"], evidence)
        trade = daily_v14.evaluate_position(short_position(), bars, now)
        self.assertEqual("DYNAMIC_H1_BOLLINGER_SNAPBACK_EXIT", trade["exit_reason"])
        self.assertGreater(trade["r_multiple"], 0)
        self.assertEqual("H1_BOLLINGER_FAST_REENTRY_30_2_5",
                         trade["monitor"]["dynamic_exit"]["policy"])
        self.assertGreater(datetime.fromisoformat(trade["closed_at"]),
                           datetime.fromisoformat(evidence["signal"]["last_closed_h1"]))

    def test_daily_band_alone_cannot_force_losing_position_to_close(self):
        evidence = daily_exit.assess_snapback(
            short_position(entry=1.098), as_minute_bars(),
            START+timedelta(hours=37, minutes=2),
            exit_price_at_mid=daily_lifecycle.execution_risk_price)
        self.assertFalse(evidence["close"])
        self.assertEqual("insufficient_executable_profit", evidence["reason"])

    def test_daily_no_hindsight_or_ongoing_h1(self):
        bars = as_minute_bars()
        before = START+timedelta(hours=36, minutes=59)
        evidence = daily_exit.assess_snapback(
            short_position(), bars, before,
            exit_price_at_mid=daily_lifecycle.execution_risk_price)
        self.assertFalse(evidence["close"])
        self.assertNotEqual("winning_position_h1_outer_band_snapback", evidence["reason"])


class ExtremeProfitRealizationTests(unittest.TestCase):
    def continuing_market(self, upper=False):
        bars=[]
        for i in range(35*60):
            v=price(i//60)
            bars.append(Bar(timestamp=START+timedelta(minutes=i),
                open=v,high=v+.00002,low=v-.00002,close=v))
        for j in range(2):
            v=1.1045+.0005*j if upper else 1.0955-.0005*j
            bars.append(Bar(timestamp=START+timedelta(hours=35,minutes=j),
                open=v,high=v+.00002,low=v-.00002,close=v))
        return bars

    def test_short_extreme_exits_while_price_is_still_falling(self):
        bars=self.continuing_market()
        now=START+timedelta(hours=35,minutes=2)
        p=short_position()
        r=daily_exit.assess_extreme_profit(p,bars,now,
            exit_price_at_mid=daily_lifecycle.execution_exit_price)
        self.assertTrue(r["close"],r)
        self.assertFalse(r["exposure_reduction"]["trend_reversal_required"])
        self.assertEqual(1.0,r["exposure_reduction"]["requested_reduction_fraction"])
        self.assertGreater(r["signal"]["depth_pips"],r["signal"]["required_depth_pips"])
        self.assertGreater(bars[-2].close,bars[-1].close)
        trade=daily_v14.evaluate_position(p,bars,now)
        self.assertEqual("DYNAMIC_H1_BOLLINGER_EXTREME_PROFIT_EXIT",trade["exit_reason"])
        self.assertAlmostEqual(bars[-1].close,trade["exit_price"],places=5)
        self.assertEqual(now,datetime.fromisoformat(trade["closed_at"].replace("Z","+00:00")))

    def test_long_symmetric_and_no_flip(self):
        bars=self.continuing_market(upper=True)
        now=START+timedelta(hours=35,minutes=2)
        p={**short_position(),"direction":"LONG","entry":1.098,"stop":1.088,"target":1.112}
        trade=daily_v14.evaluate_position(p,bars,now)
        self.assertEqual("LONG",trade["direction"])
        self.assertEqual("DYNAMIC_H1_BOLLINGER_EXTREME_PROFIT_EXIT",trade["exit_reason"])

    def test_extreme_signal_does_not_use_incomplete_or_stale_reference_or_wick(self):
        bars=self.continuing_market()
        now=START+timedelta(hours=35,minutes=2)
        p=short_position()
        # No completed mark after position entry.
        self.assertFalse(daily_exit.assess_extreme_profit(
            {**p,"opened_at":now.isoformat()},bars,now,
            exit_price_at_mid=daily_lifecycle.execution_exit_price)["close"])
        # A very old extreme cannot become an exit at a historical price.
        self.assertFalse(daily_exit.assess_extreme_profit(p,bars,now+timedelta(minutes=5),
            exit_price_at_mid=daily_lifecycle.execution_exit_price)["close"])
        # A deep low alone is not the current closing mark.
        bars[-1]=Bar(timestamp=bars[-1].timestamp,open=1.100,high=1.1001,low=1.095,close=1.100)
        self.assertFalse(daily_exit.assess_extreme_profit(p,bars,now,
            exit_price_at_mid=daily_lifecycle.execution_exit_price)["close"])

    def test_hard_tp_has_priority_over_extreme(self):
        bars=self.continuing_market()
        now=START+timedelta(hours=35,minutes=2)
        p={**short_position(),"target":1.096}
        trade=daily_v14.evaluate_position(p,bars,now)
        self.assertEqual("TP",trade["exit_reason"]) if trade["exit_reason"]=="TP" else self.assertIn(trade["exit_reason"],{"TAKE_PROFIT","TP_HIT","TAKE_PROFIT_HIT"})


class PostProfitContinuationTests(unittest.TestCase):
    NOW = START+timedelta(hours=38,minutes=5)

    def prior(self, direction="short"):
        return {"instrument_id":"eurusd","direction":direction,
                "entry_captured_at":(START+timedelta(hours=20)).isoformat(),
                "exit_captured_at":(START+timedelta(hours=35,minutes=5)).isoformat(),
                "entry_price":1.105 if direction=="short" else 1.095,
                "exit_price":1.094 if direction=="short" else 1.106,
                "exit_reason":"wes_eurusd_outer_band_extreme_profit_take"}

    def context(self, previous, current):
        return {"available":True,"lower":1.095,"middle":1.100,"upper":1.105,
                "last_closed_h1":(START+timedelta(hours=38)).isoformat(),
                "recent_completed_h1":[
                    {"closed_at":(START+timedelta(hours=37)).isoformat(),"close":previous},
                    {"closed_at":(START+timedelta(hours=38)).isoformat(),"close":current}]}

    def proof(self, previous, current, direction="short"):
        return signal.assess_profit_reentry(self.prior(direction),
            self.context(previous,current),direction,self.NOW,max_age_hours=168)

    def test_short_before_ma30_and_far_edge_after_crossing(self):
        for previous,current,zone in [(1.0994,1.0989,"BEFORE_MA30"),
                                       (1.1049,1.1042,"OPPOSITE_OUTER_BAND")]:
            proof=self.proof(previous,current)
            self.assertTrue(proof["required"],proof)
            self.assertTrue(proof["allowed"],proof)
            self.assertEqual(zone,proof["zone"])
            self.assertFalse(proof["direction_authority"])
            self.assertTrue(signal.reentry_fill_allowed(proof,current,
                self.NOW,self.NOW))
            self.assertFalse(signal.reentry_fill_allowed(proof,1.0951,
                self.NOW,self.NOW))

    def test_long_is_mirror_and_no_reverse_authority(self):
        for previous,current,zone in [(1.1006,1.1011,"BEFORE_MA30"),
                                       (1.0951,1.0958,"OPPOSITE_OUTER_BAND")]:
            proof=self.proof(previous,current,"long")
            self.assertTrue(proof["allowed"],proof)
            self.assertEqual(zone,proof["zone"])
            self.assertTrue(signal.reentry_fill_allowed(proof,current,self.NOW,self.NOW))
            self.assertFalse(signal.reentry_fill_allowed(proof,1.1049,self.NOW,self.NOW))
        proof=signal.assess_profit_reentry(self.prior(),self.context(1.101,1.102),
            "long",self.NOW,max_age_hours=168)
        self.assertFalse(proof["required"])

    def test_wait_for_inside_correction_and_resumption_not_chase(self):
        for previous,current in [(1.094,1.096),(1.099,1.0995),(1.103,1.102),
                                 (1.106,1.1055),(1.0988,1.09879)]:
            self.assertFalse(self.proof(previous,current)["allowed"],(previous,current))
        proof=signal.assess_profit_reentry(self.prior(),{},
            "short",self.NOW,max_age_hours=168)
        self.assertFalse(proof["allowed"])

    def test_reject_pre_exit_stale_fill_and_unprofitable_prior(self):
        proof=self.proof(1.0994,1.0989)
        self.assertFalse(signal.reentry_fill_allowed(proof,1.0989,
            self.NOW-timedelta(hours=1),self.NOW))
        self.assertFalse(signal.reentry_fill_allowed(proof,1.0989,
            self.NOW,self.NOW+timedelta(minutes=16)))
        prior=self.prior()
        prior["entry_price"]=1.093
        self.assertFalse(signal.assess_profit_reentry(prior,self.context(1.0994,1.0989),
            "short",self.NOW,max_age_hours=168)["allowed"])
        prior=self.prior()
        prior["exit_captured_at"]=(START+timedelta(hours=37,minutes=1)).isoformat()
        self.assertFalse(signal.assess_profit_reentry(prior,self.context(1.0994,1.0989),
            "short",self.NOW,max_age_hours=168)["allowed"])

    def test_wes_limit_plan_is_in_frozen_h1_zone(self):
        proof=self.proof(1.1049,1.1042)
        item=self.prior()
        decision={"direction":"short","post_profit_band_reentry":proof}
        fresh={"signals":{"last_close":1.09,"atr14":.005,"ema20":1.095}}
        plan=weekly_v5.build_entry_price_plan(item,decision,fresh,self.NOW,{})
        self.assertEqual("sell_limit",plan["order_type"])
        self.assertTrue(1.1042<=plan["target_price"]<=1.105,plan)
        self.assertTrue(signal.reentry_fill_allowed(proof,plan["target_price"],
            self.NOW,self.NOW))
        other={**item,"instrument_id":"btcusd"}
        self.assertFalse(weekly_ma.profit_reentry(other,{},"short",self.NOW)["required"])


class WeeklyH1IsolationTests(unittest.TestCase):
    def weekly_frame(self):
        rows = hourly_rebound()
        return pd.DataFrame([
            {"Open": o, "High": h, "Low": l, "Close": c}
            for _, o, h, l, c in rows
        ], index=pd.DatetimeIndex([t-timedelta(hours=1) for t,*_ in rows]))

    def test_wes_uses_closed_h1_and_cannot_retroactively_veto_a_fill(self):
        now = START+timedelta(hours=37, minutes=2)
        out = weekly_ma.h1_snapback_from_frame(self.weekly_frame(), now)
        self.assertTrue(out["confirmed"], out)
        context = {"data_quality": "passed", "h1_bollinger_snapback": out}
        self.assertTrue(weekly_ma.band_reentry_conflicts(context, "short"))
        self.assertFalse(weekly_ma.band_reentry_conflicts(context, "long"))
        self.assertFalse(weekly_ma.band_reentry_conflicts(
            context, "short", execution_at=START+timedelta(hours=36, minutes=30)))
        self.assertTrue(weekly_ma.band_reentry_conflicts(
            context, "short", execution_at=START+timedelta(hours=37, minutes=1)))

    def test_wes_existing_short_can_close_profitably_after_hard_risk(self):
        now = datetime(2026, 11, 23, 15, 21, tzinfo=UTC)
        entry_at = datetime(2026, 11, 23, 12, tzinfo=UTC)
        sig = {
            "available": True, "confirmed": True,
            "pattern": "LOWER_FAST_REENTRY", "counter_to_direction": "SHORT",
            "breach_h1": datetime(2026, 11, 23, 13, tzinfo=UTC).isoformat(),
            "last_closed_h1": datetime(2026, 11, 23, 15, tzinfo=UTC).isoformat(),
            "last_h1_close": 1.0980,
        }
        eur = {
            "instrument_id": "eurusd", "direction": "short", "trade_status": "open",
            "entry_captured_at": entry_at.isoformat(), "entry_price": 1.102,
            "exit_price": None, "risk_plan": {
                "stop_loss_price": 1.112, "take_profit_price": 1.088,
            },
        }
        btc = {"instrument_id": "btcusd", "trade_status": "no_trade"}
        week = {"week_id": "2026-W48", "instruments": [eur, btc]}
        mark = {
            "price": 1.0981,
            "timestamp": datetime(2026, 11, 23, 15, 15, tzinfo=UTC).isoformat(),
            "source": "Yahoo Finance:EURUSD=X:5m",
        }
        def read(path, default=None):
            if path == weekly_lifecycle.v5.POLICY:
                return {"instruments": [
                    {"instrument_id": "eurusd", "round_trip_cost": 1.5,
                     "cost_unit": "pips"}]}
            return week
        with patch.object(weekly_lifecycle.v4, "read", side_effect=read), \
             patch.object(weekly_lifecycle.v4, "write") as write, \
             patch.object(weekly_lifecycle.ma_structure.yf, "download"), \
             patch.object(weekly_lifecycle.ma_structure, "h1_snapback_from_frame", return_value=sig), \
             patch.object(weekly_lifecycle.v3, "last_completed_5m_bar", return_value=mark), \
             patch.object(weekly_lifecycle.v5.wes_outbox, "emit_weekly_event") as event:
            self.assertTrue(weekly_lifecycle.review_eurusd_band_snapback_close(
                Path("/tmp/synthetic-W48.json"), now))
        self.assertEqual("wes_h1_bollinger_snapback_profit_exit", eur["exit_reason"])
        self.assertEqual("closed", eur["trade_status"])
        self.assertGreater(eur["h1_band_exit_evidence"]["net_profit_after_policy_cost_pips"], 6)
        self.assertEqual({"instrument_id": "btcusd", "trade_status": "no_trade"}, btc)
        write.assert_called_once()
        event.assert_called_once()

    def test_wes_does_not_exit_on_stale_mark_or_without_profit(self):
        now = datetime(2026, 11, 23, 15, 21, tzinfo=UTC)
        sig = {"available": True, "confirmed": True, "counter_to_direction": "SHORT",
               "breach_h1": "2026-11-23T13:00:00+00:00",
               "last_closed_h1": "2026-11-23T15:00:00+00:00",
               "last_h1_close": 1.098}
        eur = {"instrument_id": "eurusd", "direction": "short", "entry_price": 1.102,
               "entry_captured_at": "2026-11-23T12:00:00+00:00",
               "exit_price": None,
               "risk_plan": {"stop_loss_price": 1.112, "take_profit_price": 1.088}}
        week = {"instruments": [eur]}
        with patch.object(weekly_lifecycle.v4, "read", return_value=week), \
             patch.object(weekly_lifecycle.ma_structure.yf, "download"), \
             patch.object(weekly_lifecycle.ma_structure, "h1_snapback_from_frame", return_value=sig), \
             patch.object(weekly_lifecycle.v3, "last_completed_5m_bar",
                          return_value={"price": 1.0981,
                                        "timestamp": "2026-11-23T14:15:00+00:00"}), \
             patch.object(weekly_lifecycle.v4, "write") as write:
            self.assertFalse(weekly_lifecycle.review_eurusd_band_snapback_close(
                Path("/tmp/synthetic-W48.json"), now))
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
