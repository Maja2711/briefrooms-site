from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, clamp, observation_to_evidence
from belief_core import BeliefDefinition, iso_z
from belief_market_data_adapter import Bar, MarketSnapshot

EURUSD_SYMBOL = "EURUSD=X"
BTC_SYMBOL = "BTC-USD"

WES_ASSET_BELIEFS: Tuple[BeliefDefinition, ...] = (
    BeliefDefinition(
        "eurusd.trend.bullish",
        "EUR/USD is higher into the target horizon",
        prior_probability=.50,
        half_life_hours=18,
        entity="EURUSD",
        domain="trend",
        tags=("shared", "WES", "EURUSD"),
        horizon_hours=24,
        outcome_rule="eurusd_close_above_reference",
    ),
    BeliefDefinition(
        "eurusd.usd_environment.supportive",
        "Broad USD conditions are supportive for EUR/USD into the target horizon",
        prior_probability=.50,
        half_life_hours=24,
        entity="EURUSD",
        domain="usd_environment",
        tags=("shared", "WES", "EURUSD"),
        horizon_hours=24,
        outcome_rule="uup_below_reference",
    ),
    BeliefDefinition(
        "eurusd.us_rates_pressure.supportive",
        "US rates pressure eases in a way that is supportive for EUR/USD into the target horizon",
        prior_probability=.50,
        half_life_hours=24,
        entity="EURUSD",
        domain="rates_proxy",
        tags=("shared", "WES", "EURUSD", "proxy_not_rate_differential"),
        horizon_hours=24,
        outcome_rule="tlt_above_reference_proxy",
    ),
    BeliefDefinition(
        "btc.trend.bullish",
        "BTC/USD is higher into the target horizon",
        prior_probability=.50,
        half_life_hours=12,
        entity="BTC",
        domain="trend",
        tags=("shared", "WES", "BTC"),
        horizon_hours=24,
        outcome_rule="btc_close_above_reference",
    ),
    BeliefDefinition(
        "btc.liquidity.supportive",
        "Cross-asset credit and duration conditions remain supportive for BTC into the target horizon",
        prior_probability=.50,
        half_life_hours=24,
        entity="BTC",
        domain="liquidity",
        tags=("shared", "WES", "BTC", "cross_asset_proxy"),
        horizon_hours=24,
        outcome_rule="credit_and_duration_supportive",
    ),
    BeliefDefinition(
        "btc.volatility.benign",
        "BTC volatility remains contained into the target horizon",
        prior_probability=.52,
        half_life_hours=12,
        entity="BTC",
        domain="volatility",
        tags=("shared", "WES", "BTC"),
        horizon_hours=24,
        outcome_rule="btc_absolute_return_below_frozen_cap",
    ),
    BeliefDefinition(
        "btc.usd_environment.supportive",
        "Broad USD conditions are supportive for BTC into the target horizon",
        prior_probability=.50,
        half_life_hours=24,
        entity="BTC",
        domain="usd_environment",
        tags=("shared", "WES", "BTC"),
        horizon_hours=24,
        outcome_rule="uup_below_reference",
    ),
)

WES_ASSET_BELIEF_IDS = tuple(x.belief_id for x in WES_ASSET_BELIEFS)


def belief_market_symbol(belief_id: str) -> str:
    if belief_id.startswith("eurusd."):
        return EURUSD_SYMBOL
    if belief_id.startswith("btc."):
        return BTC_SYMBOL
    return "SPY"


def coverage_report() -> Dict[str, Any]:
    return {
        "schema_version": "belief-wes-asset-coverage-v1",
        "decision_influence": False,
        "spx": {
            "status": "existing_full_bridge_scope",
        },
        "eurusd": {
            "status": "partial_market_macro_proxy_coverage",
            "beliefs": [x for x in WES_ASSET_BELIEF_IDS if x.startswith("eurusd.")],
            "covered": ["price_trend", "broad_usd_environment", "us_rates_pressure_proxy"],
            "not_covered": ["ecb_policy_state", "eur_us_rate_differential", "euro_area_macro_surprise"],
            "rate_differential_claimed": False,
        },
        "btc": {
            "status": "partial_market_cross_asset_coverage",
            "beliefs": [x for x in WES_ASSET_BELIEF_IDS if x.startswith("btc.")],
            "covered": ["price_trend", "cross_asset_liquidity", "realized_volatility", "broad_usd_environment"],
            "not_covered": ["on_chain_flows", "stablecoin_liquidity", "exchange_flows", "crypto_derivatives_positioning"],
            "on_chain_claimed": False,
        },
    }


def _safe_return(snapshot: MarketSnapshot, symbol: str, bars: int) -> Optional[float]:
    rows = snapshot.bars.get(symbol) or []
    if len(rows) <= bars or rows[-1 - bars].close == 0:
        return None
    return rows[-1].close / rows[-1 - bars].close - 1.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rvol(rows: Sequence[Bar], lookback: int) -> Optional[float]:
    chunk = list(rows[-lookback:])
    closes = [x.close for x in chunk if x.close > 0]
    if len(closes) < 3:
        return None
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    return math.sqrt(sum(x * x for x in returns))


def _trend_score(snapshot: MarketSnapshot, symbol: str, scales: Tuple[float, float, float]) -> Optional[float]:
    r3 = _safe_return(snapshot, symbol, 6)
    r1 = _safe_return(snapshot, symbol, 13)
    r5 = _safe_return(snapshot, symbol, 65)
    if None in {r3, r1, r5}:
        return None
    return clamp(
        .30 * clamp(float(r3) / scales[0], -1.0, 1.0)
        + .35 * clamp(float(r1) / scales[1], -1.0, 1.0)
        + .35 * clamp(float(r5) / scales[2], -1.0, 1.0),
        -1.0,
        1.0,
    )


class WESAssetEvidenceAdapter:
    name = "wes_asset_evidence"
    version = "1.0.0"

    def _observation(
        self,
        *,
        metric: str,
        entity: str,
        observed_at: str,
        value: float,
        cluster: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Observation:
        return Observation.make(
            adapter=self.name,
            metric=metric,
            entity=entity,
            observed_at=observed_at,
            value=round(float(value), 8),
            unit="score",
            source="Derived from Yahoo Finance OHLCV",
            source_type="derived",
            source_ref=f"derived:yahoo:{entity}:{observed_at}:{self.version}:{metric}",
            reliability=.74,
            independence_cluster=cluster,
            tags=("WES", "cross_asset", self.version),
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _evidence(obs: Observation, belief_id: str, score: float, evidence_type: str, note: str):
        return observation_to_evidence(
            obs,
            EvidenceAssessment(
                belief_id,
                1 if score >= 0 else -1,
                clamp(abs(score), .08, .75),
                evidence_type,
                note,
            ),
        )

    def run(self, snapshot: MarketSnapshot) -> AdapterResult:
        observations: List[Observation] = []
        evidence = []

        # EUR/USD coverage is deliberately a market + US proxy foundation. It
        # does not claim ECB or EUR-vs-USD rate-differential coverage.
        if EURUSD_SYMBOL in snapshot.bars:
            observed_at = iso_z(snapshot.observed_at(EURUSD_SYMBOL))
            trend = _trend_score(snapshot, EURUSD_SYMBOL, (.0035, .0075, .018))
            if trend is not None:
                obs = self._observation(metric="eurusd_trend_composite", entity="EURUSD", observed_at=observed_at,
                    value=trend, cluster="derived:EURUSD:technical", metadata={"coverage":"price_only"})
                observations.append(obs)
                evidence.append(self._evidence(obs, "eurusd.trend.bullish", trend, "technical_trend",
                    f"EUR/USD multi-horizon trend composite={trend:.3f}"))

            if "UUP" in snapshot.bars:
                u1, u5 = _safe_return(snapshot, "UUP", 13), _safe_return(snapshot, "UUP", 65)
                if u1 is not None and u5 is not None:
                    usd = clamp(-(.45 * clamp(u1 / .012, -1, 1) + .55 * clamp(u5 / .035, -1, 1)), -1, 1)
                    obs = self._observation(metric="broad_usd_support_for_eurusd", entity="EURUSD", observed_at=observed_at,
                        value=usd, cluster="derived:USD:environment", metadata={"proxy":"UUP", "ecb_coverage":False})
                    observations.append(obs)
                    evidence.append(self._evidence(obs, "eurusd.usd_environment.supportive", usd, "usd_environment",
                        f"Inverse UUP momentum support for EUR/USD={usd:.3f}"))

            if "TLT" in snapshot.bars:
                t1, t5 = _safe_return(snapshot, "TLT", 13), _safe_return(snapshot, "TLT", 65)
                if t1 is not None and t5 is not None:
                    rates = clamp(.45 * clamp(t1 / .018, -1, 1) + .55 * clamp(t5 / .045, -1, 1), -1, 1)
                    obs = self._observation(metric="us_rates_pressure_proxy_for_eurusd", entity="EURUSD", observed_at=observed_at,
                        value=rates, cluster="derived:US_RATES:duration_proxy", metadata={"proxy":"TLT", "rate_differential":False, "ecb_coverage":False})
                    observations.append(obs)
                    evidence.append(self._evidence(obs, "eurusd.us_rates_pressure.supportive", rates, "rates_proxy",
                        f"TLT-based US rates-pressure support proxy={rates:.3f}; not an EUR/USD rate differential"))

        if BTC_SYMBOL in snapshot.bars:
            observed_at = iso_z(snapshot.observed_at(BTC_SYMBOL))
            trend = _trend_score(snapshot, BTC_SYMBOL, (.018, .035, .10))
            if trend is not None:
                obs = self._observation(metric="btc_trend_composite", entity="BTC", observed_at=observed_at,
                    value=trend, cluster="derived:BTC:technical", metadata={"coverage":"price_only"})
                observations.append(obs)
                evidence.append(self._evidence(obs, "btc.trend.bullish", trend, "technical_trend",
                    f"BTC multi-horizon trend composite={trend:.3f}"))

            if "HYG" in snapshot.bars and "LQD" in snapshot.bars and "TLT" in snapshot.bars:
                credit_1 = snapshot.ratio_return("HYG", "LQD", 13)
                credit_5 = snapshot.ratio_return("HYG", "LQD", 65)
                tlt_1 = _safe_return(snapshot, "TLT", 13)
                if tlt_1 is not None:
                    liquidity = clamp(.40 * clamp(credit_1 / .006, -1, 1) + .35 * clamp(credit_5 / .018, -1, 1) + .25 * clamp(tlt_1 / .018, -1, 1), -1, 1)
                    obs = self._observation(metric="btc_cross_asset_liquidity_composite", entity="BTC", observed_at=observed_at,
                        value=liquidity, cluster="derived:BTC:cross_asset_liquidity", metadata={"proxies":["HYG/LQD","TLT"], "on_chain_coverage":False})
                    observations.append(obs)
                    evidence.append(self._evidence(obs, "btc.liquidity.supportive", liquidity, "cross_asset_liquidity",
                        f"BTC cross-asset liquidity proxy={liquidity:.3f}; no on-chain inputs"))

            rows = snapshot.bars[BTC_SYMBOL]
            current_vol = _rvol(rows, 13)
            baseline_vol = _rvol(rows, 65)
            if current_vol is not None and baseline_vol is not None and baseline_vol > 1e-9:
                vol_score = clamp((baseline_vol - current_vol) / baseline_vol, -1, 1)
                obs = self._observation(metric="btc_realized_volatility_state", entity="BTC", observed_at=observed_at,
                    value=vol_score, cluster="derived:BTC:volatility", metadata={"current_rvol":round(current_vol,8), "baseline_rvol":round(baseline_vol,8)})
                observations.append(obs)
                evidence.append(self._evidence(obs, "btc.volatility.benign", vol_score, "realized_volatility",
                    f"BTC realized-volatility state={vol_score:.3f}"))

            if "UUP" in snapshot.bars:
                u1, u5 = _safe_return(snapshot, "UUP", 13), _safe_return(snapshot, "UUP", 65)
                if u1 is not None and u5 is not None:
                    usd = clamp(-(.45 * clamp(u1 / .012, -1, 1) + .55 * clamp(u5 / .035, -1, 1)), -1, 1)
                    obs = self._observation(metric="broad_usd_support_for_btc", entity="BTC", observed_at=observed_at,
                        value=usd, cluster="derived:USD:environment", metadata={"proxy":"UUP", "crypto_specific":False})
                    observations.append(obs)
                    evidence.append(self._evidence(obs, "btc.usd_environment.supportive", usd, "usd_environment",
                        f"Inverse UUP momentum support for BTC={usd:.3f}"))

        return AdapterResult(self.name, tuple(observations), tuple(evidence))


def _btc_frozen_vol_cap(snapshot: MarketSnapshot) -> float:
    rows = snapshot.bars[BTC_SYMBOL]
    recent = _rvol(rows, 48)
    # 4% is a conservative floor for a 24h absolute BTC move; the cap may
    # expand with frozen recent volatility but is bounded to avoid vacuous tests.
    return round(max(.04, min(.12, 1.50 * float(recent or .04))), 8)


def outcome_spec(belief_id: str, snapshot: MarketSnapshot) -> Dict[str, Any]:
    if belief_id == "eurusd.trend.bullish":
        return {"kind":"price_above", "symbol":EURUSD_SYMBOL, "reference":snapshot.latest(EURUSD_SYMBOL)}
    if belief_id == "eurusd.usd_environment.supportive":
        return {"kind":"value_below", "symbol":"UUP", "reference":snapshot.latest("UUP"), "threshold":snapshot.latest("UUP")}
    if belief_id == "eurusd.us_rates_pressure.supportive":
        return {"kind":"value_above", "symbol":"TLT", "reference":snapshot.latest("TLT"), "threshold":snapshot.latest("TLT")}
    if belief_id == "btc.trend.bullish":
        return {"kind":"price_above", "symbol":BTC_SYMBOL, "reference":snapshot.latest(BTC_SYMBOL)}
    if belief_id == "btc.liquidity.supportive":
        return {"kind":"credit_duration_supportive", "reference":{"credit_ratio":snapshot.ratio("HYG","LQD"), "TLT":snapshot.latest("TLT")}}
    if belief_id == "btc.volatility.benign":
        return {"kind":"absolute_return_below", "symbol":BTC_SYMBOL, "reference":snapshot.latest(BTC_SYMBOL), "threshold_return":_btc_frozen_vol_cap(snapshot)}
    if belief_id == "btc.usd_environment.supportive":
        return {"kind":"value_below", "symbol":"UUP", "reference":snapshot.latest("UUP"), "threshold":snapshot.latest("UUP")}
    raise KeyError(belief_id)


def evaluate_spec(spec: Mapping[str, Any], values: Mapping[str, float]) -> bool:
    kind = str(spec.get("kind") or "")
    if kind == "value_above":
        return float(values[str(spec["symbol"])]) >= float(spec["threshold"])
    if kind == "absolute_return_below":
        ref = float(spec["reference"])
        if ref == 0:
            return False
        move = abs(float(values[str(spec["symbol"])]) / ref - 1.0)
        return move <= float(spec["threshold_return"])
    if kind == "credit_duration_supportive":
        ref = spec["reference"]
        ratio = float(values["HYG"]) / float(values["LQD"])
        return ratio >= float(ref["credit_ratio"]) and float(values["TLT"]) >= float(ref["TLT"])
    raise ValueError(f"unsupported WES asset outcome spec: {kind}")


def required_symbols(spec: Mapping[str, Any]) -> List[str]:
    kind = str(spec.get("kind") or "")
    if kind in {"value_above", "absolute_return_below"}:
        return [str(spec["symbol"])]
    if kind == "credit_duration_supportive":
        return ["HYG", "LQD", "TLT"]
    raise ValueError(f"unsupported WES asset outcome spec: {kind}")
