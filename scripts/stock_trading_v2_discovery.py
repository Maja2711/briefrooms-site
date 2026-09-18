#!/usr/bin/env python3
"""Continuous broad-market discovery and Opportunity Frontier for Stock Trading v2.

This module deliberately stops before trade admission. It turns a broad audited
universe into a ranked research frontier using resilient daily/intraday history
features. CASH remains an explicit alternative, but no score here can open a
position. Every candidate carries its own freshness/re-evaluation time; there is
no global morning cutoff.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_universe as universe
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_universe as universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_discovery_config.json"
UNIVERSE_ROOT = ROOT / "data/investments/stock_trading_v2_universe"
FRONTIER_ROOT = ROOT / "data/investments/stock_trading_v2_frontier"
SCHEMA_VERSION = "stock-trading-v2-opportunity-frontier-v1"
USER_AGENT = "BriefRooms-Stock-Trading-v2-Discovery/1.0"


@dataclass(frozen=True)
class Bar:
    day: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = _read_json(path)
    if config.get("schema_version") != "stock-trading-v2-discovery-config-v1":
        raise contracts.ContractError("continuous discovery config schema mismatch")
    if config.get("no_global_selection_cutoff") is not True:
        raise contracts.ContractError("v2 discovery must not restore a global selection cutoff")
    if config.get("governance", {}).get("production_decision_influence") is not False:
        raise contracts.ContractError("v2 discovery must remain shadow-only")
    weights = config.get("weights") or {}
    if abs(sum(float(value) for value in weights.values()) - 100.0) > 1e-9:
        raise contracts.ContractError("continuous discovery weights must sum to 100")
    return config


def _request_bytes(url: str, *, timeout: int, attempts: int) -> bytes:
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,*/*",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Yahoo hosts below
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(3.0, 0.5 * (2 ** attempt)))
    raise RuntimeError(f"history request failed: {type(last).__name__}: {last}")


def fetch_yahoo_history(
    symbol: str,
    *,
    range_value: str,
    timeout: int,
    attempts_per_host: int,
    hosts: Sequence[str],
) -> tuple[list[Bar], dict[str, Any]]:
    encoded = urllib.parse.quote(symbol, safe="")
    params = urllib.parse.urlencode(
        {
            "range": range_value,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    failures: list[str] = []
    for host in hosts:
        try:
            raw = _request_bytes(
                f"https://{host}/v8/finance/chart/{encoded}?{params}",
                timeout=timeout,
                attempts=attempts_per_host,
            )
            payload = json.loads(raw.decode("utf-8"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not isinstance(result, Mapping):
                raise ValueError("empty Yahoo chart result")
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            bars: list[Bar] = []
            for index, stamp in enumerate(timestamps):
                values: dict[str, Any] = {}
                for field in ("open", "high", "low", "close", "volume"):
                    series = quote.get(field) or []
                    values[field] = series[index] if index < len(series) else None
                if any(values[field] is None for field in ("open", "high", "low", "close")):
                    continue
                bars.append(
                    Bar(
                        day=datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat(),
                        open=float(values["open"]),
                        high=float(values["high"]),
                        low=float(values["low"]),
                        close=float(values["close"]),
                        volume=int(values["volume"] or 0),
                    )
                )
            bars.sort(key=lambda row: row.day)
            if not bars:
                raise ValueError("Yahoo chart has no valid bars")
            return bars, {"provider": "Yahoo", "host": host, "failures": failures}
        except Exception as exc:
            failures.append(f"{host}:{type(exc).__name__}:{' '.join(str(exc).split())}"[:500])
    raise RuntimeError(f"Yahoo history unavailable for {symbol}: {' | '.join(failures)}")


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _return(closes: Sequence[float], sessions: int) -> float | None:
    if sessions <= 0 or len(closes) <= sessions or closes[-1 - sessions] <= 0:
        return None
    return closes[-1] / closes[-1 - sessions] - 1.0


def _atr_fraction(bars: Sequence[Bar], sessions: int) -> float | None:
    if len(bars) < sessions + 1 or bars[-1].close <= 0:
        return None
    true_ranges: list[float] = []
    for index in range(len(bars) - sessions, len(bars)):
        current = bars[index]
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    return sum(true_ranges) / len(true_ranges) / bars[-1].close


def compute_features(bars: Sequence[Bar], *, config: Mapping[str, Any]) -> dict[str, Any] | None:
    feature_cfg = config.get("features") or {}
    min_sessions = int(config.get("minimum_history_sessions") or 80)
    if len(bars) < min_sessions:
        return None
    closes = [float(row.close) for row in bars]
    volumes = [float(row.volume) for row in bars]
    latest = bars[-1]
    if latest.close <= 0:
        return None

    horizons = [int(value) for value in feature_cfg.get("momentum_horizons_sessions") or [1, 5, 20, 60]]
    returns = {str(h): _return(closes, h) for h in horizons}
    vol_window = int(feature_cfg.get("volatility_sessions") or 20)
    daily_returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(max(1, len(closes) - vol_window + 1), len(closes))
        if closes[index - 1] > 0
    ]
    volatility = statistics.pstdev(daily_returns) if len(daily_returns) >= 2 else 0.0
    atr_fraction = _atr_fraction(bars, int(feature_cfg.get("atr_sessions") or 14))
    turnover_window = int(feature_cfg.get("turnover_sessions") or 20)
    turnover = [row.close * max(0, row.volume) for row in bars[-turnover_window:]]
    median_turnover = _median(turnover)
    median_volume = _median(volumes[-turnover_window:])
    volume_ratio = float(latest.volume) / median_volume if median_volume > 0 else 0.0
    fast = int(feature_cfg.get("trend_fast_sessions") or 20)
    slow = int(feature_cfg.get("trend_slow_sessions") or 60)
    sma_fast = sum(closes[-fast:]) / fast if len(closes) >= fast else None
    sma_slow = sum(closes[-slow:]) / slow if len(closes) >= slow else None
    ret20 = returns.get("20")
    risk_adjusted = (
        float(ret20) / max(float(volatility), 1e-6)
        if ret20 is not None and volatility is not None
        else None
    )
    return {
        "latest_session": latest.day,
        "last_close": latest.close,
        "history_sessions": len(bars),
        "returns": returns,
        "atr_fraction": atr_fraction,
        "realized_volatility_20d": volatility,
        "median_turnover_20d": median_turnover,
        "volume_ratio_20d": volume_ratio,
        "sma_fast": sma_fast,
        "sma_slow": sma_slow,
        "risk_adjusted_momentum_20d": risk_adjusted,
    }


def _percentile_map(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted((float(value), key) for key, value in values.items())
    count = len(ordered)
    result: dict[str, float] = {}
    index = 0
    while index < count:
        end = index
        while end + 1 < count and abs(ordered[end + 1][0] - ordered[index][0]) <= 1e-15:
            end += 1
        midpoint = (index + end) / 2.0
        percentile = 50.0 if count == 1 else 100.0 * midpoint / (count - 1)
        for cursor in range(index, end + 1):
            result[ordered[cursor][1]] = percentile
        index = end + 1
    return result


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _trend_score(features: Mapping[str, Any]) -> float:
    close = float(features.get("last_close") or 0.0)
    fast = features.get("sma_fast")
    slow = features.get("sma_slow")
    if close <= 0 or fast is None or slow is None or float(slow) <= 0:
        return 50.0
    fast_f, slow_f = float(fast), float(slow)
    score = 50.0
    score += 25.0 if close > fast_f else -25.0
    score += 20.0 if fast_f > slow_f else -20.0
    spread = (fast_f / slow_f - 1.0) if slow_f > 0 else 0.0
    score += _clamp(spread * 400.0, -5.0, 5.0)
    return _clamp(score)


def _volatility_quality(features: Mapping[str, Any]) -> float:
    atr = features.get("atr_fraction")
    if atr is None:
        return 40.0
    value = float(atr)
    if value < 0.004:
        return 20.0
    if value < 0.008:
        return 45.0
    if value <= 0.05:
        return _clamp(100.0 - abs(value - 0.025) * 800.0, 65.0, 100.0)
    if value <= 0.08:
        return _clamp(65.0 - (value - 0.05) * 1000.0, 35.0, 65.0)
    return 25.0


def _stage_zero_map(payload: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    rows = payload.get("candidates") or []
    return {
        str(row.get("symbol") or "").upper(): dict(row)
        for row in rows
        if isinstance(row, Mapping) and str(row.get("symbol") or "").strip()
    }


def select_instruments(
    universe_snapshot: Mapping[str, Any],
    *,
    market: str,
    market_config: Mapping[str, Any],
    stage_zero: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    universe.validate_snapshot(universe_snapshot)
    items = [dict(row) for row in universe_snapshot.get("instruments") or [] if isinstance(row, Mapping)]
    if market == "US" and stage_zero:
        stage = _stage_zero_map(stage_zero)
        limit = int(market_config.get("history_candidates_from_stage_zero") or 180)
        ordered = [
            str(row.get("symbol") or "").upper()
            for row in stage_zero.get("candidates") or []
            if isinstance(row, Mapping)
        ][:limit]
        by_listing = {str(row.get("listing_symbol") or "").upper(): row for row in items}
        return [by_listing[symbol] for symbol in ordered if symbol in by_listing]
    return items


def fetch_feature_map(
    instruments: Sequence[Mapping[str, Any]],
    *,
    market_config: Mapping[str, Any],
    global_config: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    network = global_config.get("network") or {}
    hosts = [str(host) for host in network.get("provider_hosts") or ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]]
    workers = min(max(1, int(network.get("workers") or 12)), max(1, len(instruments)))
    timeout = int(network.get("timeout_seconds") or 15)
    attempts = int(network.get("attempts_per_host") or 2)
    range_value = str(market_config.get("history_range") or "1y")
    result: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}

    def one(instrument: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        listing = str(instrument.get("listing_symbol") or "").upper()
        market_symbol = str(instrument.get("market_data_symbol") or listing)
        try:
            bars, provider_meta = fetch_yahoo_history(
                market_symbol,
                range_value=range_value,
                timeout=timeout,
                attempts_per_host=attempts,
                hosts=hosts,
            )
            features = compute_features(bars, config={**global_config, **market_config})
            if features is None:
                return listing, None, "insufficient_history"
            features["provider"] = provider_meta
            return listing, features, None
        except Exception as exc:
            return listing, None, f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:700]

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, instrument) for instrument in instruments]
        for future in as_completed(futures):
            listing, features, error = future.result()
            if features is not None:
                result[listing] = features
            else:
                failures[listing] = error or "unknown"
    return result, {
        "requested": len(instruments),
        "received": len(result),
        "coverage": round(len(result) / max(1, len(instruments)), 6),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "failures": failures,
    }


def build_frontier(
    universe_snapshot: Mapping[str, Any],
    feature_map: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    market: str,
    stage_zero: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    fetch_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    market = market.upper()
    market_cfg = (config.get("markets") or {}).get(market) or {}
    weights = config.get("weights") or {}
    generated = generated_at or _now_utc()
    stage = _stage_zero_map(stage_zero)
    instruments = {
        str(row.get("listing_symbol") or "").upper(): dict(row)
        for row in universe_snapshot.get("instruments") or []
        if isinstance(row, Mapping)
    }
    minimum_turnover = float(market_cfg.get("discovery_minimum_median_turnover") or 0.0)

    eligible: dict[str, dict[str, Any]] = {}
    for symbol, raw_features in feature_map.items():
        if symbol not in instruments:
            continue
        features = dict(raw_features)
        if float(features.get("median_turnover_20d") or 0.0) < minimum_turnover:
            continue
        eligible[symbol] = features

    momentum_raw: dict[str, float] = {}
    risk_raw: dict[str, float] = {}
    liquidity_raw: dict[str, float] = {}
    volume_raw: dict[str, float] = {}
    for symbol, features in eligible.items():
        returns = features.get("returns") or {}
        available = [(1, 0.10), (5, 0.30), (20, 0.35), (60, 0.25)]
        numerator = 0.0
        denominator = 0.0
        for horizon, weight in available:
            value = returns.get(str(horizon))
            if value is not None:
                numerator += float(value) * weight
                denominator += weight
        momentum_raw[symbol] = numerator / denominator if denominator else 0.0
        risk_raw[symbol] = float(features.get("risk_adjusted_momentum_20d") or 0.0)
        liquidity_raw[symbol] = math.log1p(max(0.0, float(features.get("median_turnover_20d") or 0.0)))
        volume_raw[symbol] = float(features.get("volume_ratio_20d") or 0.0)

    momentum_rank = _percentile_map(momentum_raw)
    risk_rank = _percentile_map(risk_raw)
    liquidity_rank = _percentile_map(liquidity_raw)
    volume_rank = _percentile_map(volume_raw)
    candidates: list[dict[str, Any]] = []
    freshness_minutes = int(market_cfg.get("candidate_freshness_minutes") or 120)
    production_liquidity = float(market_cfg.get("production_liquidity_reference") or 0.0)

    for symbol, features in eligible.items():
        stage_row = stage.get(symbol) or {}
        stage_score = float(stage_row.get("stage_zero_score") or 50.0)
        components = {
            "relative_momentum": momentum_rank.get(symbol, 50.0),
            "trend_quality": _trend_score(features),
            "liquidity": liquidity_rank.get(symbol, 50.0),
            "volume_impulse": volume_rank.get(symbol, 50.0),
            "volatility_quality": _volatility_quality(features),
            "risk_adjusted_momentum": risk_rank.get(symbol, 50.0),
            "stage_zero": stage_score,
        }
        score = sum(float(components[key]) * float(weights.get(key, 0.0)) for key in components) / 100.0
        instrument = instruments[symbol]
        candidates.append(
            {
                "symbol": symbol,
                "market_data_symbol": instrument.get("market_data_symbol"),
                "name": instrument.get("name"),
                "exchange": instrument.get("exchange"),
                "security_type": instrument.get("security_type"),
                "sector": stage_row.get("sector") or instrument.get("sector"),
                "industry": stage_row.get("industry") or instrument.get("industry"),
                "country": stage_row.get("country") or instrument.get("country"),
                "opportunity_score": round(score, 6),
                "research_utility_vs_cash": round((score - 50.0) / 50.0, 6),
                "score_components": {key: round(float(value), 6) for key, value in components.items()},
                "features": dict(features),
                "stage_zero": {
                    "rank": stage_row.get("stage_zero_rank"),
                    "lanes": stage_row.get("lanes") or [],
                    "pct_change": stage_row.get("pct_change"),
                    "dollar_volume": stage_row.get("dollar_volume"),
                },
                "liquidity": {
                    "discovery_pass": True,
                    "median_turnover": round(float(features.get("median_turnover_20d") or 0.0), 2),
                    "production_reference_pass": float(features.get("median_turnover_20d") or 0.0) >= production_liquidity,
                    "production_reference": production_liquidity,
                },
                "freshness": {
                    "latest_session": features.get("latest_session"),
                    "observed_at": _iso(generated),
                    "recheck_after": _iso(generated + timedelta(minutes=freshness_minutes)),
                    "global_cutoff": None,
                },
                "admission": {
                    "status": "PENDING_DEEP_EVIDENCE_AND_RISK_PLAN",
                    "production_decision_influence": False,
                },
            }
        )

    candidates.sort(
        key=lambda row: (float(row["opportunity_score"]), float(row["liquidity"]["median_turnover"])),
        reverse=True,
    )
    for rank, row in enumerate(candidates, start=1):
        row["relationship_rank"] = rank

    frontier_size = int(market_cfg.get("frontier_size") or 50)
    relationship_pool_size = max(
        frontier_size,
        int(market_cfg.get("relationship_pool_size") or frontier_size),
    )
    frontier = candidates[:frontier_size]
    for rank, row in enumerate(frontier, start=1):
        row["frontier_rank"] = rank

    relationship_pool: list[dict[str, Any]] = []
    for row in candidates[:relationship_pool_size]:
        features = row.get("features") or {}
        relationship_pool.append({
            "symbol": row.get("symbol"),
            "market_data_symbol": row.get("market_data_symbol"),
            "name": row.get("name"),
            "exchange": row.get("exchange"),
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "country": row.get("country"),
            "relationship_rank": row.get("relationship_rank"),
            "frontier_rank": row.get("frontier_rank"),
            "opportunity_score": row.get("opportunity_score"),
            "score_components": dict(row.get("score_components") or {}),
            "features": {
                "latest_session": features.get("latest_session"),
                "last_close": features.get("last_close"),
                "returns": dict(features.get("returns") or {}),
                "atr_fraction": features.get("atr_fraction"),
                "realized_volatility_20d": features.get("realized_volatility_20d"),
                "volume_ratio_20d": features.get("volume_ratio_20d"),
                "median_turnover_20d": features.get("median_turnover_20d"),
            },
            "freshness": dict(row.get("freshness") or {}),
        })

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "generated_at": _iso(generated),
        "mode": "shadow_no_production_influence",
        "holding_horizon": config.get("holding_horizon"),
        "global_selection_cutoff": None,
        "universe_semantic_sha256": universe_snapshot.get("semantic_sha256"),
        "universe_size": int(universe_snapshot.get("instrument_count") or 0),
        "features_available": len(feature_map),
        "eligible_after_discovery_liquidity": len(eligible),
        "frontier_size": len(frontier),
        "relationship_pool_size": len(relationship_pool),
        "relationship_pool": relationship_pool,
        "cash_alternative": {
            "status": "AVAILABLE",
            "research_utility": float((config.get("frontier") or {}).get("cash_utility") or 0.0),
        },
        "candidates": frontier,
        "fetch_audit": dict(fetch_audit or {}),
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "rank_only_not_recommendation": True,
            "deep_evidence_pending": True,
            "no_forced_trade": True,
            "no_global_selection_cutoff": True,
        },
    }
    body = dict(payload)
    body.pop("frontier_sha256", None)
    payload["frontier_sha256"] = contracts.payload_sha256(body)
    validate_frontier(payload)
    return payload


def validate_frontier(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("opportunity frontier schema mismatch")
    if payload.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("opportunity frontier market unsupported")
    if payload.get("global_selection_cutoff") is not None:
        raise contracts.ContractError("v2 opportunity frontier cannot contain a global cutoff")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("opportunity frontier escaped shadow governance")
    rows = payload.get("candidates")
    if not isinstance(rows, list) or int(payload.get("frontier_size", -1)) != len(rows):
        raise contracts.ContractError("opportunity frontier candidate count mismatch")
    relationship_pool = payload.get("relationship_pool")
    if relationship_pool is not None:
        if not isinstance(relationship_pool, list) or int(payload.get("relationship_pool_size", -1)) != len(relationship_pool):
            raise contracts.ContractError("relationship pool count mismatch")
        relationship_seen: set[str] = set()
        for rank, row in enumerate(relationship_pool, start=1):
            if not isinstance(row, Mapping):
                raise contracts.ContractError("relationship pool row must be an object")
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol in relationship_seen:
                raise contracts.ContractError("relationship pool duplicate/missing symbol")
            relationship_seen.add(symbol)
            if int(row.get("relationship_rank") or 0) != rank:
                raise contracts.ContractError("relationship pool rank mismatch")
    last_score = float("inf")
    seen: set[str] = set()
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise contracts.ContractError("opportunity frontier candidate must be an object")
        symbol = str(row.get("symbol") or "")
        if not symbol or symbol in seen:
            raise contracts.ContractError("opportunity frontier has duplicate/missing symbol")
        seen.add(symbol)
        if int(row.get("frontier_rank") or 0) != rank:
            raise contracts.ContractError("opportunity frontier rank mismatch")
        score = float(row.get("opportunity_score") or 0.0)
        if score > last_score + 1e-9:
            raise contracts.ContractError("opportunity frontier is not sorted")
        last_score = score
        if (row.get("admission") or {}).get("production_decision_influence") is not False:
            raise contracts.ContractError("frontier candidate escaped shadow governance")
    body = dict(payload)
    stored = str(body.pop("frontier_sha256", ""))
    if not stored or contracts.payload_sha256(body) != stored:
        raise contracts.ContractError("opportunity frontier hash mismatch")


def run_market(
    market: str,
    *,
    universe_path: Path | None = None,
    stage_zero_path: Path | None = None,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    market = market.upper()
    config = load_config(config_path)
    market_cfg = (config.get("markets") or {}).get(market)
    if not isinstance(market_cfg, Mapping):
        raise contracts.ContractError(f"missing discovery config for {market}")
    universe_path = universe_path or (UNIVERSE_ROOT / f"{market.lower()}.json")
    universe_snapshot = _read_json(universe_path)
    stage_zero = _read_json(stage_zero_path) if stage_zero_path and stage_zero_path.exists() else None
    instruments = select_instruments(
        universe_snapshot,
        market=market,
        market_config=market_cfg,
        stage_zero=stage_zero,
    )
    features, audit = fetch_feature_map(
        instruments,
        market_config=market_cfg,
        global_config=config,
    )
    minimum_coverage = 0.70 if market == "GPW" else 0.75
    if float(audit.get("coverage") or 0.0) < minimum_coverage:
        raise RuntimeError(f"{market} discovery history coverage too low: {audit.get('coverage')}")
    return build_frontier(
        universe_snapshot,
        features,
        config,
        market=market,
        stage_zero=stage_zero,
        fetch_audit=audit,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "GPW", "us", "gpw"], required=True)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--stage-zero", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    market = str(args.market).upper()
    payload = run_market(
        market,
        universe_path=args.universe,
        stage_zero_path=args.stage_zero,
        config_path=args.config,
    )
    output = args.output or (FRONTIER_ROOT / f"{market.lower()}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "market": market,
                "universe_size": payload["universe_size"],
                "features_available": payload["features_available"],
                "eligible": payload["eligible_after_discovery_liquidity"],
                "frontier_size": payload["frontier_size"],
                "top_symbol": (payload.get("candidates") or [{}])[0].get("symbol") if payload.get("candidates") else None,
                "production_decision_influence": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
