#!/usr/bin/env python3
"""Prospective portfolio counterfactuals for BRACE autonomous evolution.

Counterfactual portfolios are frozen strictly from decision-time information.
Realised prices are allowed only in the later settlement step.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping

from brace_portfolio_config import EngineConfig

SCHEMA_VERSION = "brace-portfolio-counterfactual-v1"
FORBIDDEN_DECISION_TIME_KEYS = {
    "realized_return",
    "realised_return",
    "actual_return",
    "future_return",
    "later_outcome",
    "outcome_price",
    "exit_price",
    "realized_pnl",
    "realised_pnl",
}


def canonical_sha256(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_prospective_only(value: Any, path: str = "optimization") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_DECISION_TIME_KEYS:
                raise ValueError(f"Look-ahead field is prohibited at freeze time: {path}.{key}")
            _assert_prospective_only(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_prospective_only(child, f"{path}[{index}]")


def _instrument_id(item: Mapping[str, Any]) -> str:
    return str(item.get("instrument_id") or item.get("id") or "")


def _price(item: Mapping[str, Any]) -> float | None:
    for key in ("current_price", "price", "signal_price"):
        raw = item.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def freeze_counterfactual_snapshot(
    analysis: Mapping[str, Any],
    config: EngineConfig,
    *,
    generated_at: datetime | None = None,
) -> Dict[str, Any]:
    optimization = dict(analysis.get("optimization") or {})
    if not optimization.get("comparisons"):
        raise ValueError("BRACE analysis has no portfolio comparisons to freeze")
    _assert_prospective_only(optimization)

    timestamp = generated_at
    if timestamp is None:
        raw_timestamp = analysis.get("generated_at")
        if raw_timestamp:
            timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        else:
            timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    prices: Dict[str, float] = {}
    for item in list(analysis.get("positions") or []) + list(analysis.get("candidates") or []):
        key = _instrument_id(item)
        value = _price(item)
        if key and value is not None:
            prices[key] = value

    comparisons = []
    for item in optimization.get("comparisons") or []:
        comparisons.append(
            {
                "name": str(item.get("name") or "unnamed"),
                "weights": {
                    str(key): round(float(value), 10)
                    for key, value in sorted((item.get("weights") or {}).items())
                },
                "decision_time_metrics": {
                    str(key): value
                    for key, value in sorted((item.get("metrics") or {}).items())
                    if isinstance(value, (int, float, str, bool)) or value is None
                },
            }
        )

    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "prospective_only": True,
        "historical_backfill": False,
        "selected": str(optimization.get("selected") or "current"),
        "signal_prices": dict(sorted(prices.items())),
        "comparisons": comparisons,
        "portfolio_policy_snapshot": dict(optimization.get("portfolio_policy_snapshot") or {}),
        "safety_kernel_snapshot": dict(optimization.get("safety_kernel_snapshot") or {}),
        "transaction_cost_buffer": float(config.transaction_cost_buffer),
        "real_broker_integration": False,
        "trade_execution_authority": False,
    }
    payload["snapshot_id"] = canonical_sha256(payload)
    return payload


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _price_on_or_after(history: Iterable[Mapping[str, Any]], target: date) -> float | None:
    candidates = []
    for row in history:
        day = _parse_day(row.get("date"))
        if day is None or day < target:
            continue
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close > 0:
            candidates.append((day, close))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _portfolio_realized_return(
    comparison: Mapping[str, Any],
    asset_returns: Mapping[str, float],
    transaction_cost_buffer: float,
) -> float | None:
    gross = 0.0
    for key, raw_weight in (comparison.get("weights") or {}).items():
        weight = float(raw_weight)
        if key == "CASH":
            continue
        if weight <= 0:
            continue
        if key not in asset_returns:
            return None
        gross += weight * float(asset_returns[key])
    metrics = comparison.get("decision_time_metrics") or {}
    portfolio_turnover = float(metrics.get("turnover") or 0.0)
    return gross - portfolio_turnover * transaction_cost_buffer


def settle_counterfactual_snapshot(
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    horizon_days: int,
    *,
    as_of: datetime | None = None,
) -> Dict[str, Any] | None:
    if snapshot.get("prospective_only") is not True:
        raise ValueError("Only prospective counterfactual snapshots may be settled")
    generated_at = datetime.fromisoformat(
        str(snapshot.get("generated_at")).replace("Z", "+00:00")
    )
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    target_day = generated_at.date() + timedelta(days=int(horizon_days))
    if as_of.date() < target_day:
        return None

    instruments = market.get("instruments") or {}
    signal_prices = snapshot.get("signal_prices") or {}
    required = {
        key
        for comparison in snapshot.get("comparisons") or []
        for key, value in (comparison.get("weights") or {}).items()
        if key != "CASH" and float(value) > 0
    }
    asset_returns: Dict[str, float] = {}
    missing = []
    for key in sorted(required):
        try:
            start = float(signal_prices.get(key))
        except (TypeError, ValueError):
            start = 0.0
        end = _price_on_or_after((instruments.get(key) or {}).get("history") or [], target_day)
        if start <= 0 or end is None:
            missing.append(key)
            continue
        asset_returns[key] = end / start - 1.0

    transaction_cost = float(snapshot.get("transaction_cost_buffer") or 0.0)
    scored = []
    for comparison in snapshot.get("comparisons") or []:
        net = _portfolio_realized_return(comparison, asset_returns, transaction_cost)
        if net is None:
            continue
        weights = comparison.get("weights") or {}
        scored.append(
            {
                "name": str(comparison.get("name") or "unnamed"),
                "net_return": round(net, 10),
                "cash_weight": round(float(weights.get("CASH", 0.0)), 8),
                "active_positions": sum(
                    1 for key, value in weights.items() if key != "CASH" and float(value) > 0
                ),
                "concentration_hhi": round(
                    sum(float(value) ** 2 for key, value in weights.items() if key != "CASH"),
                    10,
                ),
            }
        )
    if not scored:
        return None

    selected_name = str(snapshot.get("selected") or "current")
    selected = next((row for row in scored if row["name"] == selected_name), None)
    if selected is None:
        return None
    winner = max(scored, key=lambda row: (row["net_return"], row["name"]))
    current = next((row for row in scored if row["name"] == "current"), None)
    payload = {
        "schema_version": "brace-portfolio-counterfactual-outcome-v1",
        "snapshot_id": snapshot.get("snapshot_id"),
        "generated_at": snapshot.get("generated_at"),
        "settled_at": as_of.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "horizon_days": int(horizon_days),
        "selected": selected_name,
        "winner": winner["name"],
        "selected_net_return": selected["net_return"],
        "winner_net_return": winner["net_return"],
        "regret": round(winner["net_return"] - selected["net_return"], 10),
        "selected_vs_current": (
            round(selected["net_return"] - current["net_return"], 10)
            if current is not None
            else None
        ),
        "selected_profile": selected,
        "winner_profile": winner,
        "scored_alternatives": scored,
        "missing_instruments": missing,
        "prospective_only": True,
        "real_broker_integration": False,
    }
    payload["outcome_id"] = canonical_sha256(
        {"snapshot_id": payload["snapshot_id"], "horizon_days": int(horizon_days)}
    )
    return payload
