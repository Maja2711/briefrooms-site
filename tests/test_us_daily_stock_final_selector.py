from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from scripts import us_daily_stock as us
from scripts import us_daily_stock_final_selector as final

NY = ZoneInfo("America/New_York")


def cfg():
    return {
        "policy_version": "test",
        "analysis_not_before": "09:35",
        "publication_cutoff": "09:45",
        "minimum_data_completeness": .5,
        "minimum_median_turnover_usd": 25_000_000,
        "target_score": 72,
        "minimum_reward_risk": 1.5,
        "maximum_risk_percent": .07,
        "top_candidates_for_news": 2,
        "weights": {"catalyst":25,"relative_momentum":20,"volume_liquidity":15,"market_context":15,"risk_reward":15,"historical_expectancy":10},
        "learning": {"minimum_resolved_trades_for_adaptation": 8},
        "non_session_dates": [],
        "universe": [
            {"symbol":"AAA","name":"AAA Corp","sector":"technology"},
            {"symbol":"BBB","name":"BBB Corp","sector":"financials"},
        ],
    }


def bars_ending(day: date, *, drift=.0015, volume=2_000_000):
    rows=[]; price=100.0; cursor=day-timedelta(days=120)
    while cursor <= day:
        if cursor.weekday() < 5:
            price *= 1+drift
            rows.append(us.Bar(cursor, price*.995, price*1.01, price*.99, price, volume))
        cursor += timedelta(days=1)
    return rows[-75:]


class UsDailyFinalSelectorTests(unittest.TestCase):
    def test_one_session_historical_lag_is_usable_and_best_valid_is_selected(self):
        config=cfg(); now=datetime(2026,8,31,10,0,tzinfo=NY)
        expected=us.previous_session(now.date(),config)
        lagged=us.previous_session(expected,config)
        cache={
            "AAA":(bars_ending(lagged,drift=.0020),{"provider":"Yahoo"}),
            "BBB":(bars_ending(expected,drift=.0002),{"provider":"Yahoo"}),
        }
        def quote(symbol):
            price=120.0 if symbol=="AAA" else 110.0
            return {"provider":"Yahoo","symbol":symbol,"date":now.date().isoformat(),"observed_at":now.isoformat(),"open":price,"high":price,"low":price,"last":price,"volume":1000,"status":"single_source"}
        current=us.base_payload(now,config,"NO_TRADE","soft veto")
        payload=final.make_forced_payload(current,now=now,config=config,cache=cache,opening_fetcher=quote)
        self.assertEqual(payload["decision"],"TRADE")
        self.assertEqual(payload["selection"]["selection_mode"],"MANDATORY_DAILY_FINAL")
        self.assertIn(payload["selection"]["historical_feature_lag_sessions"],{0,1})
        self.assertEqual(payload["selection"]["market_snapshot"]["date"],now.date().isoformat())
        self.assertGreater(payload["selection"]["target"],payload["selection"]["reference_price"])
        self.assertLess(payload["selection"]["stop"],payload["selection"]["reference_price"])
        self.assertEqual(payload["selection"]["skip_above"],payload["selection"]["entry_zone"][1])

    def test_low_liquidity_is_never_relaxed(self):
        config=cfg(); now=datetime(2026,8,31,10,0,tzinfo=NY)
        expected=us.previous_session(now.date(),config)
        low=bars_ending(expected,volume=10)
        cache={"AAA":(low,{"provider":"Yahoo"}),"BBB":(low,{"provider":"Yahoo"})}
        current=us.base_payload(now,config,"NO_TRADE","soft veto")
        payload=final.make_forced_payload(current,now=now,config=config,cache=cache,opening_fetcher=lambda _: {})
        self.assertEqual(payload["decision"],"DATA_ERROR")
        self.assertIn("liquidity",payload["reason"].lower())

    def test_existing_trade_is_not_replaced(self):
        config=cfg(); now=datetime(2026,8,31,10,0,tzinfo=NY)
        current=us.base_payload(now,config,"TRADE","existing")
        current["selection"]={"symbol":"AAA"}
        payload=final.make_forced_payload(current,now=now,config=config,cache={},opening_fetcher=lambda _: {})
        self.assertIsNone(payload)

    def test_soft_no_trade_violates_daily_contract(self):
        config=cfg(); now=datetime(2026,8,31,10,0,tzinfo=NY)
        payload=us.base_payload(now,config,"NO_TRADE","weak catalyst")
        with self.assertRaises(us.PublicationError):
            final.enforce_daily_contract(payload,now=now,config=config)


if __name__ == "__main__":
    unittest.main()
