from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_core import BeliefCore  # noqa: E402
from belief_core_live import (  # noqa: E402
    AUTOMATIC_TUNING_ENABLED,
    POLICY_OUTPUT_ENABLED,
    TRADE_EXECUTION_ENABLED,
    Bar,
    due_planned_slot,
    evaluate_spec,
    floor_half_hour,
    next_weekday_close,
    run_cycle,
    strength_from_return,
    weekly_target,
)

NY = ZoneInfo("America/New_York")


class FakeChartClient:
    def __init__(self, now: datetime) -> None:
        end = now.astimezone(NY).replace(minute=0, second=0, microsecond=0)
        starts = {"SPY":100.0,"RSP":50.0,"IWM":200.0,"^VIX":18.0,"HYG":80.0,"LQD":100.0,"TLT":90.0,"UUP":25.0}
        steps = {"SPY":.10,"RSP":.06,"IWM":.25,"^VIX":-.01,"HYG":.02,"LQD":.005,"TLT":.01,"UUP":-.001}
        self.rows = {}
        for symbol, start in starts.items():
            bars = []
            for i in range(80):
                timestamp = end - timedelta(minutes=30 * (79 - i))
                bars.append(Bar(timestamp=timestamp.astimezone(ZoneInfo("UTC")), close=start + steps[symbol] * i))
            self.rows[symbol] = bars

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m"):
        return list(self.rows[symbol])


class BeliefCoreLiveTest(unittest.TestCase):
    def test_safety_flags_are_hard_off(self) -> None:
        self.assertFalse(TRADE_EXECUTION_ENABLED)
        self.assertFalse(POLICY_OUTPUT_ENABLED)
        self.assertFalse(AUTOMATIC_TUNING_ENABLED)

    def test_floor_half_hour(self) -> None:
        self.assertEqual(floor_half_hour(datetime(2026,8,18,10,7,tzinfo=NY)).time(), time(10,0))
        self.assertEqual(floor_half_hour(datetime(2026,8,18,10,37,tzinfo=NY)).time(), time(10,30))

    def test_forecast_slot_has_bounded_grace_and_no_backfill(self) -> None:
        planned=time(10,0)
        self.assertTrue(due_planned_slot(datetime(2026,8,18,10,7,tzinfo=NY),planned,False))
        self.assertTrue(due_planned_slot(datetime(2026,8,18,10,44,tzinfo=NY),planned,False))
        self.assertFalse(due_planned_slot(datetime(2026,8,18,10,45,tzinfo=NY),planned,False))
        self.assertFalse(due_planned_slot(datetime(2026,8,18,10,7,tzinfo=NY),planned,True))

    def test_next_weekday_close_skips_weekend(self) -> None:
        target=next_weekday_close(datetime(2026,8,21,16,7,tzinfo=NY))
        self.assertEqual(target.weekday(),0)
        self.assertEqual(target.date().isoformat(),"2026-08-24")
        self.assertEqual(target.time(),time(16,0))

    def test_weekly_target_is_next_friday_same_clock(self) -> None:
        target=weekly_target(datetime(2026,8,21,16,7,tzinfo=NY))
        self.assertEqual(target.date().isoformat(),"2026-08-28")
        self.assertEqual(target.time(),time(16,0))

    def test_strength_is_bounded(self) -> None:
        self.assertGreaterEqual(strength_from_return(.0001,.01),0.0)
        self.assertLessEqual(strength_from_return(.50,.01),1.0)

    def test_price_outcome(self) -> None:
        spec={"kind":"price_above","symbol":"SPY","reference":100.0}
        self.assertTrue(evaluate_spec(spec,{"SPY":101.0}))
        self.assertFalse(evaluate_spec(spec,{"SPY":99.0}))

    def test_ratio_outcome(self) -> None:
        spec={"kind":"ratio_above","numerator":"RSP","denominator":"SPY","reference":.20}
        self.assertTrue(evaluate_spec(spec,{"RSP":21.0,"SPY":100.0}))
        self.assertFalse(evaluate_spec(spec,{"RSP":19.0,"SPY":100.0}))

    def test_volatility_dynamic_cap(self) -> None:
        spec={"kind":"value_below","symbol":"^VIX","reference":18.0,"threshold":20.0}
        self.assertTrue(evaluate_spec(spec,{"^VIX":19.0}))
        self.assertFalse(evaluate_spec(spec,{"^VIX":21.0}))

    def test_financial_conditions_majority(self) -> None:
        spec={"kind":"majority_supportive","reference":{"TLT":100.0,"HYG":80.0,"UUP":25.0}}
        self.assertTrue(evaluate_spec(spec,{"TLT":101.0,"HYG":81.0,"UUP":26.0}))
        self.assertFalse(evaluate_spec(spec,{"TLT":99.0,"HYG":79.0,"UUP":24.0}))

    def test_end_to_end_1007_shadow_cycle(self) -> None:
        now=datetime(2026,8,18,10,7,tzinfo=NY)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir=Path(tmp)/"core"
            status=run_cycle(state_dir,now,FakeChartClient(now))
            self.assertEqual(status["mode"],"shadow")
            self.assertEqual(status["observations_collected"],108)
            self.assertEqual(status["evidence_ingested"],9)
            self.assertEqual(status["world_state_snapshots"],1)
            self.assertEqual(status["shared_forecasts_frozen"],5)
            self.assertEqual(status["wes_forecasts_frozen"],0)
            self.assertEqual(status["forecasts_verified"],0)
            self.assertTrue((state_dir/"observations.jsonl").exists())
            core=BeliefCore(state_dir)
            self.assertEqual(len(core.forecasts),5)
            self.assertEqual(len(core.evidence),9)
            self.assertTrue(core.verify_ledger_integrity()["valid"])
            dashboard=core.dashboard_snapshot(now)
            self.assertFalse(dashboard["controls"]["trade_execution_enabled"])
            self.assertFalse(dashboard["controls"]["policy_output_enabled"])


if __name__ == "__main__":
    unittest.main()
