"""W48-like EURUSD profit protection regression: no historical-profit fiction."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts import wes_eurusd_profit_protection as guard
from scripts import audit_intraday_risk_exits as risk

UTC = timezone.utc
OPENED = datetime(2026,11,23,9,47,47,tzinfo=UTC)
START = datetime(2026,11,23,9,50,tzinfo=UTC)
ENTRY = 1.09841835
NOW = START + timedelta(minutes=5*40,seconds=40)


def position():
    return {
        "instrument_id": "eurusd", "symbol": "EURUSD=X",
        "direction": "short", "entry_price": ENTRY, "entry_captured_at": OPENED.isoformat(),
        "exit_price": None, "trade_status": "open", "wes_status": "open_wes_continuous_position",
        "risk_plan": {
            "generated_at": "2026-11-23T09:48:03+00:00",
            "stop_loss_price": 1.10602325,
            "take_profit_price": 1.08191835,
            "scheduled_exit": "2026-11-30T09:47:47+00:00",
        },
    }


def five_min(*, last=1.0920, stale=False, short_history=False, flat=False):
    rows = []
    for i in range(40):
        if flat:
            v = ENTRY-0.002
        elif i < 20:
            v = ENTRY - (0.0100 * i/19)
        else:
            v = 1.08841835+(last-1.08841835)*(i-19)/20
        at = START + timedelta(minutes=5*i)
        rows.append(risk.PriceBar(at,high=v+0.00007,low=v-0.00007,
                                  source="Yahoo Finance:EURUSD=X:chart:5m",close=v))
    if short_history:
        rows=rows[-1:]
    if stale:
        rows=rows[:-4]
    return rows


def daily():
    at = datetime(2026,10,10,tzinfo=UTC)
    return [(at+timedelta(days=i),1.094 if i%2 else 1.096) for i in range(44)]


class ProfitProtectionUnitTests(unittest.TestCase):
    def test_large_profit_giveback_before_remote_tp_exits_at_current_5m(self):
        p=position()
        outcome=guard.assess(p,five_min(),[],NOW,round_trip_cost_pips=1.5)
        self.assertTrue(outcome["close"],outcome)
        self.assertEqual("wes_eurusd_peak_profit_giveback_exit",outcome["exit_reason"])
        self.assertGreater(outcome["peak_net_proxy_pips"],95)
        self.assertGreater(outcome["giveback_pips"],30)
        self.assertEqual(1.092,outcome["exit_price"])
        self.assertNotEqual(1.08841835,outcome["exit_price"])
        self.assertEqual((START+timedelta(minutes=200)).isoformat(),
                         outcome["exit_captured_at"])
        self.assertFalse(outcome["historical_fill_inference"])

    def test_lower_d1_band_touch_can_take_profit_without_waiting_to_give_back(self):
        p=position()
        bars=five_min(last=1.0915)
        # Ordinary touch (not deep extension) at the shifted lower band.
        out=guard.assess(p,bars,[(at,px-.001) for at,px in daily()],NOW,round_trip_cost_pips=1.5)
        self.assertTrue(out["close"],out)
        self.assertEqual("wes_eurusd_daily_band_profit_take",out["exit_reason"])
        self.assertTrue(out["daily_band_touch_now"])
        self.assertEqual(30,out["daily_bollinger"]["period"])
        self.assertEqual(2.5,out["daily_bollinger"]["deviations"])

    def test_d1_deep_break_takes_profit_before_any_rebound(self):
        bars=five_min(last=1.08841835)
        out=guard.assess(position(),bars,daily(),NOW,round_trip_cost_pips=1.5)
        self.assertTrue(out["close"],out)
        self.assertEqual("wes_eurusd_outer_band_extreme_profit_take",out["exit_reason"])
        self.assertTrue(out["outer_band_extensions"]["D1"]["extreme"])
        self.assertAlmostEqual(0,out["giveback_pips"],places=3)
        self.assertEqual(1.0,out["exposure_reduction"]["requested_reduction_fraction"])
        self.assertFalse(out["exposure_reduction"]["trend_reversal_required"])
        self.assertEqual(bars[-1].close,out["exit_price"])
        self.assertFalse(out["historical_fill_inference"])

    def test_upper_d1_deep_break_takes_long_profit_symmetrically(self):
        p=position()
        p["direction"]="long"
        p["risk_plan"]["stop_loss_price"]=2*ENTRY-1.10602325
        p["risk_plan"]["take_profit_price"]=2*ENTRY-1.08191835
        bars=[risk.PriceBar(b.ts, high=2*ENTRY-b.low, low=2*ENTRY-b.high,
                            source=b.source, close=2*ENTRY-b.close)
              for b in five_min(last=1.08841835)]
        d1=[(at,2*ENTRY-px) for at,px in daily()]
        out=guard.assess(p,bars,d1,NOW,round_trip_cost_pips=1.5)
        self.assertTrue(out["close"],out)
        self.assertTrue(out["outer_band_extensions"]["D1"]["extreme"])
        self.assertEqual("wes_eurusd_outer_band_extreme_profit_take",out["exit_reason"])

    def test_h1_deep_break_with_preentry_reference_without_d1(self):
        start=START.replace(minute=0)-timedelta(hours=34)
        history=[]
        for i in range(34*12+10):
            at=start+timedelta(minutes=5*i)
            v=ENTRY+(.0002 if (i//12)%2 else -.0002)
            history.append(risk.PriceBar(at,high=v+.0001,low=v-.0001,
                source="Yahoo Finance:EURUSD=X:chart:5m",close=v))
        # Market keeps moving down, current completed bar, zero giveback.
        bars=history+[
            risk.PriceBar(START,high=ENTRY-.004,low=ENTRY-.0041,source="test",close=ENTRY-.004),
            risk.PriceBar(START+timedelta(minutes=5),high=ENTRY-.005,low=ENTRY-.0051,source="test",close=ENTRY-.005)]
        now=START+timedelta(minutes=10,seconds=30)
        out=guard.assess(position(),bars,[],now,round_trip_cost_pips=1.5)
        self.assertTrue(out["close"],out)
        self.assertTrue(out["outer_band_extensions"]["H1"]["extreme"])
        self.assertLess(out["peak_net_proxy_pips"],65)
        self.assertEqual(0,out["giveback_pips"])

    def test_fractional_request_cannot_close_whole_legacy_position(self):
        p=position()
        before=json.dumps(p,sort_keys=True)
        with self.assertRaisesRegex(ValueError,"partial_profit_exit"):
            risk.apply_eurusd_profit_exit(p,{"exposure_reduction":{
                "requested_reduction_fraction":0.5}},NOW)
        self.assertEqual(before,json.dumps(p,sort_keys=True))

    def test_no_live_fill_on_stale_or_prior_history(self):
        p=position()
        self.assertFalse(guard.assess(
            p,five_min(stale=True),daily(),NOW,round_trip_cost_pips=1.5)["close"])
        self.assertFalse(guard.assess(
            p,five_min(short_history=True),daily(),NOW,round_trip_cost_pips=1.5)["close"])
        self.assertFalse(guard.assess(
            p,five_min(),daily(),START-timedelta(minutes=1),
            round_trip_cost_pips=1.5)["close"])

    def test_no_profit_take_from_unprofitable_extreme_or_mismatched_instrument(self):
        p=position()
        self.assertFalse(guard.assess(
            p,five_min(flat=True),daily(),NOW,round_trip_cost_pips=1.5)["close"])
        p["instrument_id"]="btcusd"
        self.assertFalse(guard.assess(
            p,five_min(),daily(),NOW,round_trip_cost_pips=1.5)["close"])

    def test_missing_d1_does_not_disable_verified_giveback(self):
        out=guard.assess(position(),five_min(),[],NOW,round_trip_cost_pips=1.5)
        self.assertTrue(out["close"])
        self.assertFalse(out["daily_bollinger"]["available"])

    def test_unknown_cost_or_missing_risk_fails_closed(self):
        p=position()
        self.assertFalse(guard.assess(
            p,five_min(),daily(),NOW,round_trip_cost_pips=float("nan"))["close"])
        del p["risk_plan"]["stop_loss_price"]
        self.assertFalse(guard.assess(
            p,five_min(),daily(),NOW,round_trip_cost_pips=1.5)["close"])

    def test_uncompleted_d1_does_not_contribute_to_band(self):
        records=daily()
        band=guard.daily_band_from_completed_closes(records,NOW)
        self.assertTrue(band["available"])
        future=records+[(NOW+timedelta(hours=1), 1.04)]
        bad=guard.daily_band_from_completed_closes(future,NOW)
        self.assertFalse(bad["available"])


class CanonicalFiveMinuteAuditTests(unittest.TestCase):
    def test_frozen_hard_stop_first_and_no_optional_profit_probe(self):
        p=position()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"2026-W48.json"
            path.write_text(json.dumps({"week_id":"2026-W48","instruments":[p]}))
            stop=( "stop_loss",1.10602325,START, "test","5m_ohlc")
            with patch.object(risk,"canonical_live_risk_hit",return_value=stop), \
                 patch.object(risk,"fetch_yahoo_bars") as fetched, \
                 patch.object(risk.wes_outbox,"is_wes_week",return_value=False):
                r=risk.audit(path=path,now=NOW,persist_report="never")
            self.assertTrue(r["changed"])
            self.assertEqual("stop_loss",json.loads(path.read_text())["instruments"][0]["exit_reason"])
            fetched.assert_not_called()

    def test_canonical_5m_profit_exit_preserves_sl_tp_and_other_instruments(self):
        p=position()
        btc={"instrument_id":"btcusd","trade_status":"no_trade","direction":"neutral"}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"2026-W48.json"
            path.write_text(json.dumps({"week_id":"2026-W48","instruments":[p,btc]}))
            with patch.object(risk,"canonical_live_risk_hit",return_value=None), \
                 patch.object(risk,"fetch_yahoo_bars",return_value=five_min()), \
                 patch.object(risk,"fetch_yahoo_completed_daily_closes",return_value=[]), \
                 patch.object(risk,"_eurusd_policy_cost",return_value=1.5), \
                 patch.object(risk.wes_outbox,"is_wes_week",return_value=False):
                result=risk.audit(path=path,now=NOW,persist_report="never")
            after=json.loads(path.read_text())["instruments"]
            self.assertTrue(result["changed"],result)
            self.assertEqual("wes_eurusd_peak_profit_giveback_exit",after[0]["exit_reason"])
            self.assertEqual(1.10602325,after[0]["risk_plan"]["stop_loss_price"])
            self.assertEqual(1.08191835,after[0]["risk_plan"]["take_profit_price"])
            self.assertEqual(btc,after[1])
            self.assertGreater(after[0]["result_units"],0)

    def test_no_history_retrofill_when_price_is_stale(self):
        p=position()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"2026-W48.json"
            path.write_text(json.dumps({"week_id":"2026-W48","instruments":[p]}))
            with patch.object(risk,"canonical_live_risk_hit",return_value=None), \
                 patch.object(risk,"fetch_yahoo_bars",return_value=five_min(stale=True)), \
                 patch.object(risk,"fetch_yahoo_completed_daily_closes",return_value=[]), \
                 patch.object(risk,"_eurusd_policy_cost",return_value=1.5), \
                 patch.object(risk.wes_outbox,"is_wes_week",return_value=False):
                result=risk.audit(path=path,now=NOW,persist_report="never")
            self.assertFalse(result["changed"])
            self.assertIsNone(json.loads(path.read_text())["instruments"][0]["exit_price"])


if __name__=="__main__":
    unittest.main()
