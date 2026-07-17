#!/usr/bin/env python3
"""Fundamental and valuation feature extraction for BRACE."""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd
import yfinance as yf

import portfolio_10k_brace_engine as engine


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def score_linear(value: Optional[float], bad: float, good: float) -> Optional[float]:
    if value is None or not math.isfinite(value) or bad == good:
        return None
    return clamp(100.0 * (value - bad) / (good - bad))


def average(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(clean) / len(clean) if clean else None


def safe_info(ticker: yf.Ticker) -> Dict[str, Any]:
    try:
        payload = ticker.get_info()
    except Exception:
        try:
            payload = ticker.info
        except Exception:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def safe_earnings(ticker: yf.Ticker) -> pd.DataFrame:
    try:
        frame = ticker.get_earnings_dates(limit=8)
    except Exception:
        return pd.DataFrame()
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def number(info: Mapping[str, Any], key: str) -> Optional[float]:
    return engine.finite(info.get(key))


def evidence(
    code: str, pillar: str, direction: int, strength: float, quality: float,
    half_life: int, source: str, pl: str, en: str,
) -> engine.Evidence:
    return engine.Evidence(
        code, pillar, direction, max(0.0, min(1.0, strength)),
        max(0.0, min(1.0, quality)), date.today().isoformat(), half_life,
        source, pl, en,
    )


def business_quality(
    position: Mapping[str, Any], info: Mapping[str, Any]
) -> Tuple[Optional[float], float, List[engine.Evidence]]:
    if position.get("asset_type") == "ETF":
        item = evidence(
            "diversified_index_structure", "business_quality", 1, 0.55, 0.95,
            365, "fund_structure",
            "Szeroka konstrukcja indeksowa ogranicza ryzyko pojedynczej spółki.",
            "Broad index construction limits single-company risk.",
        )
        return 70.0, 0.8, [item]

    revenue = number(info, "revenueGrowth")
    margin = number(info, "operatingMargins")
    roe = number(info, "returnOnEquity")
    fcf = number(info, "freeCashflow")
    market_cap = number(info, "marketCap")
    debt_equity = number(info, "debtToEquity")
    fcf_yield = fcf / market_cap if fcf is not None and market_cap and market_cap > 0 else None
    parts = [
        score_linear(revenue, -0.10, 0.25),
        score_linear(margin, 0.02, 0.35),
        score_linear(roe, 0.00, 0.35),
        score_linear(fcf_yield, -0.02, 0.08),
        score_linear(debt_equity, 250.0, 20.0),
    ]
    items: List[engine.Evidence] = []
    if revenue is not None:
        direction = 1 if revenue >= 0.10 else -1 if revenue < 0 else 0
        items.append(evidence(
            "revenue_growth", "business_quality", direction,
            min(1.0, abs(revenue) / 0.25), 0.8, 120, "company_fundamentals",
            f"Dynamika przychodów: {revenue * 100:.1f}%.",
            f"Revenue growth: {revenue * 100:.1f}%.",
        ))
    if fcf_yield is not None:
        direction = 1 if fcf_yield >= 0.03 else -1 if fcf_yield < 0 else 0
        items.append(evidence(
            "free_cash_flow_yield", "business_quality", direction,
            min(1.0, abs(fcf_yield) / 0.08), 0.75, 120, "company_fundamentals",
            f"Rentowność wolnych przepływów: {fcf_yield * 100:.1f}%.",
            f"Free-cash-flow yield: {fcf_yield * 100:.1f}%.",
        ))
    return average(parts), sum(x is not None for x in parts) / len(parts), items


def results_revisions(
    position: Mapping[str, Any], info: Mapping[str, Any], ticker: yf.Ticker
) -> Tuple[Optional[float], float, List[engine.Evidence]]:
    if position.get("asset_type") == "ETF":
        return 55.0, 0.5, []
    growth = number(info, "earningsGrowth")
    quarterly = number(info, "earningsQuarterlyGrowth")
    recommendation = number(info, "recommendationMean")
    rec_score = score_linear(recommendation, 4.5, 1.5) if recommendation else None
    surprises: List[float] = []
    frame = safe_earnings(ticker)
    for column in ("Surprise(%)", "Surprise %"):
        if not frame.empty and column in frame.columns:
            for raw in frame[column].dropna().head(4):
                value = float(raw)
                surprises.append(value / (100.0 if abs(value) > 2 else 1.0))
            break
    surprise = sum(surprises) / len(surprises) if surprises else None
    parts = [
        score_linear(growth, -0.20, 0.30),
        score_linear(quarterly, -0.25, 0.35),
        rec_score,
        score_linear(surprise, -0.15, 0.15),
    ]
    items: List[engine.Evidence] = []
    if growth is not None:
        direction = 1 if growth >= 0.08 else -1 if growth < 0 else 0
        items.append(evidence(
            "earnings_growth", "results_revisions", direction,
            min(1.0, abs(growth) / 0.30), 0.75, 100, "company_fundamentals",
            f"Dynamika zysku: {growth * 100:.1f}%.",
            f"Earnings growth: {growth * 100:.1f}%.",
        ))
    if surprise is not None:
        direction = 1 if surprise > 0.02 else -1 if surprise < -0.02 else 0
        items.append(evidence(
            "earnings_surprise", "results_revisions", direction,
            min(1.0, abs(surprise) / 0.15), 0.85, 70, "earnings_history",
            f"Średnia ostatnich niespodzianek wynikowych: {surprise * 100:.1f}%.",
            f"Average recent earnings surprise: {surprise * 100:.1f}%.",
        ))
    return average(parts), sum(x is not None for x in parts) / len(parts), items


def attractiveness(
    position: Mapping[str, Any], info: Mapping[str, Any]
) -> Tuple[Optional[float], float, List[engine.Evidence]]:
    if position.get("asset_type") == "ETF":
        return 52.0, 0.45, []
    forward_pe = number(info, "forwardPE")
    trailing_pe = number(info, "trailingPE")
    peg = number(info, "pegRatio")
    sales = number(info, "priceToSalesTrailing12Months")
    fcf = number(info, "freeCashflow")
    market_cap = number(info, "marketCap")
    fcf_yield = fcf / market_cap if fcf is not None and market_cap and market_cap > 0 else None
    parts = [
        score_linear(forward_pe, 55.0, 12.0),
        score_linear(trailing_pe, 65.0, 14.0),
        score_linear(peg, 3.5, 0.8),
        score_linear(sales, 15.0, 1.5),
        score_linear(fcf_yield, -0.01, 0.08),
    ]
    items: List[engine.Evidence] = []
    if forward_pe is not None:
        direction = 1 if forward_pe <= 22 else -1 if forward_pe >= 40 else 0
        items.append(evidence(
            "forward_pe", "attractiveness", direction,
            min(1.0, abs(forward_pe - 28) / 28), 0.65, 45, "market_valuation",
            f"Forward P/E: {forward_pe:.1f}.", f"Forward P/E: {forward_pe:.1f}.",
        ))
    return average(parts), sum(x is not None for x in parts) / len(parts), items
