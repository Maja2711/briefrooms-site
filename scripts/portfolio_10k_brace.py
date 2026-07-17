#!/usr/bin/env python3
"""Build the weekly BRACE challenger snapshot for BriefRooms."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import portfolio_10k_brace_engine as engine
import portfolio_10k_brace_fundamentals as fundamentals
import portfolio_10k_brace_market as market_features
import portfolio_10k_weekly as base

PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
OUTPUT_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace.json"
MODEL_VERSION = "1.0.0"
PILLAR_LABELS = {
    "business_quality": ("Jakość biznesu", "Business quality"),
    "results_revisions": ("Wyniki i rewizje", "Results & revisions"),
    "attractiveness": ("Atrakcyjność wyceny", "Valuation attractiveness"),
    "confirmation": ("Potwierdzenie rynkowe", "Market confirmation"),
    "risk": ("Odporność na ryzyko", "Risk resilience"),
    "context": ("Kontekst rynku", "Market context"),
    "events_information": ("Wydarzenia i informacje", "Events & information"),
}


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def previous_position(previous: Mapping[str, Any], position_id: str) -> Mapping[str, Any]:
    for item in previous.get("positions", []) if isinstance(previous, dict) else []:
        if item.get("id") == position_id:
            return item
    return {}


def strongest_and_weakest(result: Mapping[str, Any]) -> Tuple[str, str]:
    pillars = result.get("pillar_scores") or {}
    if not pillars:
        return "—", "—"
    strongest = max(pillars, key=lambda key: pillars[key])
    weakest = min(pillars, key=lambda key: pillars[key])
    return " / ".join(PILLAR_LABELS[strongest]), " / ".join(PILLAR_LABELS[weakest])


def update_outcomes(
    history: List[Dict[str, Any]], prices: Mapping[str, float],
    benchmark_price: Optional[float], as_of: date,
) -> None:
    for item in history:
        ref_price = engine.finite(item.get("reference_price"))
        ref_benchmark = engine.finite(item.get("reference_benchmark_price"))
        ref_date = item.get("review_date")
        position_id = str(item.get("id") or "")
        if not ref_price or not ref_date or position_id not in prices:
            continue
        elapsed = (as_of - datetime.fromisoformat(str(ref_date)).date()).days
        for horizon in (30, 90):
            key = f"outcome_{horizon}d"
            if elapsed < horizon or item.get(key):
                continue
            asset_return = prices[position_id] / ref_price - 1.0
            benchmark_return = (
                benchmark_price / ref_benchmark - 1.0
                if benchmark_price and ref_benchmark else 0.0
            )
            outcome = engine.score_outcome(item.get("decision", ""), asset_return - benchmark_return)
            outcome.update({
                "asset_return": round(asset_return, 6),
                "benchmark_return": round(benchmark_return, 6),
                "evaluated_at": as_of.isoformat(),
            })
            item[key] = outcome


def score_position(
    position: Dict[str, Any], benchmark: base.MarketRecord,
    context: Mapping[str, Any], previous: Mapping[str, Any], as_of: date,
) -> Dict[str, Any]:
    ticker = yf.Ticker(str(position["market_symbol"]))
    market = base.fetch_market(
        str(position["market_symbol"]), str(position.get("currency") or "USD"),
        include_earnings=position.get("asset_type") == "Stock",
    )
    info = fundamentals.safe_info(ticker)
    scores: Dict[str, Optional[float]] = {}
    completeness: Dict[str, float] = {}
    evidence: List[engine.Evidence] = []

    scores["business_quality"], completeness["business_quality"], items = fundamentals.business_quality(position, info)
    evidence += items
    scores["results_revisions"], completeness["results_revisions"], items = fundamentals.results_revisions(position, info, ticker)
    evidence += items
    scores["attractiveness"], completeness["attractiveness"], items = fundamentals.attractiveness(position, info)
    evidence += items
    scores["confirmation"], completeness["confirmation"], items = market_features.confirmation(market, benchmark)
    evidence += items
    scores["risk"], completeness["risk"], items = market_features.risk_resilience(position, market, info)
    evidence += items
    scores["context"] = float(context.get("score") or 50.0)
    completeness["context"] = 0.9 if context.get("market_date") else 0.3
    scores["events_information"], completeness["events_information"], items, material = market_features.news_evidence(position, market.market_date)
    evidence += items

    result = engine.aggregate_score(scores, completeness, evidence, as_of)
    earnings_date = position.get("next_earnings_date") or market.next_earnings_date
    decision = engine.decide(engine.DecisionInput(
        score=result["score"], confidence=result["confidence"],
        pillar_scores=result["pillar_scores"], contradictions=result["contradictions"],
        current_weight=engine.finite(position.get("current_weight")),
        target_weight=float(position.get("target_weight") or 0.0),
        days_to_earnings=market_features.days_to(earnings_date, as_of),
        material_risk_count=material,
        previous_score=engine.finite(previous.get("score")),
        previous_decision=(previous.get("decision") or {}).get("code") if isinstance(previous.get("decision"), dict) else None,
        asset_type=str(position.get("asset_type") or "Stock"),
    ))
    strongest, weakest = strongest_and_weakest(result)
    clock = engine.thesis_clock(
        position.get("entry_date"), as_of,
        8 if position.get("asset_type") == "ETF" else 4,
        (previous.get("thesis_clock") or {}).get("milestones", []),
    )
    ledger = sorted(evidence, key=lambda item: abs(item.decayed_weight(as_of)), reverse=True)[:12]
    return {
        "id": position.get("id"), "broker_symbol": position.get("broker_symbol"),
        "label": position.get("label"), "asset_type": position.get("asset_type"),
        "target_weight": position.get("target_weight"),
        "current_weight": position.get("current_weight"),
        "market_price": round(market.price, 6), "market_currency": position.get("currency"),
        "market_date": market.market_date, "next_earnings_date": earnings_date,
        **result, "decision": decision.to_dict(),
        "decision_change": bool(previous) and (previous.get("decision") or {}).get("code") != decision.code,
        "strongest_argument": strongest, "largest_risk": weakest,
        "next_catalyst_pl": f"Wyniki: {earnings_date}" if earnings_date else "Kolejny przegląd tygodniowy",
        "next_catalyst_en": f"Earnings: {earnings_date}" if earnings_date else "Next weekly review",
        "thesis_clock": clock,
        "evidence_ledger": [item.to_dict(as_of) for item in ledger],
    }


def build_snapshot(portfolio: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = datetime.now(timezone.utc)
    as_of = timestamp.date()
    context = market_features.market_context()
    benchmark = base.fetch_market(
        str(portfolio.get("benchmark", {}).get("market_symbol") or "FWIA.DE"),
        str(portfolio.get("benchmark", {}).get("currency") or "EUR"), False,
    )
    positions = [
        score_position(position, benchmark, context,
                       previous_position(previous, str(position.get("id"))), as_of)
        for position in portfolio.get("positions", [])
    ]
    score = sum(float(p["score"]) * float(p.get("target_weight") or 0.0) for p in positions)
    confidence = sum(float(p["confidence"]) * float(p.get("target_weight") or 0.0) for p in positions)
    counts: Dict[str, int] = {}
    for position in positions:
        code = position["decision"]["code"]
        counts[code] = counts.get(code, 0) + 1

    history = list(previous.get("decision_history") or [])
    price_map = {str(p["id"]): float(p["market_price"]) for p in positions}
    update_outcomes(history, price_map, benchmark.price, as_of)
    week_id = f"{as_of.isocalendar().year}-W{as_of.isocalendar().week:02d}"
    history = [item for item in history if item.get("week_id") != week_id]
    for position in positions:
        history.append({
            "week_id": week_id, "review_date": as_of.isoformat(),
            "id": position["id"], "broker_symbol": position["broker_symbol"],
            "decision": position["decision"]["code"], "score": position["score"],
            "confidence": position["confidence"],
            "reference_price": position["market_price"],
            "reference_benchmark_price": round(benchmark.price, 6),
            "decision_quality_at_publication": {
                "transparent_rules_passed": True,
                "data_completeness": position["data_completeness"],
                "contradictions_disclosed": bool(position["contradictions"]),
            },
        })

    return {
        "schema_version": "1.0.0", "model_id": "BRACE",
        "model_version": MODEL_VERSION, "mode": "challenger", "status": "live_shadow",
        "generated_at": timestamp.isoformat(timespec="seconds"),
        "review_date": as_of.isoformat(),
        "source_portfolio_id": portfolio.get("portfolio_id"),
        "source_portfolio_status": portfolio.get("status"),
        "weights": engine.PILLAR_WEIGHTS,
        "policy": {
            "no_automatic_orders": True, "negative_evidence_asymmetry": 1.35,
            "add_requires_persistence": True, "earnings_blackout_days": 5,
            "critical_risk_overrides_score": True,
        },
        "market_context": context,
        "portfolio": {
            "score": round(score, 2), "confidence": round(confidence, 2),
            "decision_counts": counts, "positions_reviewed": len(positions),
        },
        "positions": positions, "decision_history": history[-416:],
        "backtest": previous.get("backtest") or {"status": "pending"},
        "limitations_pl": [
            "BRACE działa równolegle do portfela bazowego i nie wykonuje transakcji.",
            "Niska kompletność danych obniża confidence zamiast sztucznie poprawiać score.",
            "Pełne fundamenty historyczne point-in-time nie są zastępowane dzisiejszymi danymi.",
        ],
        "limitations_en": [
            "BRACE runs in parallel with the baseline portfolio and does not execute trades.",
            "Low data coverage reduces confidence instead of artificially lifting the score.",
            "Current fundamentals are never backfilled into historical tests.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    portfolio, previous = load_json(args.portfolio), load_json(args.output)
    if not portfolio.get("positions"):
        raise SystemExit("Portfolio configuration has no positions")
    snapshot = build_snapshot(portfolio, previous)
    write_json(args.output, snapshot)
    print(f"BRACE updated: {snapshot['portfolio']['score']:.2f}/100")


if __name__ == "__main__":
    main()
