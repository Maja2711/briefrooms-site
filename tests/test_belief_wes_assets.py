from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.belief_core import BeliefCore
from scripts.belief_core_live import (
    AUTOMATIC_TUNING_ENABLED,
    BELIEFS,
    POLICY_OUTPUT_ENABLED,
    TRADE_EXECUTION_ENABLED,
    _belief_ids_for_consumer,
    evaluate_spec,
    freeze_set,
    outcome_spec,
)
from scripts.belief_market_data_adapter import (
    BTC_SYMBOL if False else Bar,  # keep import line explicit without hidden aliases
)
from scripts.belief_market_data_adapter import CORE_SYMBOLS, DEFAULT_SYMBOLS, MarketDataAdapter, MarketSnapshot, OPTIONAL_WES_ASSET_SYMBOLS
from scripts.belief_wes_assets_adapter import (
    BTC_SYMBOL,
    EURUSD_SYMBOL,
    WES_ASSET_BELIEF_IDS,
    WESAssetEvidenceAdapter,
    coverage_report,
)

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def make_snapshot(now: datetime) -> MarketSnapshot:
    starts = {
        "SPY": 100.0,
        "RSP": 50.0,
        "IWM": 200.0,
        "^VIX": 18.0,
        "HYG": 80.0,
        "LQD": 100.0,
        "TLT": 90.0,
        "UUP": 25.0,
        EURUSD_SYMBOL: 1.16,
        BTC_SYMBOL: 60000.0,
    }
    steps = {
        "SPY": .10,
        "RSP": .06,
        "IWM": .25,
        "^VIX": -.01,
        "HYG": .02,
        "LQD": .005,
        "TLT": .015,
        "UUP": -.002,
        EURUSD_SYMBOL: .00012,
        BTC_SYMBOL: 25.0,
    }
    bars = {}
    end = now.astimezone(NY).replace(minute=0, second=0, microsecond=0)
    for symbol, start in starts.items():
        rows = []
        for i in range(90):
            ts = end - timedelta(minutes=30 * (89 - i))
            close = start + steps[symbol] * i
            rows.append(Bar(ts.astimezone(UTC), close, open=close * .9999, high=close * 1.0005, low=close * .9995, volume=100000 + i * 100))
        bars[symbol] = rows
    return MarketSnapshot(bars)


class OptionalMarketCoverageTests(unittest.TestCase):
    def test_default_symbols_extend_core_without_replacing_it(self):
        self.assertTrue(set(CORE_SYMBOLS) < set(DEFAULT_SYMBOLS))
        self.assertEqual(tuple(OPTIONAL_WES_ASSET_SYMBOLS), (EURUSD_SYMBOL, BTC_SYMBOL))

    def test_optional_fetch_failure_does_not_take_down_spx_snapshot(self):
        now = datetime(2026, 8, 20, 10, 0, tzinfo=NY)
        full = make_snapshot(now)

        class Client:
            def bars(self, symbol, range_="10d", interval="30m"):
                if symbol in OPTIONAL_WES_ASSET_SYMBOLS:
                    raise RuntimeError("optional source unavailable")
                return full.bars[symbol]

        snapshot = MarketDataAdapter(client=Client()).fetch_snapshot()
        self.assertTrue(set(CORE_SYMBOLS) <= set(snapshot.bars))
        self.assertNotIn(EURUSD_SYMBOL, snapshot.bars)
        self.assertNotIn(BTC_SYMBOL, snapshot.bars)


class WESAssetAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 20, 10, 7, tzinfo=NY)
        self.snapshot = make_snapshot(self.now)

    def test_seven_atomic_beliefs_are_registered(self):
        ids = {x.belief_id for x in BELIEFS}
        self.assertEqual(len(WES_ASSET_BELIEF_IDS), 7)
        self.assertTrue(set(WES_ASSET_BELIEF_IDS) <= ids)
        self.assertEqual(len([x for x in WES_ASSET_BELIEF_IDS if x.startswith("eurusd.")]), 3)
        self.assertEqual(len([x for x in WES_ASSET_BELIEF_IDS if x.startswith("btc.")]), 4)

    def test_adapter_emits_all_seven_evidence_with_explicit_proxies(self):
        result = WESAssetEvidenceAdapter().run(self.snapshot)
        self.assertEqual(len(result.evidence), 7)
        self.assertEqual({x.belief_id for x in result.evidence}, set(WES_ASSET_BELIEF_IDS))
        self.assertTrue(all(x.source_type == "derived" for x in result.evidence))
        self.assertTrue(all(x.derived_from for x in result.evidence))
        eur_rates = next(x for x in result.evidence if x.belief_id == "eurusd.us_rates_pressure.supportive")
        self.assertFalse(eur_rates.metadata["rate_differential"])
        btc_liq = next(x for x in result.evidence if x.belief_id == "btc.liquidity.supportive")
        self.assertFalse(btc_liq.metadata["on_chain_coverage"])

    def test_coverage_report_does_not_claim_ecb_or_onchain(self):
        report = coverage_report()
        self.assertEqual(report["eurusd"]["status"], "partial_market_macro_proxy_coverage")
        self.assertFalse(report["eurusd"]["rate_differential_claimed"])
        self.assertIn("ecb_policy_state", report["eurusd"]["not_covered"])
        self.assertEqual(report["btc"]["status"], "partial_market_cross_asset_coverage")
        self.assertFalse(report["btc"]["on_chain_claimed"])
        self.assertIn("stablecoin_liquidity", report["btc"]["not_covered"])
        self.assertFalse(report["decision_influence"])

    def test_eurusd_outcomes_are_deterministic(self):
        trend = outcome_spec("eurusd.trend.bullish", self.snapshot)
        self.assertTrue(evaluate_spec(trend, {EURUSD_SYMBOL: trend["reference"] * 1.01}))
        self.assertFalse(evaluate_spec(trend, {EURUSD_SYMBOL: trend["reference"] * .99}))
        usd = outcome_spec("eurusd.usd_environment.supportive", self.snapshot)
        self.assertTrue(evaluate_spec(usd, {"UUP": usd["threshold"] * .99}))
        self.assertFalse(evaluate_spec(usd, {"UUP": usd["threshold"] * 1.01}))
        rates = outcome_spec("eurusd.us_rates_pressure.supportive", self.snapshot)
        self.assertTrue(evaluate_spec(rates, {"TLT": rates["threshold"] * 1.01}))
        self.assertFalse(evaluate_spec(rates, {"TLT": rates["threshold"] * .99}))

    def test_btc_liquidity_and_volatility_outcomes_are_frozen(self):
        liq = outcome_spec("btc.liquidity.supportive", self.snapshot)
        ref = liq["reference"]
        self.assertTrue(evaluate_spec(liq, {"HYG": 82.0, "LQD": 100.0, "TLT": ref["TLT"] * 1.01}))
        self.assertFalse(evaluate_spec(liq, {"HYG": 78.0, "LQD": 100.0, "TLT": ref["TLT"] * 1.01}))
        vol = outcome_spec("btc.volatility.benign", self.snapshot)
        self.assertGreaterEqual(vol["threshold_return"], .04)
        self.assertLessEqual(vol["threshold_return"], .12)
        self.assertTrue(evaluate_spec(vol, {BTC_SYMBOL: vol["reference"] * (1 + vol["threshold_return"] * .5)}))
        self.assertFalse(evaluate_spec(vol, {BTC_SYMBOL: vol["reference"] * (1 + vol["threshold_return"] * 1.2)}))


class ConsumerIsolationTests(unittest.TestCase):
    def test_brace_consumer_stays_spx_only_while_wes_gets_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = BeliefCore(Path(tmp))
            core.register_beliefs(BELIEFS)
            brace_ids = _belief_ids_for_consumer(core, "BRACE+BRACE-SPX")
            wes_ids = _belief_ids_for_consumer(core, "WES")
            self.assertEqual(len(brace_ids), 5)
            self.assertTrue(all(x.startswith("spx.") for x in brace_ids))
            self.assertEqual(len(wes_ids), 12)
            self.assertTrue(set(WES_ASSET_BELIEF_IDS) <= set(wes_ids))

    def test_freeze_set_uses_asset_specific_market_timestamp(self):
        snapshot = make_snapshot(datetime(2026, 8, 21, 16, 7, tzinfo=NY))
        with tempfile.TemporaryDirectory() as tmp:
            core = BeliefCore(Path(tmp))
            core.register_beliefs(BELIEFS)
            core.ingest(WESAssetEvidenceAdapter().run(snapshot).evidence)
            core.recompute(datetime(2026, 8, 21, 16, 7, tzinfo=NY))
            count = freeze_set(
                core,
                snapshot,
                datetime(2026, 8, 21, 16, 7, tzinfo=NY),
                datetime(2026, 8, 28, 16, 0, tzinfo=NY),
                "WES",
                "wes:2026-08-21:1600",
                "risk_on",
            )
            self.assertEqual(count, 12)
            eur = next(x for x in core.forecasts.values() if x.belief_id == "eurusd.trend.bullish")
            btc = next(x for x in core.forecasts.values() if x.belief_id == "btc.trend.bullish")
            self.assertEqual(eur.metadata["market_symbol"], EURUSD_SYMBOL)
            self.assertEqual(btc.metadata["market_symbol"], BTC_SYMBOL)
            self.assertEqual(eur.metadata["market_observed_at"], snapshot.observed_at(EURUSD_SYMBOL).astimezone(UTC).isoformat().replace("+00:00", "Z"))

    def test_safety_flags_remain_off(self):
        self.assertFalse(TRADE_EXECUTION_ENABLED)
        self.assertFalse(POLICY_OUTPUT_ENABLED)
        self.assertFalse(AUTOMATIC_TUNING_ENABLED)


if __name__ == "__main__":
    unittest.main()
