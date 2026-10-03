from __future__ import annotations

import sys
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_core import BeliefCore  # noqa: E402
from belief_adapter_contract import AdapterResult, Observation  # noqa: E402
from belief_core_live import (  # noqa: E402
    AUTOMATIC_TUNING_ENABLED,
    POLICY_OUTPUT_ENABLED,
    TRADE_EXECUTION_ENABLED,
    Bar,
    advance_us_regular_session_equivalent,
    due_planned_slot,
    evaluate_spec,
    floor_half_hour,
    forecast_contract_metadata,
    horizon_target_plan,
    in_fx_window,
    next_weekday_close,
    production_probability,
    run_cycle,
    strength_from_return,
    target_values,
    weekly_target,
)

NY = ZoneInfo("America/New_York")


class CountingTargetClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = {}

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m"):
        key = (symbol, range_, interval)
        self.calls[key] = self.calls.get(key, 0) + 1
        return list(self.rows[symbol])


class StubSnapshot:
    def __init__(self, values):
        self.values = values

    def latest(self, symbol):
        return self.values[symbol]


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

    def test_fx_window_covers_sunday_open_through_friday_close(self) -> None:
        self.assertTrue(in_fx_window(datetime(2026, 10, 4, 17, 1, tzinfo=NY)))
        self.assertTrue(in_fx_window(datetime(2026, 10, 6, 3, 0, tzinfo=NY)))
        self.assertTrue(in_fx_window(datetime(2026, 10, 9, 16, 59, tzinfo=NY)))
        self.assertFalse(in_fx_window(datetime(2026, 10, 9, 17, 1, tzinfo=NY)))
        self.assertFalse(in_fx_window(datetime(2026, 10, 10, 12, 0, tzinfo=NY)))

    def test_forecast_slot_uses_market_phase_without_backfill(self) -> None:
        planned=time(10,0)
        self.assertTrue(due_planned_slot(datetime(2026,8,18,10,7,tzinfo=NY),planned,False))
        self.assertTrue(due_planned_slot(datetime(2026,8,18,12,59,tzinfo=NY),planned,False))
        self.assertFalse(due_planned_slot(datetime(2026,8,18,13,0,tzinfo=NY),planned,False))
        self.assertFalse(due_planned_slot(datetime(2026,8,18,10,7,tzinfo=NY),planned,True))

        afternoon=time(13,0)
        self.assertTrue(due_planned_slot(datetime(2026,8,18,13,51,tzinfo=NY),afternoon,False))
        self.assertTrue(due_planned_slot(datetime(2026,8,18,15,59,tzinfo=NY),afternoon,False))
        self.assertFalse(due_planned_slot(datetime(2026,8,18,16,0,tzinfo=NY),afternoon,False))

        close=time(16,0)
        self.assertTrue(due_planned_slot(datetime(2026,8,18,16,19,tzinfo=NY),close,False))
        self.assertTrue(due_planned_slot(datetime(2026,8,18,16,20,tzinfo=NY),close,False))
        self.assertFalse(due_planned_slot(datetime(2026,8,18,16,21,tzinfo=NY),close,False))

    def test_us_session_equivalent_24h_targets_next_session_same_phase(self) -> None:
        late=datetime(2026,9,28,16,3,30,tzinfo=NY)
        target=advance_us_regular_session_equivalent(late,24)
        self.assertEqual(target.date().isoformat(),"2026-09-29")
        self.assertEqual(target.time(),time(16,0))

        midday=datetime(2026,9,28,13,3,tzinfo=NY)
        target=advance_us_regular_session_equivalent(midday,24)
        self.assertEqual(target.date().isoformat(),"2026-09-29")
        self.assertEqual(target.time(),time(13,3))

    def test_us_session_equivalent_skips_weekend(self) -> None:
        friday=datetime(2026,10,2,16,3,tzinfo=NY)
        target=advance_us_regular_session_equivalent(friday,24)
        self.assertEqual(target.weekday(),0)
        self.assertEqual(target.date().isoformat(),"2026-10-05")
        self.assertEqual(target.time(),time(16,0))

    def test_horizon_target_plan_separates_clock_and_us_session_contracts(self) -> None:
        when=datetime(2026,9,28,16,3,30,tzinfo=NY)
        btc=horizon_target_plan({"kind":"price_above","symbol":"BTC-USD","reference":1.0},when,24)
        uup=horizon_target_plan({"kind":"value_below","symbol":"UUP","reference":1.0,"threshold":1.0},when,24)
        self.assertEqual(btc["basis"],"ELAPSED_TIME")
        self.assertEqual(btc["label"],"24H")
        self.assertEqual(btc["target"],when+timedelta(hours=24))
        self.assertEqual(uup["basis"],"US_REGULAR_SESSION_EQUIVALENT")
        self.assertEqual(uup["label"],"1S")
        self.assertEqual(uup["calibration_bucket"],"1S_US_SESSION")
        self.assertEqual(uup["target"].astimezone(NY).time(),time(16,0))
        self.assertEqual(uup["session_equivalent_count"],1.0)

    def test_global_production_overlay_is_applied_without_touching_raw_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path=Path(tmp)/"policy.json"
            policy_path.write_text(json.dumps({
                "overrides":{
                    "__GLOBAL__":{
                        "active":True,
                        "belief_id":"__GLOBAL__",
                        "version":"global-test",
                        "transform":{"type":"logit_affine_v1","intercept":0.0,"slope":0.0},
                    }
                }
            }))
            with patch("belief_core_live.PRODUCTION_POLICY_PATH", policy_path):
                p, overlay=production_probability("spx.trend.bullish", .80)
            self.assertAlmostEqual(p,.50)
            self.assertEqual(overlay["version"],"global-test")

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

    def test_target_values_uses_first_bar_after_target_and_shared_cache(self) -> None:
        utc=ZoneInfo("UTC")
        target=datetime(2026,9,29,20,3,30,tzinfo=utc)
        now=target+timedelta(minutes=40)
        client=CountingTargetClient({
            "BTC-USD":[
                Bar(timestamp=datetime(2026,9,29,20,0,tzinfo=utc),close=100.0),
                Bar(timestamp=datetime(2026,9,29,20,5,tzinfo=utc),close=101.0),
                Bar(timestamp=datetime(2026,9,29,20,10,tzinfo=utc),close=102.0),
            ]
        })
        cache={}
        trend={"kind":"price_above","symbol":"BTC-USD","reference":99.0}
        vol={"kind":"absolute_return_below","symbol":"BTC-USD","reference":100.0,"threshold_return":.05}
        first=target_values(client,trend,target,now,None,cache)
        second=target_values(client,vol,target,now,None,cache)
        self.assertEqual(first,{"BTC-USD":101.0})
        self.assertEqual(second,{"BTC-USD":101.0})
        self.assertEqual(client.calls[("BTC-USD","5d","5m")],1)

    def test_forecast_calendar_depends_on_settlement_inputs_not_instrument_name(self) -> None:
        utc=ZoneInfo("UTC")
        when=datetime(2026,9,28,20,3,tzinfo=utc)
        target=when+timedelta(hours=24)
        snapshot=StubSnapshot({"BTC-USD":83379.0,"UUP":28.69,"EURUSD=X":1.137})
        btc_price=forecast_contract_metadata(
            snapshot,"BTC-USD",
            {"kind":"price_above","symbol":"BTC-USD","reference":83379.0},
            when,target,24,
        )
        btc_usd_proxy=forecast_contract_metadata(
            snapshot,"BTC-USD",
            {"kind":"value_below","symbol":"UUP","reference":28.69,"threshold":28.69},
            when,target,24,
        )
        eurusd_price=forecast_contract_metadata(
            snapshot,"EURUSD=X",
            {"kind":"price_above","symbol":"EURUSD=X","reference":1.137},
            when,target,24,
        )
        self.assertEqual(btc_price["market_calendar"],"24/7")
        self.assertAlmostEqual(btc_price["settlement_max_delay_hours"],.333333)
        self.assertEqual(btc_usd_proxy["market_calendar"],"tradable_session_first_available")
        self.assertEqual(btc_usd_proxy["settlement_max_delay_hours"],72)
        self.assertEqual(eurusd_price["market_calendar"],"fx_24x5")

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

    def test_eurusd_evidence_refreshes_outside_us_cash_session(self) -> None:
        now = datetime(2026, 10, 6, 3, 7, tzinfo=NY)

        class FxClient:
            def __init__(self):
                self.rows = {}
                starts = {"EURUSD=X": 1.1200, "UUP": 28.0, "TLT": 90.0}
                steps = {"EURUSD=X": .00002, "UUP": -.001, "TLT": .01}
                for symbol, start in starts.items():
                    rows = []
                    for i in range(90):
                        ts = now - timedelta(minutes=30 * (89 - i))
                        rows.append(Bar(timestamp=ts.astimezone(ZoneInfo("UTC")), close=start + steps[symbol] * i))
                    self.rows[symbol] = rows

            def bars(self, symbol: str, range_: str = "10d", interval: str = "30m"):
                if symbol not in self.rows:
                    raise RuntimeError(symbol)
                return list(self.rows[symbol])

        class FakeCalendar:
            def run(self, when):
                class Result:
                    observations = ()
                    evidence = ()
                return Result()

            def source_status(self):
                return {
                    "schema_version": "macro-calendar-coverage-v1",
                    "required_sources": ["BLS", "BEA", "FOMC", "EUROSTAT", "ECB"],
                    "sources": {
                        key: {"status": "ok"}
                        for key in ("BLS", "BEA", "FOMC", "EUROSTAT", "ECB")
                    },
                    "complete": True,
                }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("belief_core_live.MacroEventCalendarAdapter", FakeCalendar):
                status = run_cycle(Path(tmp) / "core", now, FxClient())
            live = status["eurusd_24x5_liveness"]
            self.assertTrue(live["attempted"])
            self.assertEqual(live["status"], "ok")
            self.assertGreaterEqual(live["evidence"], 1)
            self.assertIn("EURUSD=X", live["symbols"])

    def test_end_to_end_1007_shadow_cycle_is_retry_idempotent(self) -> None:
        now=datetime(2026,8,18,10,7,tzinfo=NY)
        with tempfile.TemporaryDirectory() as tmp:
            state_dir=Path(tmp)/"core"
            client=FakeChartClient(now)
            status=run_cycle(state_dir,now,client)
            self.assertEqual(status["mode"],"shadow")
            self.assertEqual(status["observations_collected"],113)
            self.assertEqual(status["evidence_ingested"],24)
            self.assertEqual(status["world_state_snapshots"],1)
            self.assertEqual(status["shared_forecasts_frozen"],5)
            self.assertEqual(status["wes_forecasts_frozen"],0)
            self.assertEqual(status["v3_candidate_forecasts_frozen"],2)
            self.assertEqual(status["v3_ready_candidate_count"],4)
            self.assertEqual(status["forecasts_verified"],0)
            observations_path=state_dir/"observations.jsonl"
            self.assertTrue(observations_path.exists())
            self.assertEqual(len(observations_path.read_text().splitlines()),113)

            retry=run_cycle(state_dir,now,client)
            self.assertEqual(retry["observations_collected"],0)
            self.assertEqual(retry["world_state_snapshots"],0)
            self.assertEqual(retry["shared_forecasts_frozen"],0)
            self.assertEqual(retry["v3_candidate_forecasts_frozen"],0)
            self.assertEqual(len(observations_path.read_text().splitlines()),113)

            core=BeliefCore(state_dir)
            self.assertEqual(len(core.forecasts),7)
            self.assertEqual(len(core.evidence),24)
            self.assertTrue(core.verify_ledger_integrity()["valid"])
            dashboard=core.dashboard_snapshot(now)
            self.assertFalse(dashboard["controls"]["trade_execution_enabled"])
            self.assertFalse(dashboard["controls"]["policy_output_enabled"])

    def test_fx_week_calendar_liveness_appends_fresh_coverage_when_enabled(self) -> None:
        now=datetime(2026,8,18,10,7,tzinfo=NY)

        class FakeCalendarAdapter:
            def run(self, current):
                observation=Observation.make(
                    adapter="macro_event_calendar",
                    metric="calendar_coverage",
                    entity="GLOBAL_MACRO",
                    observed_at=current.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00","Z"),
                    value={"complete":True},
                    unit="coverage_status",
                    source="test calendar",
                    source_type="derived",
                    source_ref="test://calendar",
                    reliability=1.0,
                    independence_cluster="test:calendar",
                    metadata={"complete":True,"sources":{"BLS":{"status":"ok"},"BEA":{"status":"ok"},"FOMC":{"status":"ok"},"EUROSTAT":{"status":"ok"},"ECB":{"status":"ok"}}},
                )
                return AdapterResult("macro_event_calendar",(observation,),())

        with tempfile.TemporaryDirectory() as tmp:
            state_dir=Path(tmp)/"core"
            with patch.dict("os.environ", {"BELIEF_EURUSD_CALENDAR_LIVENESS":"1"}, clear=False):
                with patch("belief_core_live.MacroEventCalendarAdapter", FakeCalendarAdapter):
                    status=run_cycle(state_dir,now,FakeChartClient(now))
            calendar=status["eurusd_calendar_liveness"]
            self.assertTrue(calendar["attempted"])
            self.assertEqual(calendar["status"],"ok")
            rows=[json.loads(x) for x in (state_dir/"observations.jsonl").read_text().splitlines()]
            self.assertTrue(any(x.get("metric")=="calendar_coverage" for x in rows))


if __name__ == "__main__":
    unittest.main()
