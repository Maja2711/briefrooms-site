from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_macro_event_risk as risk

UTC = timezone.utc
OPENED = datetime(2026, 10, 2, 9, 12, tzinfo=UTC)


def position() -> dict:
    return {
        "schema_version": "eurusd-daily-position-v1",
        "trade_id": "macro-risk-short",
        "status": "OPEN",
        "direction": "SHORT",
        "opened_at": OPENED.isoformat().replace("+00:00", "Z"),
        "expires_at": (OPENED + timedelta(hours=27)).isoformat().replace("+00:00", "Z"),
        "entry": 1.12509,
        "stop": 1.12821,
        "target": 1.11970,
        "entry_score": 39.3,
        "entry_confidence": 0.214,
        "entry_components": {},
        "entry_weights": {},
        "engine_version": "test",
        "execution_price_engine": {
            "synthetic_spread_pips": 1.5,
            "synthetic_half_spread_pips": 0.75,
        },
    }


def bar(minutes: int, close: float, *, high: float | None = None, low: float | None = None) -> Bar:
    return Bar(
        timestamp=OPENED + timedelta(minutes=minutes),
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
    )


class DailyEURUSDMacroEventRiskTests(unittest.TestCase):
    def test_pre_event_asymmetry_can_protect_large_profit(self):
        p = position()
        p["_management_candidate"] = {"direction": "SHORT", "score": 32.1}
        p["_belief_macro_context"] = {
            "available": False,
            "score": 0.0,
            "macro_calendar": {
                "events": [{
                    "title": "Employment Situation",
                    "event_at": "2026-10-02T12:30:00Z",
                    "hours_until": 0.15,
                    "source": "U.S. Bureau of Labor Statistics",
                }]
            },
        }
        bars = [
            bar(0, 1.12509),
            bar(180, 1.12280, low=1.12260),
            bar(189, 1.12279),
        ]
        trade = risk.maybe_close_position(p, bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "MACRO_EVENT_ASYMMETRY_EXIT")
        self.assertGreater(trade["r_multiple"], 0.5)

    def test_post_event_giveback_plus_flat_candidate_invalidates_short(self):
        p = position()
        p["_management_candidate"] = {"direction": "FLAT", "score": 45.81}
        p["_belief_macro_context"] = {
            "available": False,
            "score": 0.0,
            "macro_calendar": {
                "events": [{
                    "title": "Employment Situation",
                    "event_at": "2026-10-02T12:30:00Z",
                    "hours_until": -0.05,
                    "source": "U.S. Bureau of Labor Statistics",
                }]
            },
        }
        bars = [
            bar(0, 1.12509),
            bar(180, 1.12260, low=1.12245),
            bar(201, 1.12582, high=1.12595),
        ]
        trade = risk.maybe_close_position(p, bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "MACRO_EVENT_THESIS_INVALIDATION")
        self.assertLess(trade["r_multiple"], 0.0)

    def test_fresh_belief_conflict_has_immediate_close_authority(self):
        p = position()
        p["_management_candidate"] = {"direction": "SHORT", "score": 37.0}
        p["_belief_macro_context"] = {
            "available": True,
            "score": 7.5,
            "macro_calendar": {"events": []},
        }
        bars = [bar(0, 1.12509), bar(30, 1.12520)]
        trade = risk.maybe_close_position(p, bars, bars[-1].timestamp)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "BELIEF_MACRO_THESIS_INVALIDATION")


if __name__ == "__main__":
    unittest.main()
