from __future__ import annotations

import copy
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone

from scripts import counterfactual_decision_gate_diagnostics_v29_1 as bridge
from scripts import daily_stock_rejected_candidate_freeze as freeze


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


def bars(*, close: float = 100.0, volume: int = 1_000_000) -> list[Bar]:
    out = []
    start = date(2026, 5, 1)
    for index in range(80):
        day = date.fromordinal(start.toordinal() + index)
        price = close + index * 0.05
        out.append(Bar(day=day, open=price - 0.2, high=price + 1.0, low=price - 1.0, close=price, volume=volume))
    return out


def config(market: str) -> dict:
    key = "minimum_median_turnover_pln" if market == "gpw" else "minimum_median_turnover_usd"
    return {
        key: 1_000_000.0 if market == "gpw" else 25_000_000.0,
        "maximum_risk_percent": 0.07,
        "minimum_reward_risk": 1.5,
        "minimum_composite_score": 72.0,
        "top_candidates_for_news": 2,
        "universe": [
            {"symbol": "AAA.WA" if market == "gpw" else "AAA", "name": "AAA", "sector": "banki"},
            {"symbol": "BBB.WA" if market == "gpw" else "BBB", "name": "BBB", "sector": "tech"},
        ],
    }


def payload(market: str, expected: str, *, rejection: str = "source_gate") -> dict:
    symbol = "AAA.WA" if market == "gpw" else "AAA"
    return {
        "schema_version": "test",
        "policy_version": "test-v1",
        "date": "2026-07-20",
        "generated_at": "2026-07-20T08:00:00+00:00",
        "decision": "BRAK_TRANSAKCJI" if market == "gpw" else "NO_TRADE",
        "reason": "No candidate passed evidence and risk gates.",
        "selection": None,
        "data_quality": {
            "status": "healthy",
            "expected_session": expected,
            "analysis_rejections": {symbol: rejection},
        },
    }


def normalizer(rows: list[dict]) -> None:
    rows.sort(key=lambda row: str(row["symbol"]))
    for index, row in enumerate(rows):
        row["quant_pre_score"] = 80.0 - index * 5.0


class RejectedCandidateFreezeTests(unittest.TestCase):
    def _context(self, market: str, *, low_volume_symbol: str | None = None):
        cfg = config(market)
        sample = bars()
        expected = sample[-1].day

        def fetcher(symbol: str):
            volume = 100 if symbol == low_volume_symbol else 1_000_000
            return bars(volume=volume)

        def builder(company, raw_bars):
            symbol = company["symbol"]
            if symbol == low_volume_symbol:
                return None
            return {
                **company,
                "symbol": symbol,
                "reference_price": raw_bars[-1].close,
                "entry_zone": [99.0, 101.0],
                "stop": 97.0,
                "target": 105.4,
                "reward_risk": 1.8,
                "scores": {"relative_momentum": 70.0},
                "returns": {"1d": 0.01, "5d": 0.02, "20d": 0.03},
            }

        return cfg, expected, fetcher, builder

    def test_source_gate_rejection_freezes_full_gpw_long_state(self):
        cfg, expected, fetcher, builder = self._context("gpw")
        p = payload("gpw", expected.isoformat())
        frozen = freeze.build_freeze(
            p,
            market="gpw",
            config=cfg,
            universe=cfg["universe"],
            bar_fetcher=fetcher,
            exact_builder=builder,
            normalizer=normalizer,
            frozen_at=datetime(2026, 7, 20, 8, 1, tzinfo=timezone.utc),
        )
        row = next(item for item in frozen["candidates"] if item["symbol"] == "AAA.WA")
        self.assertEqual(row["first_blocking_gate"]["name"], "source_evidence")
        self.assertTrue(row["settlement_eligibility"]["eligible"])
        self.assertIsNotNone(row["risk_plan"]["stop"])
        self.assertIsNotNone(row["risk_plan"]["target"])
        freeze._validate_freeze(frozen)

    def test_early_liquidity_rejection_still_freezes_prospective_diagnostic_plan(self):
        cfg, expected, fetcher, builder = self._context("us", low_volume_symbol="AAA")
        p = payload("us", expected.isoformat(), rejection="screened_by_liquidity_atr_or_risk")
        frozen = freeze.build_freeze(
            p,
            market="us",
            config=cfg,
            universe=cfg["universe"],
            bar_fetcher=fetcher,
            exact_builder=builder,
            normalizer=normalizer,
        )
        row = next(item for item in frozen["candidates"] if item["symbol"] == "AAA")
        self.assertEqual(row["first_blocking_gate"]["name"], "liquidity")
        self.assertEqual(row["risk_plan"]["plan_source"], "diagnostic_pre_gate_same_point_in_time_rules")
        self.assertTrue(row["settlement_eligibility"]["eligible"])

    def test_existing_same_decision_freeze_is_immutable(self):
        cfg, expected, fetcher, builder = self._context("gpw")
        p = payload("gpw", expected.isoformat())
        first, changed = freeze.apply_freeze(
            p,
            market="gpw",
            config=cfg,
            universe=cfg["universe"],
            bar_fetcher=fetcher,
            exact_builder=builder,
            normalizer=normalizer,
            frozen_at=datetime(2026, 7, 20, 8, 1, tzinfo=timezone.utc),
        )
        self.assertTrue(changed)
        original = copy.deepcopy(first[freeze.FIELD])
        second, changed = freeze.apply_freeze(
            first,
            market="gpw",
            config=cfg,
            universe=cfg["universe"],
            bar_fetcher=lambda _symbol: (_ for _ in ()).throw(AssertionError("must not refetch")),
            exact_builder=builder,
            normalizer=normalizer,
            frozen_at=datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(changed)
        self.assertEqual(second[freeze.FIELD], original)

    def test_provider_failure_remains_non_evaluable(self):
        cfg, expected, _fetcher, builder = self._context("gpw")
        p = payload("gpw", expected.isoformat())
        p["data_quality"]["provider_failures"] = {"AAA.WA": "timeout"}

        def fetcher(symbol: str):
            if symbol == "AAA.WA":
                raise RuntimeError("timeout")
            return bars()

        frozen = freeze.build_freeze(
            p,
            market="gpw",
            config=cfg,
            universe=cfg["universe"],
            bar_fetcher=fetcher,
            exact_builder=builder,
            normalizer=normalizer,
        )
        row = next(item for item in frozen["candidates"] if item["symbol"] == "AAA.WA")
        self.assertEqual(row["first_blocking_gate"]["name"], "market_data")
        self.assertFalse(row["settlement_eligibility"]["eligible"])

    def test_pr29_bridge_replaces_legacy_insufficient_candidate(self):
        cfg, expected, fetcher, builder = self._context("gpw")
        p = payload("gpw", expected.isoformat())
        p, changed = freeze.apply_freeze(
            p,
            market="gpw",
            config=cfg,
            universe=cfg["universe"],
            bar_fetcher=fetcher,
            exact_builder=builder,
            normalizer=normalizer,
        )
        self.assertTrue(changed)
        snapshots = bridge.adapt_daily_stock_v29_1(p, market="gpw")
        self.assertEqual(len(snapshots), 1)
        snap = snapshots[0]
        self.assertEqual(snap["coverage"], "selected_risk_plan_plus_full_rejected_candidate_state_v29_1")
        rejected = [row for row in snap["candidates"] if not row["selected"]]
        self.assertTrue(rejected)
        aaa = next(row for row in rejected if row["market_symbol"] == "AAA.WA")
        self.assertEqual(aaa["settlement_mode"], "risk_plan")
        self.assertEqual(aaa["metadata"]["first_blocking_gate"]["name"], "source_evidence")
        self.assertTrue(any(row["selected"] and row["action"] == "FLAT" for row in snap["candidates"]))


if __name__ == "__main__":
    unittest.main()
