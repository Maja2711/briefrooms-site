"""Regression tests for as-of, evidence integrity and zero execution authority."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_exit_intelligence_v2 as x


T0 = datetime(2026, 10, 8, 9, 56, tzinfo=timezone.utc)


def bar(minute, close, high=None, low=None):
    return {"timestamp": x.iso(T0 + timedelta(minutes=minute)),
            "close": close, "high": close if high is None else high,
            "low": close if low is None else low}


def trade():
    return {"trade_id": "T1", "direction": "SHORT", "entry": 1.11867,
            "opened_at": x.iso(T0), "closed_at": x.iso(T0 + timedelta(minutes=150)),
            "exit_price": 1.11982, "exit_reason": "DYNAMIC_RISK_EXIT",
            "mfe_pips": 11.02, "r_multiple": -0.363, "outcome": "LOSS",
            "monitor": {"dynamic_exit": {"velocity_1h_rph": -0.5458, "giveback_r": 0.7106}}}


class ExitIntelligenceTests(unittest.TestCase):
    def test_no_lookahead_in_capture(self):
        pos = dict(trade(), status="OPEN")
        now = T0 + timedelta(minutes=20)
        candles = [bar(i, 1.11867 - i * 0.00005) for i in range(22)]
        snapshot = x.capture(pos, x.normalize_bars(candles, now), [], now)
        self.assertLessEqual(snapshot["market_bar_at"], snapshot["captured_at"])
        self.assertFalse(snapshot["signals"]["us10y_yield_falling_15m"])
        self.assertEqual(snapshot["yield_10y_proxy"]["status"], "UNAVAILABLE")
        self.assertIsNone(x.capture(pos, x.normalize_bars(candles, T0+timedelta(minutes=10)),
                                   [], now + timedelta(days=1)))

    def test_profit_and_reversal_signal_honest(self):
        pos = dict(trade(), status="OPEN")
        candles = [bar(i, 1.11867 - (min(i, 20)) * 0.00006 +
                       max(0, i-20) * 0.00010) for i in range(28)]
        now = T0 + timedelta(minutes=27)
        series = x.normalize_bars(candles, now)
        snapshot = x.capture(pos, series, [], now)
        self.assertTrue(snapshot["signals"]["profit_reached_11p"])
        self.assertTrue(snapshot["signals"]["momentum_5m_reversal"])
        self.assertTrue(snapshot["signals"]["peak_giveback_35pct_or_3p"])
        self.assertFalse(snapshot["exit_authority"])

    def test_no_signal_during_profit_must_remain_unknown(self):
        t = trade()
        r = x.review(t, [], [], T0 + timedelta(minutes=180))
        self.assertEqual(r["data_audit"]["evidence_of_actionable_profit_alert"],
                         "UNKNOWN_NO_PRE_EXIT_PROFIT_SNAPSHOTS")
        self.assertFalse(r["profit_protection"]["early_exit_proven"])
        self.assertEqual(r["post_exit_path"]["plus_1h"]["status"], "PENDING")

    def test_after_close_snapshots_are_not_live_evidence(self):
        t = trade()
        later = {"trade_id": "T1",
                 "captured_at": x.iso(T0 + timedelta(minutes=151)),
                 "market_bar_at": x.iso(T0 + timedelta(minutes=150)),
                 "current_indicative_pips": 11,
                 "best_favorable_mid_pips": 11,
                 "signals": {"momentum_5m_reversal": True}}
        r = x.review(t, [later], [], T0 + timedelta(minutes=180))
        self.assertEqual(r["data_audit"]["snapshots_before_exit"], 0)

    def test_pending_then_matured_future_path(self):
        t = trade()
        closed = x.parse_time(t["closed_at"])
        series = x.normalize_bars([bar(210, 1.12032)], T0 + timedelta(hours=5))
        before = x.review(t, [], series, closed + timedelta(minutes=30))
        self.assertEqual(before["post_exit_path"]["plus_1h"]["status"], "PENDING")
        after = x.review(t, [], series, closed + timedelta(hours=1, minutes=1))
        self.assertEqual(after["post_exit_path"]["plus_1h"]["status"], "RETROSPECTIVE_MID_PROXY")
        self.assertEqual(after["post_exit_path"]["plus_3h"]["status"], "PENDING")

    def test_delayed_entry_is_hindsight_not_execution(self):
        t = trade()
        series = x.normalize_bars([bar(5, 1.11890), bar(15, 1.11910), bar(30, 1.11920)],
                                  T0 + timedelta(hours=4))
        r = x.review(t, [], series, T0 + timedelta(hours=4))
        self.assertEqual(r["entry_timing"]["delay_5m"]["status"], "RETROSPECTIVE_MID_PROXY")
        self.assertIn("not_a_fill", r["entry_timing"]["delay_5m"]["warning"])

    def test_step_idempotent_and_never_changes_canonical_input(self):
        pos = dict(trade(), status="OPEN")
        spot = {"metadata": {"position": pos}}
        history = {"trades": [trade()]}
        original = copy.deepcopy((spot, history))
        now = T0 + timedelta(minutes=20)
        fx = [bar(i, 1.11867 - i * 0.00004) for i in range(21)]
        journal, reviews = x.step(spot, history, {}, {}, fx, [], now)
        next_journal, next_reviews = x.step(spot, history, journal, reviews, fx, [], now)
        self.assertEqual(journal["snapshots"], next_journal["snapshots"])
        self.assertEqual((spot, history), original)
        self.assertEqual(next_reviews["trading_decision_influence"], False)
        self.assertEqual(next_reviews["automatic_promotion"], False)

    def test_conservative_stop_precedes_tp_on_same_bar(self):
        t = trade()
        t["stop"], t["target"] = 1.12184, 1.11338
        fx = x.normalize_bars([
            bar(151, 1.119, high=1.122, low=1.113),
            bar(152, 1.120)
        ], T0 + timedelta(minutes=180))
        simulated = x.hold_counterfactual(t, fx, T0 + timedelta(minutes=152))
        self.assertEqual(simulated["status"], "SIMULATED_RISK_EXIT")
        self.assertEqual(simulated["reason"], "STOP_LOSS")
        self.assertFalse(simulated["execution_proven"])

    def test_horizon_requires_contiguous_bars(self):
        t = trade()
        fx = x.normalize_bars([bar(210, 1.1184)], T0 + timedelta(hours=5))
        result = x.hold_counterfactual(t, fx, T0 + timedelta(minutes=210))
        self.assertEqual(result["status"], "INSUFFICIENT_CONTIGUOUS_PATH")

    def test_journal_generated_at_does_not_change_when_idle(self):
        now = T0 + timedelta(minutes=180)
        j, r = x.step({}, {"trades": []}, {}, {}, [], [], now)
        j2, r2 = x.step({}, {"trades": []}, j, r, [], [], now + timedelta(minutes=5))
        self.assertEqual(j, j2)
        self.assertEqual(r, r2)

    def test_stale_rates_are_unavailable(self):
        now = T0 + timedelta(minutes=30)
        pos = dict(trade(), status="OPEN")
        fx = x.normalize_bars([bar(i, 1.11867) for i in range(31)], now)
        rates = x.normalize_bars([bar(0, 42.5), bar(15, 42.2)], now)
        s = x.capture(pos, fx, rates, now)
        self.assertEqual(s["yield_10y_proxy"]["status"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
