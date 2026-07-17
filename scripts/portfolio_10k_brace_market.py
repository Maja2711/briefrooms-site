#!/usr/bin/env python3
"""Market, risk, context and event feature extraction for BRACE."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

import portfolio_10k_brace_engine as engine
import portfolio_10k_weekly as base
from portfolio_10k_brace_fundamentals import average, evidence, score_linear


def market_context() -> Dict[str, Any]:
    records: Dict[str, Any] = {}
    for key, symbol in {"sp500": "^GSPC", "vix": "^VIX", "ten_year": "^TNX"}.items():
        try:
            records[key] = base.fetch_market(symbol, "USD", include_earnings=False)
        except Exception:
            records[key] = None
    sp, vix, ten = records.get("sp500"), records.get("vix"), records.get("ten_year")
    parts: List[Optional[float]] = []
    if sp:
        parts += [
            70.0 if sp.price >= (sp.ma200 or sp.price) else 30.0,
            score_linear(sp.return_6m, -0.15, 0.18),
            score_linear(sp.drawdown_52w, -0.30, 0.0),
        ]
    if vix:
        parts.append(score_linear(vix.price, 35.0, 13.0))
    if ten:
        parts.append(score_linear(ten.price, 6.0, 2.5))
    score = average(parts) or 50.0
    regime = "risk_on" if score >= 65 else "risk_off" if score <= 38 else "mixed"
    return {
        "score": round(score, 2), "regime": regime,
        "sp500_price": round(sp.price, 4) if sp else None,
        "sp500_ma200": round(sp.ma200, 4) if sp and sp.ma200 else None,
        "vix": round(vix.price, 4) if vix else None,
        "us_10y": round(ten.price, 4) if ten else None,
        "market_date": max([r.market_date for r in records.values() if r], default=None),
    }


def confirmation(
    market: base.MarketRecord, benchmark: base.MarketRecord
) -> Tuple[Optional[float], float, List[engine.Evidence]]:
    ma50 = market.price / market.ma50 - 1 if market.ma50 else None
    ma200 = market.price / market.ma200 - 1 if market.ma200 else None
    relative = (
        market.return_6m - benchmark.return_6m
        if market.return_6m is not None and benchmark.return_6m is not None else None
    )
    parts = [
        score_linear(ma50, -0.15, 0.15), score_linear(ma200, -0.25, 0.25),
        score_linear(market.return_6m, -0.25, 0.35), score_linear(relative, -0.20, 0.20),
    ]
    items: List[engine.Evidence] = []
    if ma200 is not None:
        item = engine.Evidence(
            "price_vs_ma200", "confirmation", 1 if ma200 >= 0 else -1,
            min(1.0, abs(ma200) / 0.25), 0.75, market.market_date, 35,
            "market_data", f"Cena jest {ma200 * 100:+.1f}% względem MA200.",
            f"Price is {ma200 * 100:+.1f}% versus MA200.",
        )
        items.append(item)
    if relative is not None:
        items.append(engine.Evidence(
            "relative_strength_6m", "confirmation", 1 if relative >= 0 else -1,
            min(1.0, abs(relative) / 0.20), 0.75, market.market_date, 45,
            "market_data", f"Relatywna stopa zwrotu 6M: {relative * 100:+.1f} pp.",
            f"Six-month relative return: {relative * 100:+.1f} pp.",
        ))
    return average(parts), sum(x is not None for x in parts) / len(parts), items


def risk_resilience(
    position: Mapping[str, Any], market: base.MarketRecord, info: Mapping[str, Any]
) -> Tuple[Optional[float], float, List[engine.Evidence]]:
    beta = engine.finite(info.get("beta"))
    debt = engine.finite(info.get("debtToEquity"))
    current = engine.finite(position.get("current_weight"))
    target = float(position.get("target_weight") or 0.0)
    concentration = current / target if current is not None and target > 0 else 1.0
    parts = [
        score_linear(market.volatility_20d, 0.75, 0.15),
        score_linear(market.drawdown_52w, -0.50, 0.0),
        score_linear(beta, 2.0, 0.6), score_linear(debt, 300.0, 20.0),
        score_linear(concentration, 1.8, 0.8),
    ]
    items: List[engine.Evidence] = []
    if market.drawdown_52w is not None:
        direction = -1 if market.drawdown_52w <= -0.25 else 1 if market.drawdown_52w > -0.10 else 0
        items.append(engine.Evidence(
            "drawdown_52w", "risk", direction,
            min(1.0, abs(market.drawdown_52w) / 0.45), 0.8,
            market.market_date, 40, "market_data",
            f"Obsunięcie od szczytu 52T: {market.drawdown_52w * 100:.1f}%.",
            f"Drawdown from 52-week high: {market.drawdown_52w * 100:.1f}%.",
        ))
    return average(parts), sum(x is not None for x in parts) / len(parts), items


def _published(item: Mapping[str, Any], fallback: str) -> str:
    try:
        return pd.Timestamp(item.get("published")).date().isoformat()
    except Exception:
        return fallback


def news_evidence(
    position: Mapping[str, Any], fallback_date: str
) -> Tuple[float, float, List[engine.Evidence], int]:
    items = list(position.get("recent_news") or [])
    output: List[engine.Evidence] = []
    signed: List[float] = []
    material = 0
    for index, item in enumerate(items[:5]):
        risk = bool(item.get("risk_keywords"))
        positive = bool(item.get("positive_keywords"))
        if not risk and not positive:
            continue
        direction = -1 if risk else 1
        material += int(risk)
        source = str(item.get("source") or "news")
        quality = 0.9 if source.lower() in {"reuters", "company release"} else 0.65
        title = str(item.get("title") or "")
        output.append(engine.Evidence(
            f"news_{index}", "events_information", direction,
            0.8 if risk else 0.55, quality, _published(item, fallback_date),
            35 if risk else 25, source, title, title,
        ))
        signed.append(-35.0 if risk else 25.0)
    score = max(0.0, min(100.0, 50.0 + sum(signed) / max(1, len(signed)))) if signed else 50.0
    return score, 0.7 if items else 0.25, output, material


def days_to(value: Optional[str], as_of: date) -> Optional[int]:
    if not value:
        return None
    try:
        return (pd.Timestamp(value).date() - as_of).days
    except Exception:
        return None
