from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, timedelta

from scripts import daily_stock_core as core


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


def bars(count: int = 80, *, volume: int = 2_000_000) -> list[Bar]:
    result = []
    cursor = date(2026, 4, 20)
    price = 100.0
    while len(result) < count:
        if cursor.weekday() < 5:
            price *= 1.0015
            result.append(Bar(cursor, price - 0.8, price + 1.0, price - 1.0, price, volume))
        cursor += timedelta(days=1)
    return result


class DailyStockCoreTests(unittest.TestCase):
    def test_gpw_and_us_profiles_keep_market_specific_risk_and_liquidity(self):
        self.assertEqual(core.GPW_PROFILE.currency, "PLN")
        self.assertEqual(core.US_PROFILE.currency, "USD")
        self.assertNotEqual(core.GPW_PROFILE.risk_atr_multiple, core.US_PROFILE.risk_atr_multiple)
        self.assertNotEqual(core.GPW_PROFILE.turnover_floor, core.US_PROFILE.turnover_floor)

    def test_candidate_uses_shared_momentum_risk_and_reward_risk(self):
        rows = bars(volume=3_000_000)
        expected = rows[-1].day
        config = {
            "minimum_median_turnover_usd": 25_000_000,
            "maximum_risk_percent": 0.07,
            "learning": {"minimum_resolved_trades_for_adaptation": 8},
        }
        candidate = core.build_quant_candidate(
            {"symbol": "TEST", "name": "Test", "sector": "technology"},
            rows,
            expected,
            config,
            core.US_PROFILE,
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["core_market"], "US")
        self.assertEqual(candidate["core_currency"], "USD")
        self.assertEqual(candidate["reward_risk"], 1.8)
        self.assertEqual(candidate["historical_sample"], 0)
        self.assertEqual(candidate["scores"]["historical_expectancy"], 50.0)

    def test_cross_section_writes_same_common_quant_score_contract(self):
        candidates = [
            {"sector": "technology", "returns": {"1d": 0.01, "5d": 0.05}, "raw_momentum": 75.0,
             "scores": {"relative_momentum": 75.0, "volume_liquidity": 70.0, "market_context": 50.0, "risk_reward": 80.0, "historical_expectancy": 50.0}},
            {"sector": "financials", "returns": {"1d": -0.005, "5d": 0.01}, "raw_momentum": 55.0,
             "scores": {"relative_momentum": 55.0, "volume_liquidity": 65.0, "market_context": 50.0, "risk_reward": 75.0, "historical_expectancy": 50.0}},
        ]
        core.normalize_cross_section(candidates)
        self.assertGreater(candidates[0]["scores"]["relative_momentum"], candidates[1]["scores"]["relative_momentum"])
        self.assertTrue(all(0 <= row["quant_pre_score"] <= 100 for row in candidates))

    def test_bayesian_learning_is_neutral_until_minimum_and_bounded_after(self):
        history = []
        for index in range(10):
            history.append({
                "date": f"2026-08-{index + 1:02d}",
                "selection": {"sector": "banks" if index < 6 else "technology"},
                "outcome": {"status": "RESOLVED", "activated": True, "r_multiple": 1.8 if index < 8 else -1.0},
            })
        neutral, n = core.bayesian_history_expectancy_score(history[:7], "banks", 8)
        self.assertEqual((neutral, n), (50.0, 7))
        learned, n = core.bayesian_history_expectancy_score(history, "banks", 8, max_adjustment=12)
        self.assertEqual(n, 10)
        self.assertGreaterEqual(learned, 38.0)
        self.assertLessEqual(learned, 62.0)
        self.assertGreater(learned, 50.0)

    def test_composite_uses_configured_six_factor_weights(self):
        candidate = {"scores": {
            "relative_momentum": 80,
            "volume_liquidity": 70,
            "market_context": 60,
            "risk_reward": 90,
            "historical_expectancy": 50,
        }}
        analysis = {"catalyst_score": 85}
        config = {"weights": {
            "catalyst": 25,
            "relative_momentum": 20,
            "volume_liquidity": 15,
            "market_context": 15,
            "risk_reward": 15,
            "historical_expectancy": 10,
        }}
        self.assertEqual(core.composite_score(candidate, analysis, config), 75.25)


if __name__ == "__main__":
    unittest.main()
