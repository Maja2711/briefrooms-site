from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import gpw_daily_pick as gpw
from scripts import gpw_mandatory_daily as mandatory

WARSAW = ZoneInfo("Europe/Warsaw")


def cfg():
    return {
        "policy_version": "test",
        "minimum_median_turnover_pln": 1_000_000,
        "maximum_risk_percent": 0.07,
        "minimum_reward_risk": 1.5,
        "minimum_composite_score": 72,
        "weights": {"catalyst":25,"relative_momentum":20,"volume_liquidity":15,"market_context":15,"risk_reward":15,"historical_expectancy":10},
        "learning": {"minimum_resolved_trades_for_adaptation": 8},
        "universe": [
            {"symbol":"AAA.WA","name":"AAA","sector":"x"},
            {"symbol":"BBB.WA","name":"BBB","sector":"y"},
        ],
        "non_session_dates": [],
    }


def policy():
    return {
        "enabled": True, "not_before":"09:15", "cutoff":"10:30",
        "minimum_market_coverage":0.8, "maximum_candidate_risk_percent":0.15,
        "maximum_published_risk_percent":0.07, "reward_risk":1.8,
        "neutral_catalyst_score":50.0,
    }


def bars(day: date, start: float, drift: float, volume: int = 100) -> list[gpw.Bar]:
    rows=[]
    price=start
    for i in range(70):
        d=day-timedelta(days=69-i)
        price *= 1 + drift
        rows.append(gpw.Bar(d, price*0.995, price*1.01, price*0.99, price, volume))
    return rows


class MandatoryGpwTests(unittest.TestCase):
    def test_forces_best_ranked_candidate_even_below_standard_turnover_gate(self):
        now=datetime(2026,8,21,9,30,tzinfo=WARSAW)
        expected=date(2026,8,20)
        # Adjust synthetic rows so final completed bar lands on expected session.
        a=bars(expected,100,0.0015,100)
        b=bars(expected,100,0.0002,100)
        # overwrite dates to business-agnostic monotonic sequence ending on expected
        for rows in (a,b):
            for idx,row in enumerate(list(rows)):
                rows[idx]=gpw.Bar(expected-timedelta(days=69-idx),row.open,row.high,row.low,row.close,row.volume)
        current={
            "schema_version":"gpw-daily-pick-v1","policy_version":"test","date":"2026-08-21",
            "generated_at":now.isoformat(),"timezone":"Europe/Warsaw","publication_cutoff":"10:00",
            "decision":"BRAK_TRANSAKCJI","reason":"standard filters rejected all","locked":False,
            "selection":None,"data_quality":{},"methodology":{"minimum_score":72,"minimum_reward_risk":1.5},
            "metrics":{},"disclaimer":"x"
        }
        snapshot={"provider":"Yahoo","symbol":"AAA.WA","date":"2026-08-21","observed_at":now.isoformat(),"open":111.0,"high":112.0,"low":110.5,"last":111.5,"volume":1000,"crosscheck":{"status":"confirmed"}}
        with (
            patch.object(gpw,"is_session_day",return_value=True),
            patch.object(gpw,"previous_session",return_value=expected),
            patch.object(gpw,"all_history",return_value=[]),
        ):
            result=mandatory.make_forced_payload(current,now=now,config=cfg(),policy=policy(),cache={"AAA.WA":a,"BBB.WA":b},opening_fetcher=lambda symbol,now: {**snapshot,"symbol":symbol})
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"],"TRANSAKCJA")
        self.assertEqual(result["selection"]["selection_mode"],"MANDATORY_DAILY")
        self.assertTrue(result["selection"]["review"]["approved"])
        self.assertGreater(result["selection"]["target"],result["selection"]["reference_price"])
        self.assertLess(result["selection"]["stop"],result["selection"]["reference_price"])

    def test_does_not_replace_existing_transaction(self):
        now=datetime(2026,8,21,9,30,tzinfo=WARSAW)
        current={"decision":"TRANSAKCJA","date":"2026-08-21"}
        result=mandatory.make_forced_payload(current,now=now,config=cfg(),policy=policy(),cache={})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
