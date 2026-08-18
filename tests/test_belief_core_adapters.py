from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_adapter_contract import EvidenceAssessment, Observation, observation_to_evidence
from belief_core_live import build_adapter_payload
from belief_liquidity_adapter import LiquidityEvidenceAdapter
from belief_market_data_adapter import Bar, MarketDataAdapter, MarketSnapshot
from belief_regime_adapter import RegimeCrossAssetAdapter
from belief_technical_adapter import TechnicalEvidenceAdapter

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def make_snapshot(now: datetime) -> MarketSnapshot:
    starts = {"SPY":100.0,"RSP":50.0,"IWM":200.0,"^VIX":18.0,"HYG":80.0,"LQD":100.0,"TLT":90.0,"UUP":25.0}
    steps = {"SPY":.10,"RSP":.06,"IWM":.25,"^VIX":-.01,"HYG":.02,"LQD":.005,"TLT":.01,"UUP":-.001}
    bars = {}
    end = now.astimezone(NY).replace(minute=0, second=0, microsecond=0)
    for symbol, start in starts.items():
        rows = []
        for i in range(80):
            ts = end - timedelta(minutes=30 * (79-i))
            close = start + steps[symbol]*i
            rows.append(Bar(ts.astimezone(UTC), close, open=close-.02, high=close+.05, low=close-.05, volume=100000+i*100))
        bars[symbol] = rows
    return MarketSnapshot(bars)


class BeliefAdapterContractTest(unittest.TestCase):
    def test_unavailable_observation_cannot_become_evidence(self) -> None:
        obs = Observation.make(adapter="x", metric="spread", entity="SPY", observed_at="2026-08-18T14:00:00Z",
            value=None, unit="price", source="test", source_type="secondary", source_ref="x", reliability=.8,
            independence_cluster="market:SPY:spread", status="unavailable")
        with self.assertRaises(ValueError):
            observation_to_evidence(obs, EvidenceAssessment("spx.trend.bullish", 1, .5, "test", "test"))

    def test_evidence_keeps_observation_provenance_and_derived_lineage(self) -> None:
        obs = Observation.make(adapter="x", metric="momentum", entity="SPY", observed_at="2026-08-18T14:00:00Z",
            value=.01, unit="return", source="test", source_type="derived", source_ref="x", reliability=.7,
            independence_cluster="derived:SPY:test")
        ev = observation_to_evidence(obs, EvidenceAssessment("spx.trend.bullish", 1, .6, "technical", "ok"))
        self.assertEqual(ev.metadata["observation_id"], obs.observation_id)
        self.assertEqual(ev.metadata["adapter"], "x")
        self.assertEqual(ev.metadata["lineage_node_type"], "observation")
        self.assertEqual(ev.source_type, "derived")
        self.assertEqual(ev.derived_from, (obs.observation_id,))


class AdapterSuiteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026,8,18,10,7,tzinfo=NY)
        self.snapshot = make_snapshot(self.now)

    def test_market_data_exposes_ohlcv_and_never_fabricates_spread(self) -> None:
        result = MarketDataAdapter(symbols=tuple(self.snapshot.bars)).run(self.snapshot)
        spread = [x for x in result.observations if x.entity == "SPY" and x.metric == "bid_ask_spread"][0]
        turnover = [x for x in result.observations if x.entity == "SPY" and x.metric == "dollar_turnover"][0]
        self.assertEqual(spread.status, "unavailable")
        self.assertIsNone(spread.value)
        self.assertEqual(turnover.status, "ok")
        self.assertGreater(turnover.value, 0)
        self.assertEqual(len(result.evidence), 0)

    def test_technical_adapter_is_deterministic_and_produces_one_trend_evidence(self) -> None:
        result = TechnicalEvidenceAdapter().run(self.snapshot)
        metrics = {x.metric for x in result.observations}
        self.assertTrue({"momentum_1d","rsi_14","breakout_20","vwap_distance","atr_14_pct","trend_composite"} <= metrics)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].belief_id, "spx.trend.bullish")
        self.assertEqual(result.evidence[0].metadata["adapter"], "technical_evidence")
        self.assertTrue(result.evidence[0].derived_from)

    def test_liquidity_adapter_produces_tradability_observations_and_credit_evidence(self) -> None:
        result = LiquidityEvidenceAdapter().run(self.snapshot)
        metrics = {x.metric for x in result.observations}
        self.assertTrue({"relative_volume","abnormal_volume","dollar_turnover","amihud_proxy","tradability_score"} <= metrics)
        rvol = [x for x in result.observations if x.metric == "relative_volume"][0]
        self.assertTrue(rvol.metadata["time_of_day_adjusted"])
        self.assertEqual(len(result.evidence), 2)
        self.assertEqual({x.belief_id for x in result.evidence}, {"spx.liquidity.supportive"})
        self.assertTrue(all(x.derived_from for x in result.evidence))

    def test_regime_adapter_produces_cross_asset_evidence(self) -> None:
        result = RegimeCrossAssetAdapter().run(self.snapshot)
        self.assertEqual(RegimeCrossAssetAdapter.classify(self.snapshot), "risk_on")
        self.assertEqual(len(result.evidence), 6)
        self.assertTrue({"spx.breadth.healthy","spx.volatility.benign","spx.financial_conditions.supportive"} <= {x.belief_id for x in result.evidence})
        self.assertTrue(all(x.derived_from for x in result.evidence))

    def test_full_pipeline_preserves_nine_evidence_but_adds_observation_layer(self) -> None:
        payload = build_adapter_payload(self.snapshot)
        self.assertEqual(len(payload["evidence"]), 9)
        self.assertEqual(len(payload["observations"]), 108)
        self.assertEqual(payload["adapter_counts"]["market_data"], {"observations":80,"evidence":0})
        self.assertEqual(payload["adapter_counts"]["liquidity_evidence"], {"observations":7,"evidence":2})
        self.assertEqual(payload["regime"], "risk_on")


if __name__ == "__main__":
    unittest.main()
