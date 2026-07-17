#!/usr/bin/env python3
"""Remove irrelevant and duplicated headlines from the public 10K portfolio.

Google News often syndicates the same wire story under several publishers and
can return unrelated ETF articles. This pass keeps only entity-relevant titles,
deduplicates syndicated copies and recalculates review flags. It never invents
news and never turns missing headlines into a positive signal.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd

import portfolio_10k_weekly as base

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "investments" / "portfolio_10k.json"

MATCH_TERMS = {
    "fwia": ("invesco ftse all-world", "ftse all-world", "fwia"),
    "zprv": ("small-cap value", "small cap value", "zprv", "spdr msci usa small cap value"),
    "googl": ("alphabet", "google", "youtube", "gemini", "waymo"),
    "amzn": ("amazon", "aws", "prime video"),
    "tsm": ("tsmc", "taiwan semiconductor"),
    "visa": (
        "visa inc", "visa earnings", "visa stock", "visa shares", "visa payments",
        "visa card", "visa profit", "visa revenue", "visa antitrust"
    ),
    "spgi": ("s&p global", "spgi", "s&p ratings", "standard & poor's"),
    "novo": ("novo nordisk", "wegovy", "ozempic", "cagrisema"),
}
GENERIC_BAD_PREFIXES = ("etfs investing in ", "funds holding ")


def headline_key(title: str) -> str:
    """Normalize the story title while removing a final publisher suffix."""
    base_title = re.sub(r"\s+-\s+[^-]+$", "", title.strip())
    return re.sub(r"[^a-z0-9]+", " ", base_title.lower()).strip()


def relevant(position_id: str, title: str) -> bool:
    lower = title.lower().strip()
    if lower.startswith(GENERIC_BAD_PREFIXES):
        return False
    terms = MATCH_TERMS.get(position_id, ())
    return not terms or any(term in lower for term in terms)


def clean_news(position_id: str, items: Iterable[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title or not relevant(position_id, title):
            continue
        key = headline_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
        if len(cleaned) >= limit:
            break
    return cleaned


def market_record(position: Dict[str, Any]) -> base.MarketRecord:
    return base.MarketRecord(
        symbol=str(position.get("market_symbol") or position.get("broker_symbol") or ""),
        price=base.finite(position.get("current_price")) or 0.0,
        market_date=str(position.get("market_date") or ""),
        currency=str(position.get("currency") or ""),
        ma50=base.finite(position.get("ma50")),
        ma200=base.finite(position.get("ma200")),
        return_6m=base.finite(position.get("return_6m")),
        drawdown_52w=base.finite(position.get("drawdown_52w")),
        volatility_20d=base.finite(position.get("volatility_20d")),
        history=pd.DataFrame(),
        next_earnings_date=position.get("next_earnings_date"),
    )


def refresh_position(position: Dict[str, Any]) -> None:
    items = clean_news(str(position.get("id") or ""), position.get("recent_news") or [])
    score, positives, risks = base.technical_score(market_record(position), items)
    material = sum(1 for item in items if item.get("risk_keywords"))
    weight = base.finite(position.get("current_weight"))
    position["recent_news"] = items
    position["model_score"] = score
    position["positive_signals"] = positives
    position["risk_signals"] = risks
    position["review_flag"] = base.review_flag(position, score, weight, material)


def update_latest_history(data: Dict[str, Any]) -> None:
    positions = data.get("positions", [])
    by_id = {p.get("id"): p for p in positions}
    if data.get("snapshots"):
        snapshot = data["snapshots"][-1]
        for position_id, entry in (snapshot.get("positions") or {}).items():
            position = by_id.get(position_id)
            if position:
                entry["review_flag"] = position.get("review_flag")
    if data.get("weekly_reviews"):
        review = data["weekly_reviews"][-1]
        summary = {
            "total_value_pln": data.get("total_value_pln"),
            "cash_pln": data.get("cash_pln"),
            "dividends_pln": data.get("dividends_pln"),
            "total_return_pln": data.get("total_return_pln"),
            "total_return_percent": data.get("total_return_percent"),
            "benchmark_value_pln": data.get("benchmark_value_pln"),
            "benchmark_return_percent": data.get("benchmark_return_percent"),
        }
        review["portfolio"] = summary
        review["summary_pl"] = base.weekly_summary_text(data, summary, "pl")
        review["summary_en"] = base.weekly_summary_text(data, summary, "en")
        review["position_flags"] = [
            {
                "id": p.get("id"),
                "broker_symbol": p.get("broker_symbol"),
                "flag": p.get("review_flag"),
                "model_score": p.get("model_score"),
                "current_weight": p.get("current_weight"),
                "target_weight": p.get("target_weight"),
                "next_earnings_date": p.get("next_earnings_date"),
                "risk_signals": p.get("risk_signals", []),
            }
            for p in positions
        ]


def main() -> None:
    data = base.load_json(DATA_PATH)
    for position in data.get("positions", []):
        refresh_position(position)
    update_latest_history(data)
    data["news_quality_note_pl"] = "Nagłówki są filtrowane pod kątem zgodności z instrumentem i deduplikowane; brak nagłówka nie jest sygnałem pozytywnym."
    data["news_quality_note_en"] = "Headlines are entity-filtered and deduplicated; the absence of a headline is not treated as a positive signal."
    base.write_json_atomic(DATA_PATH, data)
    print("Portfolio news relevance and duplicate filter applied")


if __name__ == "__main__":
    main()
