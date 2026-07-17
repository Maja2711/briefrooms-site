#!/usr/bin/env python3
"""Initialize and review the public BriefRooms 10K model portfolio.

The script is deliberately deterministic and audit-friendly:
- the first successful run freezes model entry prices and quantities;
- closed history, snapshots and weekly reviews are retained;
- market data gaps are shown as missing values, never as artificial zeroes;
- the model creates review flags, not broker orders.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
WARSAW = ZoneInfo("Europe/Warsaw")
FX_SYMBOLS = {"PLN": None, "USD": "USDPLN=X", "EUR": "EURPLN=X", "DKK": "DKKPLN=X"}
RISK_WORDS = {
    "investigation", "probe", "lawsuit", "antitrust", "fraud", "recall", "downgrade",
    "warning", "misses", "cuts guidance", "guidance cut", "ban", "sanction", "default",
    "cyberattack", "breach", "accounting", "resigns", "geopolitical", "tariff",
    "dochodzenie", "pozew", "ostrzeżenie", "obniża prognozę", "zakaz", "sankcje"
}
POSITIVE_WORDS = {
    "beats", "raises guidance", "upgrade", "buyback", "dividend increase", "approval",
    "record revenue", "partnership", "wyższa prognoza", "podwyższa prognozę", "skup akcji"
}


@dataclass
class MarketRecord:
    symbol: str
    price: float
    market_date: str
    currency: str
    ma50: Optional[float]
    ma200: Optional[float]
    return_6m: Optional[float]
    drawdown_52w: Optional[float]
    volatility_20d: Optional[float]
    history: pd.DataFrame
    next_earnings_date: Optional[str]


def now_local() -> datetime:
    return datetime.now(WARSAW)


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def round_or_none(value: Any, digits: int = 4) -> Optional[float]:
    number = finite(value)
    return round(number, digits) if number is not None else None


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, path)


def normalized_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "Close" not in frame:
        return pd.DataFrame()
    result = frame.copy()
    return result[result["Close"].notna()]


def calendar_date(ticker: yf.Ticker) -> Optional[str]:
    """Read the nearest future earnings date without assuming one yfinance shape."""
    try:
        cal = ticker.calendar
    except Exception:
        return None
    candidates: List[datetime] = []
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date") or cal.get("EarningsDate")
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        for value in values:
            try:
                candidates.append(pd.Timestamp(value).to_pydatetime())
            except Exception:
                pass
    elif isinstance(cal, pd.DataFrame) and not cal.empty:
        for key in ("Earnings Date", "EarningsDate"):
            if key in cal.index:
                for value in cal.loc[key].tolist():
                    try:
                        candidates.append(pd.Timestamp(value).to_pydatetime())
                    except Exception:
                        pass
    today = now_local().date()
    future = sorted({x.date() for x in candidates if x.date() >= today})
    return future[0].isoformat() if future else None


def fetch_market(symbol: str, currency: str, include_earnings: bool = True) -> MarketRecord:
    ticker = yf.Ticker(symbol)
    history = normalized_history(ticker.history(period="1y", interval="1d", auto_adjust=False, actions=True))
    if history.empty:
        raise RuntimeError(f"No market history for {symbol}")
    close = history["Close"].astype(float)
    price = finite(close.iloc[-1])
    if price is None:
        raise RuntimeError(f"No finite close for {symbol}")
    ma50 = finite(close.tail(50).mean()) if len(close) >= 20 else None
    ma200 = finite(close.tail(200).mean()) if len(close) >= 100 else None
    return_6m = None
    if len(close) >= 126:
        base = finite(close.iloc[-126])
        if base:
            return_6m = price / base - 1.0
    high_52w = finite(close.max())
    drawdown = price / high_52w - 1.0 if high_52w else None
    returns = close.pct_change().dropna().tail(20)
    volatility = finite(returns.std(ddof=1) * math.sqrt(252)) if len(returns) >= 10 else None
    market_dt = pd.Timestamp(history.index[-1]).date().isoformat()
    return MarketRecord(
        symbol=symbol,
        price=price,
        market_date=market_dt,
        currency=currency,
        ma50=ma50,
        ma200=ma200,
        return_6m=return_6m,
        drawdown_52w=drawdown,
        volatility_20d=volatility,
        history=history,
        next_earnings_date=calendar_date(ticker) if include_earnings else None,
    )


def fx_rate(currency: str, cache: Dict[str, MarketRecord]) -> Tuple[float, str]:
    if currency == "PLN":
        return 1.0, now_local().date().isoformat()
    symbol = FX_SYMBOLS.get(currency)
    if not symbol:
        raise RuntimeError(f"Unsupported portfolio currency: {currency}")
    if symbol not in cache:
        cache[symbol] = fetch_market(symbol, "PLN", include_earnings=False)
    record = cache[symbol]
    return record.price, record.market_date


def fx_history(currency: str, start: str, end: str, cache: Dict[Tuple[str, str, str], pd.Series]) -> pd.Series:
    if currency == "PLN":
        idx = pd.date_range(start=start, end=end, freq="D")
        return pd.Series(1.0, index=idx)
    symbol = FX_SYMBOLS[currency]
    key = (symbol or "PLN", start, end)
    if key in cache:
        return cache[key]
    frame = normalized_history(yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False, actions=False))
    if frame.empty:
        return pd.Series(dtype=float)
    series = frame["Close"].astype(float)
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    cache[key] = series
    return series


def dividends_in_pln(position: Dict[str, Any], market: MarketRecord, fx_series_cache: Dict[Tuple[str, str, str], pd.Series]) -> float:
    quantity = finite(position.get("quantity"))
    entry_date = position.get("entry_date")
    if not quantity or not entry_date:
        return 0.0
    history = market.history
    first_market_date = pd.Timestamp(history.index[0]).date() if not history.empty else None
    if first_market_date is None or pd.Timestamp(entry_date).date() < first_market_date:
        try:
            history = normalized_history(
                yf.Ticker(str(position.get("market_symbol"))).history(
                    start=(pd.Timestamp(entry_date) - pd.Timedelta(days=7)).date().isoformat(),
                    interval="1d", auto_adjust=False, actions=True
                )
            )
        except Exception:
            history = market.history
    if history.empty or "Dividends" not in history:
        return 0.0
    dividends = history["Dividends"].fillna(0.0)
    dividends = dividends[dividends > 0]
    if dividends.empty:
        return 0.0
    dividends.index = pd.to_datetime(dividends.index).tz_localize(None).normalize()
    dividends = dividends[dividends.index >= pd.Timestamp(entry_date)]
    if dividends.empty:
        return 0.0
    if position.get("currency") == "PLN":
        return float(dividends.sum() * quantity)
    start = (dividends.index.min() - pd.Timedelta(days=7)).date().isoformat()
    end = (dividends.index.max() + pd.Timedelta(days=7)).date().isoformat()
    rates = fx_history(str(position.get("currency")), start, end, fx_series_cache)
    total = 0.0
    for when, per_share in dividends.items():
        if rates.empty:
            rate = finite(position.get("current_fx_to_pln")) or finite(position.get("entry_fx_to_pln")) or 0.0
        else:
            eligible = rates[rates.index <= when]
            rate = finite(eligible.iloc[-1]) if not eligible.empty else finite(rates.iloc[0])
        if rate:
            total += float(per_share) * quantity * rate
    return total


def rss_news(query: str, limit: int = 3) -> List[Dict[str, Any]]:
    encoded = urllib.parse.quote_plus(f"{query} when:10d")
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    request = urllib.request.Request(url, headers={"User-Agent": "BriefRoomsPortfolio/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            root = ET.fromstring(response.read())
    except Exception:
        return []
    items: List[Dict[str, Any]] = []
    for node in root.findall("./channel/item")[:limit]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = (node.findtext("pubDate") or "").strip()
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        lower = title.lower()
        risk_hits = sorted(word for word in RISK_WORDS if word in lower)
        positive_hits = sorted(word for word in POSITIVE_WORDS if word in lower)
        if title and link:
            items.append({
                "title": title,
                "link": link,
                "source": source,
                "published": published,
                "risk_keywords": risk_hits,
                "positive_keywords": positive_hits,
            })
    return items


def technical_score(record: MarketRecord, news: Iterable[Dict[str, Any]]) -> Tuple[int, List[str], List[str]]:
    score = 50
    positives: List[str] = []
    risks: List[str] = []
    if record.ma200 is not None:
        if record.price >= record.ma200:
            score += 10; positives.append("price_above_ma200")
        else:
            score -= 10; risks.append("price_below_ma200")
    if record.ma50 is not None and record.ma200 is not None:
        if record.ma50 >= record.ma200:
            score += 10; positives.append("ma50_above_ma200")
        else:
            score -= 10; risks.append("ma50_below_ma200")
    if record.return_6m is not None:
        if record.return_6m > 0:
            score += 10; positives.append("positive_six_month_momentum")
        else:
            score -= 10; risks.append("negative_six_month_momentum")
    if record.drawdown_52w is not None:
        if record.drawdown_52w > -0.20:
            score += 5; positives.append("drawdown_below_twenty_percent")
        else:
            score -= 5; risks.append("drawdown_above_twenty_percent")
    if record.volatility_20d is not None:
        if record.volatility_20d <= 0.35:
            score += 5; positives.append("contained_short_term_volatility")
        elif record.volatility_20d > 0.55:
            score -= 5; risks.append("elevated_short_term_volatility")
    material = sum(1 for item in news if item.get("risk_keywords"))
    if material:
        score -= min(15, material * 5)
        risks.append("material_news_headline_requires_review")
    return max(0, min(100, int(score))), positives, risks


def review_flag(position: Dict[str, Any], score: int, current_weight: Optional[float], material_news: int) -> str:
    target = finite(position.get("target_weight")) or 0.0
    if material_news >= 2 or score <= 30:
        return "THESIS_REVIEW"
    if current_weight is not None and current_weight > max(target + 0.05, target * 1.5):
        return "TRIM_REVIEW"
    if current_weight is not None and current_weight < max(0.0, target - 0.03) and score >= 70:
        return "ADD_REVIEW"
    return "HOLD"


def initialize_portfolio(data: Dict[str, Any], markets: Dict[str, MarketRecord], fx_cache: Dict[str, MarketRecord]) -> None:
    start_capital = finite(data.get("starting_capital_pln")) or 10000.0
    spent = 0.0
    dates: List[str] = []
    for position in data.get("positions", []):
        record = markets[position["id"]]
        rate, _ = fx_rate(position["currency"], fx_cache)
        allocation = start_capital * float(position["target_weight"])
        quantity = round(allocation / (record.price * rate), 6)
        entry_value = quantity * record.price * rate
        position.update({
            "status": "active",
            "quantity": quantity,
            "entry_date": record.market_date,
            "entry_price": round(record.price, 6),
            "entry_fx_to_pln": round(rate, 6),
            "entry_value_pln": round(entry_value, 2),
        })
        spent += entry_value
        dates.append(record.market_date)
    data["cash_pln"] = round(start_capital - spent, 2)
    data["status"] = "active"
    data["initialization_note_pl"] = "Ceny wejścia zostały zamrożone przy pierwszym udanym przebiegu modelu. Są to ceny zamknięcia z zewnętrznego źródła, nie potwierdzenie wykonania zleceń w XTB."
    data["initialization_note_en"] = "Entry prices were frozen on the first successful model run. They are external closing prices, not confirmation of executions in XTB."
    data["model_entry_date"] = max(dates) if dates else now_local().date().isoformat()

    benchmark = data["benchmark"]
    record = markets["fwia"]
    rate, _ = fx_rate(benchmark["currency"], fx_cache)
    benchmark.update({
        "entry_price": round(record.price, 6),
        "entry_fx_to_pln": round(rate, 6),
        "units": round(start_capital / (record.price * rate), 8),
        "entry_date": record.market_date,
    })


def update_current_state(data: Dict[str, Any], markets: Dict[str, MarketRecord], fx_cache: Dict[str, MarketRecord]) -> Dict[str, Any]:
    fx_series_cache: Dict[Tuple[str, str, str], pd.Series] = {}
    position_value = 0.0
    for position in data.get("positions", []):
        if position.get("status") != "active":
            continue
        record = markets[position["id"]]
        rate, _ = fx_rate(position["currency"], fx_cache)
        quantity = finite(position.get("quantity")) or 0.0
        value = quantity * record.price * rate
        dividends = dividends_in_pln(position, record, fx_series_cache)
        entry_value = finite(position.get("entry_value_pln")) or 0.0
        pnl = value + dividends - entry_value
        pnl_pct = pnl / entry_value if entry_value else None
        news = rss_news(str(position.get("news_query") or position.get("label") or ""), 3)
        score, positives, risks = technical_score(record, news)
        position.update({
            "current_price": round(record.price, 6),
            "current_fx_to_pln": round(rate, 6),
            "current_value_pln": round(value, 2),
            "pnl_pln": round(pnl, 2),
            "pnl_percent": round(pnl_pct, 6) if pnl_pct is not None else None,
            "dividends_pln": round(dividends, 2),
            "market_date": record.market_date,
            "ma50": round_or_none(record.ma50, 6),
            "ma200": round_or_none(record.ma200, 6),
            "return_6m": round_or_none(record.return_6m, 6),
            "drawdown_52w": round_or_none(record.drawdown_52w, 6),
            "volatility_20d": round_or_none(record.volatility_20d, 6),
            "next_earnings_date": record.next_earnings_date,
            "model_score": score,
            "positive_signals": positives,
            "risk_signals": risks,
            "recent_news": news,
        })
        position_value += value

    total_value = position_value + (finite(data.get("cash_pln")) or 0.0)
    for position in data.get("positions", []):
        value = finite(position.get("current_value_pln"))
        weight = value / total_value if value is not None and total_value else None
        material = sum(1 for item in position.get("recent_news", []) if item.get("risk_keywords"))
        position["current_weight"] = round(weight, 6) if weight is not None else None
        position["review_flag"] = review_flag(position, int(position.get("model_score") or 0), weight, material)

    benchmark = data.get("benchmark", {})
    record = markets["fwia"]
    rate, _ = fx_rate(str(benchmark.get("currency") or "EUR"), fx_cache)
    units = finite(benchmark.get("units"))
    if units:
        bench_value = units * record.price * rate
        benchmark.update({
            "current_price": round(record.price, 6),
            "current_fx_to_pln": round(rate, 6),
            "current_value_pln": round(bench_value, 2),
            "return_percent": round(bench_value / float(data["starting_capital_pln"]) - 1.0, 6),
            "market_date": record.market_date,
        })
    dividends_total = sum(finite(p.get("dividends_pln")) or 0.0 for p in data.get("positions", []))
    total_return = total_value + dividends_total - float(data["starting_capital_pln"])
    return {
        "total_value_pln": round(total_value, 2),
        "dividends_pln": round(dividends_total, 2),
        "total_return_pln": round(total_return, 2),
        "total_return_percent": round(total_return / float(data["starting_capital_pln"]), 6),
        "benchmark_value_pln": round_or_none(benchmark.get("current_value_pln"), 2),
        "benchmark_return_percent": round_or_none(benchmark.get("return_percent"), 6),
    }


def upsert_snapshot(data: Dict[str, Any], summary: Dict[str, Any], market_date: str) -> None:
    snapshot = {
        "date": market_date,
        "recorded_at": now_local().isoformat(timespec="seconds"),
        **summary,
        "positions": {
            p["id"]: {
                "value_pln": p.get("current_value_pln"),
                "weight": p.get("current_weight"),
                "pnl_percent": p.get("pnl_percent"),
                "review_flag": p.get("review_flag"),
            }
            for p in data.get("positions", [])
        },
    }
    snapshots = data.setdefault("snapshots", [])
    snapshots[:] = [item for item in snapshots if item.get("date") != market_date]
    snapshots.append(snapshot)
    snapshots.sort(key=lambda item: item.get("date") or "")
    data["snapshots"] = snapshots[-260:]


def weekly_summary_text(data: Dict[str, Any], summary: Dict[str, Any], lang: str) -> str:
    flags = [p for p in data.get("positions", []) if p.get("review_flag") != "HOLD"]
    total_pct = 100 * (finite(summary.get("total_return_percent")) or 0.0)
    bench_pct = 100 * (finite(summary.get("benchmark_return_percent")) or 0.0)
    if lang == "pl":
        intro = f"Portfel modelowy ma wynik {total_pct:+.2f}% od startu wobec {bench_pct:+.2f}% benchmarku."
        if flags:
            names = ", ".join(f"{p['broker_symbol']} ({p['review_flag']})" for p in flags)
            return f"{intro} Do pogłębionego przeglądu oznaczono: {names}. Flaga nie jest automatycznym zleceniem."
        return f"{intro} Żadna pozycja nie przekroczyła obecnie progów wymagających rotacji."
    intro = f"The model portfolio has returned {total_pct:+.2f}% since launch versus {bench_pct:+.2f}% for the benchmark."
    if flags:
        names = ", ".join(f"{p['broker_symbol']} ({p['review_flag']})" for p in flags)
        return f"{intro} The following require deeper review: {names}. A flag is not an automatic order."
    return f"{intro} No position currently exceeds the thresholds requiring rotation."


def upsert_weekly_review(data: Dict[str, Any], summary: Dict[str, Any]) -> None:
    now = now_local()
    iso = now.isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"
    review = {
        "week_id": week_id,
        "reviewed_at": now.isoformat(timespec="seconds"),
        "summary_pl": weekly_summary_text(data, summary, "pl"),
        "summary_en": weekly_summary_text(data, summary, "en"),
        "portfolio": summary,
        "position_flags": [
            {
                "id": p["id"],
                "broker_symbol": p["broker_symbol"],
                "flag": p.get("review_flag"),
                "model_score": p.get("model_score"),
                "current_weight": p.get("current_weight"),
                "target_weight": p.get("target_weight"),
                "next_earnings_date": p.get("next_earnings_date"),
                "risk_signals": p.get("risk_signals", []),
            }
            for p in data.get("positions", [])
        ],
    }
    reviews = data.setdefault("weekly_reviews", [])
    reviews[:] = [item for item in reviews if item.get("week_id") != week_id]
    reviews.append(review)
    reviews.sort(key=lambda item: item.get("week_id") or "")
    data["weekly_reviews"] = reviews[-104:]


def validate_config(data: Dict[str, Any]) -> None:
    weights = [finite(p.get("target_weight")) for p in data.get("positions", [])]
    if not weights or any(value is None or value <= 0 for value in weights):
        raise ValueError("All target weights must be positive finite numbers")
    if abs(sum(value for value in weights if value is not None) - 1.0) > 1e-9:
        raise ValueError("Target weights must sum to exactly 1.0")
    ids = [p.get("id") for p in data.get("positions", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("Position ids must be unique")


def run(mode: str) -> None:
    data = load_json(DATA_PATH)
    validate_config(data)
    fx_cache: Dict[str, MarketRecord] = {}
    markets: Dict[str, MarketRecord] = {}
    errors: List[str] = []
    for position in data.get("positions", []):
        try:
            markets[position["id"]] = fetch_market(
                str(position["market_symbol"]),
                str(position["currency"]),
                include_earnings=position.get("asset_type") == "Stock",
            )
        except Exception as exc:
            errors.append(f"{position.get('market_symbol')}: {exc}")
    if errors:
        data["last_run_error"] = "; ".join(errors)
        data["last_updated_at"] = now_local().isoformat(timespec="seconds")
        write_json_atomic(DATA_PATH, data)
        raise RuntimeError(data["last_run_error"])

    if data.get("status") == "planned" or mode == "initialize":
        initialize_portfolio(data, markets, fx_cache)

    summary = update_current_state(data, markets, fx_cache)
    market_date = max(record.market_date for record in markets.values())
    data.update(summary)
    data["last_market_session"] = market_date
    data["last_updated_at"] = now_local().isoformat(timespec="seconds")
    data["last_run_error"] = None
    upsert_snapshot(data, summary, market_date)
    upsert_weekly_review(data, summary)
    write_json_atomic(DATA_PATH, data)
    print(f"Portfolio 10K updated for {market_date}: {summary['total_value_pln']:.2f} PLN")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "initialize", "review"), default="auto")
    args = parser.parse_args()
    run(args.mode)


if __name__ == "__main__":
    main()
