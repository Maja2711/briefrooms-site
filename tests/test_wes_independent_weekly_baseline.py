from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wes_independent_weekly_baseline as baseline


def at(day: int, hour: int, minute: int = 0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def week(direction="long"):
    # All times in UTC. 08:00 local October 12 = 06:00 UTC.
    row={"instrument_id":"sp500_futures", "symbol":"ES=F", "direction":direction,
         "score":54, "data_quality":"passed",
         "risk_distance":{"stop_price_distance":10.0, "take_price_distance":20.0}}
    window={"entry_target_local":"2026-10-12T06:00:00+00:00",
            "entry_latest_local":"2026-10-12T08:00:00+00:00",
            "exit_target_local":"2026-10-16T20:00:00+00:00"}
    return {"week_id":"2026-W42",
            "frozen_forecast":{"week_id":"2026-W42",
                               "forecast_locked_at":"2026-10-11T00:00:00+00:00",
                               "base_method_version":"2.0.0",
                               "market_window":window,"instruments":[row]},
            "instruments":[{"instrument_id":"sp500_futures", "trade_status":"no_trade",
                            "entry_price":None, "entry_captured_at":None, "position_legs":[]}]}


def policy():
    return {"instruments":[{"instrument_id":"sp500_futures","round_trip_cost":1.0,"cost_unit":"points"}]}


def candles(rows):
    return pd.DataFrame({"Open":[x[1] for x in rows],
                         "High":[x[2] for x in rows],
                         "Low":[x[3] for x in rows],
                         "Close":[x[4] for x in rows]},
                        index=pd.to_datetime([x[0] for x in rows],utc=True))


class IndependentWeeklyBaselineTests(unittest.TestCase):
    def test_frozen_weekly_baseline_is_separate_from_wes_live_fill(self):
        w=week()
        w["instruments"][0]["entry_price"]=9999
        w["instruments"][0]["entry_captured_at"]="2026-10-11T09:00:00+00:00"
        state=baseline.capture(baseline.empty(),w,at(11,12),policy())
        row=state["weeks"]["2026-W42"]
        self.assertEqual(row["status"],"captured")
        self.assertEqual(row["plan"]["direction"],"long")
        self.assertNotIn("entry_price",row["plan"])
        self.assertFalse(state["execution_authority"])
        self.assertFalse(state["automatic_promotion"])
        self.assertFalse(state["historical_backfill_allowed"])

    def test_first_capture_is_immutable(self):
        original=baseline.capture(baseline.empty(),week(),at(11,12),policy())
        changed=week("short")
        changed["frozen_forecast"]["instruments"][0]["risk_distance"]["stop_price_distance"]=999
        new=baseline.capture(original,changed,at(11,13),policy())
        self.assertEqual(original,new)

    def test_late_capture_is_missed_not_reconstructed(self):
        out=baseline.capture(baseline.empty(),week(),at(12,9),policy())
        row=out["weeks"]["2026-W42"]
        self.assertEqual(row["status"],"not_eligible")
        self.assertNotIn("plan",row)
        out2=baseline.capture(out,week(),at(12,10),policy())
        self.assertEqual(out2,out)

    def test_verified_virtual_entry_then_stop_first(self):
        w=week()
        st=baseline.capture(baseline.empty(),w,at(11,12),policy())
        bars=candles([
            ("2026-10-12T06:00:00Z",100,101,99,100),
            ("2026-10-12T06:05:00Z",100,121,89,110),
        ])
        pending=baseline.evaluate(st,{"2026-W42":w},at(12,6,4),
                                  bars_by_week={"2026-W42":bars})
        self.assertEqual(pending["weeks"]["2026-W42"]["outcome"]["status"],"pending_market_data")
        result=baseline.evaluate(st,{"2026-W42":w},at(12,6,15),
                                 bars_by_week={"2026-W42":bars})
        outcome=result["weeks"]["2026-W42"]["outcome"]
        self.assertEqual(outcome["virtual_fill"]["entry_price"],100)
        self.assertEqual(outcome["exit_reason"],"stop_loss")
        self.assertEqual(outcome["net_result_percent"],-11)
        self.assertEqual(outcome["wes_comparison"]["status"],"pending")

    def test_no_trade_week_is_zero_but_only_after_deadline(self):
        w=week("neutral")
        st=baseline.capture(baseline.empty(),w,at(11,12),policy())
        pending=baseline.evaluate(st,{"2026-W42":w},at(12,10))
        self.assertEqual(pending["weeks"]["2026-W42"]["outcome"]["status"],"pending")
        resolved=baseline.evaluate(st,{"2026-W42":w},at(16,21))
        row=resolved["weeks"]["2026-W42"]["outcome"]
        self.assertEqual(row["status"],"resolved")
        self.assertEqual(row["net_result_percent"],0)
        self.assertEqual(row["wes_comparison"]["net_result_percent"],0)
        self.assertEqual(baseline.report(resolved)["sample"]["resolved_weekly_comparisons"],1)

    def test_one_canonical_closed_leg_settles_comparison(self):
        w=week("neutral")
        w["instruments"][0]["position_legs"]=[{
            "entry_captured_at":"2026-10-12T08:00:00Z",
            "exit_captured_at":"2026-10-13T09:00:00Z",
            "net_result_percent":2.0}]
        st=baseline.capture(baseline.empty(),w,at(11,12),policy())
        out=baseline.evaluate(st,{"2026-W42":w},at(16,21))
        entry=out["weeks"]["2026-W42"]["outcome"]["wes_comparison"]
        self.assertEqual(entry["incremental_weekly_net_percent"],2)
        self.assertEqual(baseline.report(out)["sample"]["resolved_weekly_comparisons"],1)

    def test_two_legs_not_silently_aggregated(self):
        w=week("neutral")
        w["instruments"][0]["position_legs"]=[
            {"net_result_percent":1,"exit_captured_at":"2026-10-13T09:00:00Z"},
            {"net_result_percent":2,"exit_captured_at":"2026-10-14T09:00:00Z"}]
        st=baseline.capture(baseline.empty(),w,at(11,12),policy())
        out=baseline.evaluate(st,{"2026-W42":w},at(16,21))
        self.assertEqual(out["weeks"]["2026-W42"]["outcome"]["wes_comparison"]["status"],"not_comparable")
        self.assertEqual(baseline.report(out)["sample"]["resolved_weekly_comparisons"],0)


if __name__ == "__main__":
    unittest.main()
