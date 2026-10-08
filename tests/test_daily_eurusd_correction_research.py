"""Downtrend correction research: no future leak, censored events, min sample & risk isolation."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_correction_research as c
from scripts import daily_eurusd_exit_intelligence_v2 as exits

T0 = datetime(2026, 10, 8, 8, 0, tzinfo=timezone.utc)


def bar(minutes, price):
    return {"time": T0 + timedelta(minutes=minutes), "close": price,
            "high": price, "low": price}


def falling_and_rebounding():
    # 20-minute warmup, downward impulse 12 pips, then ~4 pip correction.
    rows = [bar(i, 1.11990) for i in range(20)]
    rows += [bar(20, 1.12000)]
    rows += [bar(20+i, 1.12000-i*0.0001) for i in range(1,13)]
    rows += [bar(33, 1.11910), bar(34, 1.11920)]
    return rows


class DowntrendCorrectionTests(unittest.TestCase):
    def test_as_of_warning_precedes_confirmation_not_future(self):
        rows = falling_and_rebounding()
        t31 = T0 + timedelta(minutes=31)
        signals = c.current_context(rows, t31)
        self.assertEqual(signals["confirmed_event_count_as_of"], 0)
        self.assertTrue(signals["research_warning_flags"]["downmove_at_least_10p"])
        self.assertEqual(signals["current_downswing"]["decline_from_high_pips"], 11)
        self.assertIsNone(signals["historical_median_fall_pips"])
        self.assertTrue(signals["research_only"])
        # No inference about an unfinished future 12-pip drawdown.
        self.assertFalse(signals["research_warning_flags"]["bounce_from_low_at_least_2p"])

    def test_correction_trough_is_only_known_after_confirmed_close(self):
        rows = falling_and_rebounding()
        events, state = c.scan_swings(rows, T0 + timedelta(minutes=33))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["fall_pips"], 12)
        self.assertEqual(event["fall_duration_minutes"], 12)
        self.assertEqual(event["confirmation_delay_minutes"], 1)
        self.assertTrue(event["valid_for_statistics"])
        self.assertEqual(event["confirmed_at"], c._iso(T0 + timedelta(minutes=33)))
        self.assertEqual(event["known_at"], c._iso(T0 + timedelta(minutes=34)))
        self.assertEqual(
            c.journal_study({}, rows, T0 + timedelta(minutes=33))["confirmed_events"], 0
        )
        self.assertEqual(
            c.journal_study({}, rows, T0 + timedelta(minutes=34))["confirmed_events"], 1
        )

    def test_no_duplicate_count_in_event_archive(self):
        rows = falling_and_rebounding()
        now = T0 + timedelta(minutes=35)
        study = c.journal_study({}, rows, now)
        repeated = c.journal_study(study, rows, now)
        self.assertEqual(study, repeated)
        self.assertEqual(study["confirmed_events"], 1)
        self.assertFalse(study["exit_authority"])

    def test_market_gap_does_not_form_fake_impulse(self):
        rows = [bar(i, 1.12100) for i in range(25)]
        rows += [bar(i, 1.11500) for i in range(100, 121)]
        out = c.current_context(rows, T0 + timedelta(minutes=120))
        self.assertEqual(out["confirmed_event_count_as_of"], 0)
        self.assertEqual(out["current_downswing"]["decline_from_high_pips"], 0)

    def test_avoid_fake_statistical_confidence(self):
        events = []
        for i in range(20):
            events.append({
                "event_id": f"event{i}", "valid_for_statistics": True,
                "confirmed_at": c._iso(T0 + timedelta(minutes=i)),
                "known_at": c._iso(T0 + timedelta(minutes=i+1)),
                "fall_pips": 10.0+i/2, "fall_duration_minutes": 15.0+i,
                "confirmation_delay_minutes": 2.0,
                "session_utc": "UTC_06_12",
                "size_bucket": c._bucket(10.0+i/2)
            })
        small = c.summarize_events(events[:19])
        self.assertEqual(small["all"]["status"], "INSUFFICIENT_SAMPLE")
        self.assertIsNone(small["all"]["median_fall_pips"])
        full = c.summarize_events(events)
        self.assertEqual(full["all"]["status"], "DESCRIPTIVE_SAMPLE")
        self.assertIsNotNone(full["all"]["median_fall_duration_minutes"])
        self.assertNotIn("probability_of_correction", full)
        self.assertEqual(full["by_utc_session"]["UTC_12_20"]["status"], "INSUFFICIENT_SAMPLE")

    def test_capture_links_downtrend_state_without_exit_rights(self):
        rows = falling_and_rebounding()
        at_time = T0 + timedelta(minutes=32)
        fx = exits.normalize_bars([
            {"timestamp": c._iso(b["time"]), "close": b["close"],
             "high": b["high"], "low": b["low"]} for b in rows
        ], at_time)
        position = {
            "trade_id": "T1", "status": "OPEN", "direction": "SHORT",
            "opened_at": c._iso(T0), "entry": 1.12000
        }
        original = copy.deepcopy(position)
        snapshot = exits.capture(position, fx, [], at_time)
        self.assertTrue(snapshot["signals"]["correction_downmove_at_least_10p"])
        self.assertFalse(snapshot["exit_authority"])
        self.assertTrue(snapshot["downswing_correction"]["research_only"])
        self.assertEqual(position, original)
        self.assertNotIn("SL", str(snapshot))
        # Absence of historical confirmed corrections cannot equal low risk.
        self.assertTrue(snapshot["downswing_correction"]["no_predicted_correction_probability"])

    def test_long_does_not_receive_short_correction_exit_flags(self):
        fx = exits.normalize_bars([
            {"timestamp": c._iso(b["time"]), "close": b["close"],
             "high": b["high"], "low": b["low"]} for b in falling_and_rebounding()
        ], T0 + timedelta(minutes=32))
        snapshot = exits.capture({
            "trade_id": "LONG", "direction": "LONG", "opened_at": c._iso(T0),
            "entry": 1.12
        }, fx, [], T0 + timedelta(minutes=32))
        self.assertFalse(any(k.startswith("correction_") for k in snapshot["signals"]))
        self.assertFalse(snapshot["exit_authority"])

    def test_downtrend_reference_requires_asof_downtrend_at_trough(self):
        # A 70-minute sell-off after a high established beyond warmup.
        bars = [bar(i, 1.13000) for i in range(65)]
        bars += [bar(65, 1.13050)]
        bars += [bar(65+i, 1.13050 - i * 0.00003) for i in range(1, 71)]
        trough = T0 + timedelta(minutes=135)
        before = c.current_context(bars, trough)
        self.assertEqual(before["confirmed_event_count_as_of"], 0)
        self.assertTrue(before["current_downswing"]["trend_1m"]["local_downtrend"])
        # Correction 7 pips; 25% of 21-pip sell-off is 5.25 pips.
        bars += [bar(136, 1.12910)]
        events, _ = c.scan_swings(bars, T0 + timedelta(minutes=136))
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["local_downtrend_at_trough"])
        self.assertTrue(events[0]["valid_for_statistics"])
        study = c.journal_study({}, bars, T0 + timedelta(minutes=137))
        self.assertEqual(study["summary"]["in_confirmed_local_downtrend"]["confirmed_event_count"], 1)
        self.assertEqual(study["summary"]["in_confirmed_local_downtrend"]["status"], "INSUFFICIENT_SAMPLE")
        self.assertIsNone(study["summary"]["in_confirmed_local_downtrend"]["median_fall_pips"])
        after = c.current_context(bars, T0 + timedelta(minutes=137), study["events"])
        self.assertEqual(after["conditional_reference"], "LOCAL_1M_DOWNTREND_ONLY")
        self.assertEqual(after["conditional_sample_count"], 0 if
                         after["current_downswing"]["trend_1m"]["local_downtrend"] is not True else 1)
        self.assertFalse(after["higher_timeframe_daily_trend_confirmed"])

    def test_unknown_trend_is_not_used_as_downtrend_evidence(self):
        bars = falling_and_rebounding()
        study = c.journal_study({}, bars, T0 + timedelta(minutes=35))
        self.assertEqual(study["confirmed_events"], 1)
        self.assertEqual(study["summary"]["in_confirmed_local_downtrend"]["confirmed_event_count"], 0)
        self.assertEqual(study["summary"]["local_trend_unknown_count"], 1)

    def test_no_false_correction_count_when_bars_missing(self):
        study = c.journal_study({}, [], T0)
        self.assertEqual(study["confirmed_events"], 0)
        self.assertEqual(study["summary"]["all"]["status"], "INSUFFICIENT_SAMPLE")


if __name__ == "__main__":
    unittest.main()
