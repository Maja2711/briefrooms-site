from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import daily_stock_core as core
from scripts import gpw_data_gates as gates
from scripts import gpw_daily_pick as gpw
from scripts import gpw_full_ranking as ranking


ROOT = Path(__file__).resolve().parents[1]
WARSAW = ZoneInfo("Europe/Warsaw")


def cfg() -> dict:
    return {
        "policy_version": "test-p035",
        "minimum_data_completeness": 0.6,
        "minimum_median_turnover_pln": 1_000_000,
        "maximum_risk_percent": 0.07,
        "minimum_reward_risk": 1.5,
        "weights": {
            "catalyst": 25,
            "relative_momentum": 20,
            "volume_liquidity": 15,
            "market_context": 15,
            "risk_reward": 15,
            "historical_expectancy": 10,
        },
        "learning": {"minimum_resolved_trades_for_adaptation": 8},
        "relative_momentum": {
            "enabled": True,
            "engine": "cross-sectional-multihorizon-v2",
            "weights": {
                "rank_1d": 0.10,
                "rank_5d": 0.35,
                "rank_20d": 0.20,
                "rank_risk_adjusted_5d": 0.20,
                "rank_sector_5d": 0.15,
            },
        },
        "data_gates": {
            "engine": "gpw-data-gates-v1",
            "minimum_market_coverage": 0.6,
            "maximum_historical_lag_sessions": 1,
            "require_current_session_quote": True,
            "maximum_execution_quote_age_minutes": 20,
            "maximum_future_clock_skew_minutes": 2,
            "reject_crosscheck_statuses": ["conflict", "rejected"],
        },
        "full_ranking": {"enabled": True, "engine": "gpw-full-ranking-v1"},
        "non_session_dates": [],
        "universe": [
            {"symbol": "AAA.WA", "name": "AAA", "sector": "banki"},
            {"symbol": "BBB.WA", "name": "BBB", "sector": "banki"},
            {"symbol": "CCC.WA", "name": "CCC", "sector": "energia"},
        ],
    }


def bars(last_day: date, *, drift: float = 0.001, volume: int = 20_000) -> list[gpw.Bar]:
    rows: list[gpw.Bar] = []
    price = 100.0
    for i in range(75):
        day = last_day - timedelta(days=74 - i)
        price *= 1.0 + drift
        rows.append(gpw.Bar(day, price * 0.995, price * 1.01, price * 0.99, price, volume))
    return rows


def candidate(symbol: str, sector: str, r1: float, r5: float, r20: float, atr: float) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "sector": sector,
        "returns": {"1d": r1, "5d": r5, "20d": r20},
        "atr_percent": atr,
        "raw_momentum": 50.0,
        "relative_momentum_model": core._relative_momentum_settings(cfg()),
        "scores": {
            "relative_momentum": 50.0,
            "volume_liquidity": 60.0,
            "market_context": 50.0,
            "risk_reward": 70.0,
            "historical_expectancy": 50.0,
        },
    }


class GpwP035Tests(unittest.TestCase):
    def test_p05_uses_multihorizon_robust_relative_momentum(self):
        rows = [
            candidate("AAA.WA", "banki", 0.01, 0.05, 0.08, 0.025),
            candidate("BBB.WA", "banki", -0.01, 0.01, 0.02, 0.025),
            candidate("CCC.WA", "energia", 0.08, 0.50, 0.70, 0.10),
            candidate("DDD.WA", "energia", 0.00, 0.02, 0.03, 0.02),
        ]
        core.normalize_cross_section(rows)
        by_symbol = {row["symbol"]: row for row in rows}
        self.assertGreater(
            by_symbol["AAA.WA"]["scores"]["relative_momentum"],
            by_symbol["BBB.WA"]["scores"]["relative_momentum"],
        )
        detail = by_symbol["AAA.WA"]["relative_momentum_detail"]
        self.assertEqual(detail["engine"], "cross-sectional-multihorizon-v2")
        self.assertIn("rank_20d", detail["components"])
        self.assertIn("rank_risk_adjusted_5d", detail["components"])
        self.assertIn("rank_sector_5d", detail["components"])

    def test_p04_accepts_one_session_lag_and_rejects_older_history(self):
        config = cfg()
        expected = date(2026, 8, 20)
        one_lag = gates.historical_gate(
            bars(date(2026, 8, 19)),
            expected_day=expected,
            config=config,
        )
        two_lag = gates.historical_gate(
            bars(date(2026, 8, 18)),
            expected_day=expected,
            config=config,
        )
        self.assertTrue(one_lag["accepted"])
        self.assertEqual(one_lag["lag_sessions"], 1)
        self.assertFalse(two_lag["accepted"])
        self.assertEqual(two_lag["status"], "stale")

    def test_p04_execution_gate_rejects_same_day_but_old_quote(self):
        config = cfg()
        now = datetime(2026, 8, 21, 10, 0, tzinfo=WARSAW)
        stale = {
            "provider": "Yahoo",
            "date": "2026-08-21",
            "observed_at": "2026-08-21T09:39:00+02:00",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "last": 101.0,
            "crosscheck": {"status": "confirmed"},
        }
        with self.assertRaisesRegex(ValueError, "stale execution quote age"):
            gates.execution_gate(stale, now=now, config=config)

    def test_p03_full_ranking_contains_every_configured_symbol(self):
        config = cfg()
        now = datetime(2026, 8, 21, 9, 10, tzinfo=WARSAW)
        expected = date(2026, 8, 20)
        cache = {
            "AAA.WA": bars(expected, drift=0.0015, volume=20_000),
            "BBB.WA": bars(expected, drift=0.0003, volume=100),
        }

        def build(company, source, feature_day, config_value, history):
            return core.build_quant_candidate(
                company,
                source,
                feature_day,
                config_value,
                core.GPW_PROFILE,
                history=history,
            )

        with (
            patch.object(ranking.runtime, "install", return_value=None),
            patch.object(ranking.gpw, "load_config", return_value=config),
            patch.object(ranking.gpw, "previous_session", return_value=expected),
            patch.object(ranking.gpw, "all_history", return_value=[]),
            patch.object(ranking.gpw, "build_quant_candidate", side_effect=build),
            patch.object(ranking.gpw, "normalize_cross_section", side_effect=core.normalize_cross_section),
        ):
            result = ranking.build(
                now=now,
                cache=cache,
                provider_failures={"CCC.WA": "provider down"},
            )

        self.assertEqual(len(result["rows"]), len(config["universe"]))
        statuses = {row["symbol"]: row["status"] for row in result["rows"]}
        self.assertEqual(statuses["AAA.WA"], "RANKED")
        self.assertEqual(statuses["BBB.WA"], "SCREENED_OUT")
        self.assertEqual(statuses["CCC.WA"], "DATA_REJECTED")
        self.assertEqual(result["data_quality"]["engine"], "gpw-data-gates-v1")

    def test_p03_ui_is_single_synchronized_pl_en_component(self):
        script = (ROOT / "scripts/gpw-full-ranking-public.js").read_text(encoding="utf-8")
        polish = (ROOT / "pl/inwestycje/daily-trading.html").read_text(encoding="utf-8")
        english = (ROOT / "en/investing/daily-trading.html").read_text(encoding="utf-8")
        self.assertIn("Pełny ranking kandydatów · P0.3", script)
        self.assertIn("Full candidate ranking · P0.3", script)
        self.assertIn("gpw_daily_candidate_ranking.json", script)
        for page in (polish, english):
            self.assertIn("/scripts/gpw-full-ranking-public.js", page)
            self.assertIn("/assets/gpw-full-ranking.css", page)


if __name__ == "__main__":
    unittest.main()
