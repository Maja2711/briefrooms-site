#!/usr/bin/env python3
"""Cost-aware execution layer for the BriefRooms 10K portfolio.

It reuses the audited data/news engine from portfolio_10k_weekly.py while
correcting two accounting details:
- foreign-currency purchases include the public 0.5% XTB FX-cost assumption;
- cumulative gross dividends are held as portfolio cash and included once in NAV.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, List, Tuple

import pandas as pd

import portfolio_10k_weekly as base

DEFAULT_FX_FEE_RATE = 0.005
COST_MODEL_VERSION = "1.0"


def fee_rate(data: Dict[str, Any]) -> float:
    assumptions = data.setdefault("cost_assumptions", {})
    assumptions.setdefault("fx_conversion_fee_rate", DEFAULT_FX_FEE_RATE)
    assumptions.setdefault(
        "note_pl",
        "Model zakłada 0,5% kosztu przewalutowania przy modelowym zakupie instrumentu w walucie obcej. Dywidendy są pokazywane brutto przed podatkiem u źródła.",
    )
    assumptions.setdefault(
        "note_en",
        "The model assumes a 0.5% FX conversion cost on each foreign-currency model purchase. Dividends are shown gross before withholding tax.",
    )
    return base.finite(assumptions.get("fx_conversion_fee_rate")) or DEFAULT_FX_FEE_RATE


def initialize_portfolio(
    data: Dict[str, Any],
    markets: Dict[str, base.MarketRecord],
    fx_cache: Dict[str, base.MarketRecord],
) -> None:
    start_capital = base.finite(data.get("starting_capital_pln")) or 10000.0
    fx_fee = fee_rate(data)
    spent = 0.0
    dates: List[str] = []

    for position in data.get("positions", []):
        record = markets[position["id"]]
        rate, _ = base.fx_rate(position["currency"], fx_cache)
        allocation = start_capital * float(position["target_weight"])
        applied_fee = fx_fee if position.get("currency") != "PLN" else 0.0
        unit_notional_pln = record.price * rate
        quantity = round(allocation / (unit_notional_pln * (1.0 + applied_fee)), 6)
        entry_notional = quantity * unit_notional_pln
        entry_fee = entry_notional * applied_fee
        entry_value = entry_notional + entry_fee
        position.update({
            "status": "active",
            "quantity": quantity,
            "entry_date": record.market_date,
            "entry_price": round(record.price, 6),
            "entry_fx_to_pln": round(rate, 6),
            "entry_notional_pln": round(entry_notional, 2),
            "entry_fee_pln": round(entry_fee, 2),
            "entry_value_pln": round(entry_value, 2),
        })
        spent += entry_value
        dates.append(record.market_date)

    base_cash = round(start_capital - spent, 2)
    data["base_cash_pln"] = base_cash
    data["cash_pln"] = base_cash
    data["cash_balance_pln"] = base_cash
    data["status"] = "active"
    data["cost_model_version"] = COST_MODEL_VERSION
    data["model_entry_date"] = max(dates) if dates else base.now_local().date().isoformat()
    data["initialization_note_pl"] = (
        "Ceny wejścia zostały zamrożone przy pierwszym udanym przebiegu modelu. "
        "To zewnętrzne ceny zamknięcia, a nie potwierdzenie zleceń w XTB. "
        "Wielkość pozycji uwzględnia założony koszt przewalutowania 0,5%."
    )
    data["initialization_note_en"] = (
        "Entry prices were frozen on the first successful model run. "
        "They are external closing prices, not XTB execution confirmations. "
        "Position sizing includes the assumed 0.5% FX conversion cost."
    )
    data["broker_note_pl"] = (
        "Symbole odpowiadają instrumentom wyszukiwanym w XTB. Ceny modelowe mogą różnić się od BID/ASK brokera. "
        "Model uwzględnia 0,5% kosztu przewalutowania przy zakupie; dywidendy pokazuje brutto przed podatkiem."
    )
    data["broker_note_en"] = (
        "Symbols match instruments searched in XTB. Model prices may differ from broker bid/ask quotes. "
        "The model applies a 0.5% purchase FX conversion cost; dividends are shown gross before tax."
    )

    benchmark = data["benchmark"]
    record = markets["fwia"]
    rate, _ = base.fx_rate(benchmark["currency"], fx_cache)
    applied_fee = fx_fee if benchmark.get("currency") != "PLN" else 0.0
    unit_notional_pln = record.price * rate
    units = start_capital / (unit_notional_pln * (1.0 + applied_fee))
    benchmark_notional = units * unit_notional_pln
    benchmark.update({
        "entry_price": round(record.price, 6),
        "entry_fx_to_pln": round(rate, 6),
        "entry_fee_pln": round(benchmark_notional * applied_fee, 2),
        "units": round(units, 8),
        "entry_date": record.market_date,
    })


def update_current_state(
    data: Dict[str, Any],
    markets: Dict[str, base.MarketRecord],
    fx_cache: Dict[str, base.MarketRecord],
) -> Dict[str, Any]:
    fx_series_cache: Dict[Tuple[str, str, str], pd.Series] = {}
    position_value = 0.0

    for position in data.get("positions", []):
        if position.get("status") != "active":
            continue
        record = markets[position["id"]]
        rate, _ = base.fx_rate(position["currency"], fx_cache)
        quantity = base.finite(position.get("quantity")) or 0.0
        value = quantity * record.price * rate
        dividends = base.dividends_in_pln(position, record, fx_series_cache)
        entry_value = base.finite(position.get("entry_value_pln")) or 0.0
        pnl = value + dividends - entry_value
        pnl_pct = pnl / entry_value if entry_value else None
        news = base.rss_news(str(position.get("news_query") or position.get("label") or ""), 3)
        score, positives, risks = base.technical_score(record, news)
        position.update({
            "current_price": round(record.price, 6),
            "current_fx_to_pln": round(rate, 6),
            "current_value_pln": round(value, 2),
            "pnl_pln": round(pnl, 2),
            "pnl_percent": round(pnl_pct, 6) if pnl_pct is not None else None,
            "dividends_pln": round(dividends, 2),
            "market_date": record.market_date,
            "ma50": base.round_or_none(record.ma50, 6),
            "ma200": base.round_or_none(record.ma200, 6),
            "return_6m": base.round_or_none(record.return_6m, 6),
            "drawdown_52w": base.round_or_none(record.drawdown_52w, 6),
            "volatility_20d": base.round_or_none(record.volatility_20d, 6),
            "next_earnings_date": record.next_earnings_date,
            "model_score": score,
            "positive_signals": positives,
            "risk_signals": risks,
            "recent_news": news,
        })
        position_value += value

    dividends_total = sum(base.finite(p.get("dividends_pln")) or 0.0 for p in data.get("positions", []))
    base_cash = base.finite(data.get("base_cash_pln"))
    if base_cash is None:
        base_cash = base.finite(data.get("cash_pln")) or 0.0
        data["base_cash_pln"] = round(base_cash, 2)
    cash_balance = base_cash + dividends_total
    total_value = position_value + cash_balance

    for position in data.get("positions", []):
        value = base.finite(position.get("current_value_pln"))
        weight = value / total_value if value is not None and total_value else None
        material = sum(1 for item in position.get("recent_news", []) if item.get("risk_keywords"))
        position["current_weight"] = round(weight, 6) if weight is not None else None
        position["review_flag"] = base.review_flag(
            position, int(position.get("model_score") or 0), weight, material
        )

    benchmark = data.get("benchmark", {})
    record = markets["fwia"]
    rate, _ = base.fx_rate(str(benchmark.get("currency") or "EUR"), fx_cache)
    units = base.finite(benchmark.get("units"))
    if units:
        benchmark_value = units * record.price * rate
        benchmark.update({
            "current_price": round(record.price, 6),
            "current_fx_to_pln": round(rate, 6),
            "current_value_pln": round(benchmark_value, 2),
            "return_percent": round(
                benchmark_value / float(data["starting_capital_pln"]) - 1.0, 6
            ),
            "market_date": record.market_date,
        })

    total_return = total_value - float(data["starting_capital_pln"])
    return {
        "total_value_pln": round(total_value, 2),
        "cash_pln": round(cash_balance, 2),
        "cash_balance_pln": round(cash_balance, 2),
        "base_cash_pln": round(base_cash, 2),
        "dividends_pln": round(dividends_total, 2),
        "total_return_pln": round(total_return, 2),
        "total_return_percent": round(total_return / float(data["starting_capital_pln"]), 6),
        "benchmark_value_pln": base.round_or_none(benchmark.get("current_value_pln"), 2),
        "benchmark_return_percent": base.round_or_none(benchmark.get("return_percent"), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "initialize", "review"), default="auto")
    args = parser.parse_args()
    base.initialize_portfolio = initialize_portfolio
    base.update_current_state = update_current_state
    base.run(args.mode)


if __name__ == "__main__":
    main()
