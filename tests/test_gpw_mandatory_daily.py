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
        "weights": {
            "catalyst": 25,
            "relative_momentum": 20,
            "volume_liquidity": 15,
            "market_context": 15,
            "risk_reward": 15,
            "historical_expectancy": 10,
        },
        "learning": {"minimum_resolved_trades_for_adaptation": 8},
        "universe": [
            {"symbol": "AAA.WA", "name": "AAA", "sector": "x"},
            {"symbol": "BBB.WA", "name": "BBB", "sector": "y"},
        ],
        "non_session_dates": [],
    }


def policy():
    return {
        "enabled": True,
        "not_before": "09:15",
        "cutoff": "10:30",
        "recovery_cutoff": "16:30",
        "minimum_market_coverage": 0.8,
        "minimum_median_turnover_pln": 1_000_000,
        "maximum_candidate_risk_percent": 0.07,
        "maximum_published_risk_percent": 0.07,
        "maximum_historical_lag_sessions": 1,
        "reward_risk": 1.8,
        "neutral_catalyst_score": 50.0,
    }


def bars(last_day: date, start: float, drift: float, volume: int = 20_000) -> list[gpw.Bar]:
    rows = []
    price = start
    for i in range(70):
        d = last_day - timedelta(days=69 - i)
        price *= 1 + drift
        rows.append(gpw.Bar(d, price * 0.995, price * 1.01, price * 0.99, price, volume))
    return rows


def current_payload(now: datetime, decision: str = "BRAK_TRANSAKCJI") -> dict:
    return {
        "schema_version": "gpw-daily-pick-v1",
        "policy_version": "test",
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "timezone": "Europe/Warsaw",
        "publication_cutoff": "10:00",
        "decision": decision,
        "reason": "primary did not select",
        "locked": False,
        "selection": None,
        "data_quality": {},
        "methodology": {"minimum_score": 72, "minimum_reward_risk": 1.5},
        "metrics": {},
        "disclaimer": "x",
    }


def snapshot(symbol: str, now: datetime, price: float = 111.5) -> dict:
    return {
        "provider": "Yahoo",
        "symbol": symbol,
        "date": now.date().isoformat(),
        "observed_at": now.isoformat(),
        "open": price - 0.5,
        "high": price + 0.5,
        "low": price - 1.0,
        "last": price,
        "volume": 10_000,
        "crosscheck": {"status": "confirmed"},
    }


class MandatoryGpwTests(unittest.TestCase):
    def test_final_selector_keeps_liquidity_and_risk_hard(self):
        selector = mandatory._selector_config(cfg(), policy())
        self.assertEqual(selector["minimum_median_turnover_pln"], 1_000_000)
        self.assertEqual(selector["maximum_risk_percent"], 0.07)

    def test_forces_best_valid_candidate_after_preferred_cutoff(self):
        now = datetime(2026, 8, 21, 10, 45, tzinfo=WARSAW)
        expected = date(2026, 8, 20)
        a = bars(expected, 100, 0.0015)
        b = bars(expected, 100, 0.0002)
        current = current_payload(now, "AWARIA_DANYCH")

        with (
            patch.object(gpw, "is_session_day", return_value=True),
            patch.object(gpw, "previous_session", return_value=expected),
            patch.object(gpw, "all_history", return_value=[]),
        ):
            result = mandatory.make_forced_payload(
                current,
                now=now,
                config=cfg(),
                policy=policy(),
                cache={"AAA.WA": a, "BBB.WA": b},
                opening_fetcher=lambda symbol, now: snapshot(symbol, now),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["decision"], "TRANSAKCJA")
        self.assertEqual(result["selection"]["selection_mode"], "MANDATORY_DAILY_FINAL")
        self.assertTrue(result["methodology"]["mandatory_daily_selection"]["late_recovery"])
        self.assertGreater(result["selection"]["target"], result["selection"]["reference_price"])
        self.assertLess(result["selection"]["stop"], result["selection"]["reference_price"])
        self.assertGreater(result["selection"]["skip_above"], result["selection"]["reference_price"])

    def test_one_session_historical_lag_is_usable(self):
        now = datetime(2026, 8, 21, 9, 30, tzinfo=WARSAW)
        expected = date(2026, 8, 20)
        lagged = date(2026, 8, 19)
        current = current_payload(now)
        cache = {
            "AAA.WA": bars(lagged, 100, 0.0015),
            "BBB.WA": bars(lagged, 100, 0.0002),
        }

        with (
            patch.object(gpw, "is_session_day", return_value=True),
            patch.object(gpw, "all_history", return_value=[]),
        ):
            result = mandatory.make_forced_payload(
                current,
                now=now,
                config=cfg(),
                policy=policy(),
                cache=cache,
                opening_fetcher=lambda symbol, now: snapshot(symbol, now),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["selection"]["historical_feature_session"], lagged.isoformat())
        self.assertEqual(result["selection"]["historical_feature_lag_sessions"], 1)

    def test_low_liquidity_candidate_is_not_force_selected(self):
        now = datetime(2026, 8, 21, 9, 30, tzinfo=WARSAW)
        expected = date(2026, 8, 20)
        current = current_payload(now)
        low_liquidity = bars(expected, 100, 0.003, volume=100)
        liquid = bars(expected, 100, 0.0005, volume=20_000)

        with (
            patch.object(gpw, "is_session_day", return_value=True),
            patch.object(gpw, "previous_session", return_value=expected),
            patch.object(gpw, "all_history", return_value=[]),
        ):
            result = mandatory.make_forced_payload(
                current,
                now=now,
                config=cfg(),
                policy=policy(),
                cache={"AAA.WA": low_liquidity, "BBB.WA": liquid},
                opening_fetcher=lambda symbol, now: snapshot(symbol, now),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["selection"]["symbol"], "BBB.WA")

    def test_stale_publication_is_rebased_to_today_before_selection(self):
        now = datetime(2026, 8, 31, 10, 45, tzinfo=WARSAW)
        stale = {"date": "2026-08-25", "decision": "BRAK_TRANSAKCJI"}
        with patch.object(gpw, "common_payload") as common:
            common.return_value = current_payload(now)
            result = mandatory._fresh_base_payload(stale, now=now, config=cfg())
        self.assertEqual(result["date"], "2026-08-31")
        self.assertTrue(result["data_quality"]["recovered_from_stale_publication"])
        self.assertEqual(result["data_quality"]["previous_publication_date"], "2026-08-25")

    def test_does_not_replace_existing_transaction(self):
        now = datetime(2026, 8, 21, 9, 30, tzinfo=WARSAW)
        current = current_payload(now, "TRANSAKCJA")
        current["selection"] = {"symbol": "AAA.WA"}
        result = mandatory.make_forced_payload(
            current,
            now=now,
            config=cfg(),
            policy=policy(),
            cache={},
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
