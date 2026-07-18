#!/usr/bin/env python3
"""Generate append-only material-event reports for active 10K positions.

The generator is deliberately deterministic. It creates price alerts from
confirmed daily closes and accepts news events only when a source adapter
provides complete bilingual factual copy. Missing data never becomes a report.
"""
from __future__ import annotations

import argparse
import copy
import re
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

import portfolio_10k_news_quality as news_quality
import portfolio_10k_weekly as base

ROOT = Path(__file__).resolve().parents[1]
REPORTS_PATH = ROOT / "data" / "investments" / "portfolio_10k_material_reports.json"
SCHEMA_VERSION = "1.0.0"
DEFAULT_DAILY_MOVE_THRESHOLD = 0.07

NEWS_PATTERNS = (
    ("GUIDANCE", re.compile(r"\b(raises?|cuts?|lowers?|withdraws?) guidance\b", re.I)),
    ("EARNINGS", re.compile(r"\b(earnings|quarterly results?|annual results?|revenue|profit)\b", re.I)),
    ("ANALYST_CHANGE", re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|price target)\b", re.I)),
    ("REGULATORY", re.compile(r"\b(regulator|regulatory|antitrust|investigation|fine|approval)\b", re.I)),
    ("POLITICAL", re.compile(r"\b(tariff|sanction|export control|government ban)\b", re.I)),
    ("OPERATIONS", re.compile(r"\b(recall|cyberattack|outage|factory|production halt|supply disruption)\b", re.I)),
    ("DIVIDEND", re.compile(r"\b(dividend|special distribution)\b", re.I)),
    ("BUYBACK", re.compile(r"\b(buyback|share repurchase)\b", re.I)),
)


def empty_payload(portfolio_id: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "portfolio_id": portfolio_id,
        "last_updated_at": None,
        "reports": [],
    }


def is_https_url(value: Any) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def daily_move(previous_close: Any, current_close: Any) -> Optional[float]:
    previous = base.finite(previous_close)
    current = base.finite(current_close)
    if previous is None or previous <= 0 or current is None or current <= 0:
        return None
    return current / previous - 1.0


def daily_move_from_history(history: pd.DataFrame) -> Optional[float]:
    if history is None or history.empty or "Close" not in history:
        return None
    closes = history["Close"].dropna()
    if len(closes) < 2:
        return None
    return daily_move(closes.iloc[-2], closes.iloc[-1])


def _timestamp_for_last_bar(history: pd.DataFrame) -> Optional[str]:
    try:
        stamp = pd.Timestamp(history.index[-1])
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(timezone.utc)
        else:
            stamp = stamp.tz_convert(timezone.utc)
        return stamp.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _round(value: Any, digits: int = 6) -> Optional[float]:
    number = base.finite(value)
    return round(number, digits) if number is not None else None


def position_snapshot(
    position: Dict[str, Any], current_price: Any, current_fx_to_pln: Any
) -> Optional[Dict[str, Any]]:
    quantity = base.finite(position.get("quantity"))
    entry_price = base.finite(position.get("entry_price"))
    entry_fx = base.finite(position.get("entry_fx_to_pln"))
    price = base.finite(current_price)
    current_fx = base.finite(current_fx_to_pln)
    if not quantity or quantity <= 0 or not entry_price or entry_price <= 0:
        return None

    cost_local = quantity * entry_price
    market_local = quantity * price if price and price > 0 else None
    pnl_local = market_local - cost_local if market_local is not None else None
    pnl_percent = pnl_local / cost_local if pnl_local is not None and cost_local else None
    entry_fee = base.finite(position.get("entry_fee_pln")) or 0.0
    cost_pln = base.finite(position.get("entry_value_pln"))
    if cost_pln is None and entry_fx and entry_fx > 0:
        cost_pln = cost_local * entry_fx + entry_fee
    market_pln = market_local * current_fx if market_local is not None and current_fx and current_fx > 0 else None
    pnl_pln = market_pln - cost_pln if market_pln is not None and cost_pln is not None else None
    instrument_effect = (
        quantity * (price - entry_price) * entry_fx
        if price and price > 0 and entry_fx and entry_fx > 0 else None
    )
    fx_effect = (
        quantity * price * (current_fx - entry_fx)
        if price and price > 0 and entry_fx and entry_fx > 0 and current_fx and current_fx > 0
        else None
    )
    return {
        "quantity": _round(quantity, 8),
        "entry_price": _round(entry_price),
        "entry_fx_to_pln": _round(entry_fx),
        "cost_basis_local": _round(cost_local, 4),
        "market_value_local": _round(market_local, 4),
        "unrealized_pnl_local": _round(pnl_local, 4),
        "unrealized_pnl_percent": _round(pnl_percent),
        "position_currency": position.get("currency"),
        "current_fx_to_pln": _round(current_fx),
        "cost_basis_pln": _round(cost_pln, 2),
        "market_value_pln": _round(market_pln, 2),
        "unrealized_pnl_pln": _round(pnl_pln, 2),
        "instrument_effect_pln": _round(instrument_effect, 2),
        "fx_effect_pln": _round(fx_effect, 2),
        "entry_fee_pln": _round(entry_fee, 2),
    }


def _price_source_url(position: Dict[str, Any]) -> str:
    symbol = urllib.parse.quote(str(position.get("market_symbol") or ""), safe="")
    return f"https://finance.yahoo.com/quote/{symbol}"


def _impact_details(move: float) -> Tuple[str, str, int, str]:
    absolute = abs(move)
    severity = "CRITICAL" if absolute >= 0.15 else "HIGH" if absolute >= 0.10 else "MEDIUM"
    impact = "POSITIVE" if move > 0 else "NEGATIVE"
    score = 5 if absolute >= 0.15 else 4 if absolute >= 0.10 else 3
    action = "THESIS_REVIEW" if move <= -0.10 else "HOLD"
    return severity, impact, score, action


def create_price_alert(
    position: Dict[str, Any], record: base.MarketRecord, fx_to_pln: float
) -> Optional[Dict[str, Any]]:
    move = daily_move_from_history(record.history)
    monitoring = position.get("report_monitoring") or {}
    alerts = monitoring.get("price_alerts") or {}
    threshold = base.finite(alerts.get("daily_move_percent"))
    if threshold is None:
        threshold = DEFAULT_DAILY_MOVE_THRESHOLD
    if monitoring.get("enabled", True) is False or move is None or abs(move) + 1e-12 < threshold:
        return None

    closes = record.history["Close"].dropna()
    previous = float(closes.iloc[-2])
    current = float(closes.iloc[-1])
    direction = "up" if move > 0 else "down"
    severity, impact, score, action = _impact_details(move)
    symbol = str(position.get("broker_symbol") or "")
    percentage_pl = f"{abs(move) * 100:.2f}".replace(".", ",")
    percentage_en = f"{abs(move) * 100:.2f}"
    verb_pl = "wzrósł" if move > 0 else "spadł"
    verb_en = "rose" if move > 0 else "fell"
    event_date = record.market_date
    quoted_at = _timestamp_for_last_bar(record.history)
    if not quoted_at:
        return None
    source_url = _price_source_url(position)
    return {
        "id": f"{position.get('id')}-{event_date}-daily-price-{direction}",
        "position_id": position.get("id"),
        "symbol": symbol,
        "published_at": quoted_at,
        "event_date": event_date,
        "type": "PRICE_ALERT",
        "category": "DAILY_MOVE",
        "severity": severity,
        "impact": impact,
        "impact_score": score,
        "title_pl": f"{symbol}: jednodniowy ruch kursu o {percentage_pl}%",
        "title_en": f"{symbol}: a {percentage_en}% one-day price move",
        "summary_pl": (
            f"Kurs zamknięcia {verb_pl} z {previous:.4f} do {current:.4f} "
            f"{record.currency} podczas sesji {event_date}."
        ),
        "summary_en": (
            f"The closing price {verb_en} from {previous:.4f} to {current:.4f} "
            f"{record.currency} in the {event_date} session."
        ),
        "thesis_effect_pl": None,
        "thesis_effect_en": None,
        "model_action": action,
        "quote": {
            "value": _round(current),
            "currency": record.currency,
            "kind": "CLOSE",
            "market": None,
            "quoted_at": quoted_at,
            "source": "Yahoo Finance daily market data",
        },
        "position_snapshot": position_snapshot(position, current, fx_to_pln),
        "sources": [{"label": "Yahoo Finance market data", "url": source_url}],
    }


def classify_material_news(title: str) -> Optional[str]:
    for event_type, pattern in NEWS_PATTERNS:
        if pattern.search(title):
            return event_type
    return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:52]


def create_confirmed_news_report(
    position: Dict[str, Any], item: Dict[str, Any], record: base.MarketRecord, fx_to_pln: float
) -> Optional[Dict[str, Any]]:
    title = str(item.get("title") or "").strip()
    event_type = classify_material_news(title)
    source_url = item.get("link")
    required_copy = ("title_pl", "title_en", "summary_pl", "summary_en")
    if not event_type or not is_https_url(source_url) or any(not item.get(key) for key in required_copy):
        return None
    event_date = str(item.get("event_date") or record.market_date)
    try:
        if date.fromisoformat(event_date).isoformat() != event_date:
            return None
    except ValueError:
        return None
    published_at = str(item.get("published_at") or item.get("published") or "")
    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
    except ValueError:
        return None
    current_fx = base.finite(fx_to_pln)
    quote = None
    quote_timestamp = _timestamp_for_last_bar(record.history)
    if record.price > 0 and quote_timestamp:
        quote = {
            "value": _round(record.price),
            "currency": record.currency,
            "kind": "CLOSE",
            "market": None,
            "quoted_at": quote_timestamp,
            "source": "Yahoo Finance daily market data",
        }
    return {
        "id": f"{position.get('id')}-{event_date}-{_slug(title)}",
        "position_id": position.get("id"),
        "symbol": position.get("broker_symbol"),
        "published_at": published_at,
        "event_date": event_date,
        "type": event_type,
        "category": str(item.get("category") or event_type),
        "severity": str(item.get("severity") or "HIGH"),
        "impact": str(item.get("impact") or "NEUTRAL"),
        "impact_score": item.get("impact_score"),
        "title_pl": item["title_pl"],
        "title_en": item["title_en"],
        "summary_pl": item["summary_pl"],
        "summary_en": item["summary_en"],
        "thesis_effect_pl": item.get("thesis_effect_pl"),
        "thesis_effect_en": item.get("thesis_effect_en"),
        "model_action": str(item.get("model_action") or "HOLD"),
        "quote": quote,
        "position_snapshot": position_snapshot(position, record.price, current_fx),
        "sources": [{"label": str(item.get("source") or "Source"), "url": source_url}],
    }


def deduplicate_new_reports(
    existing: Iterable[Dict[str, Any]], candidates: Iterable[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    ids = {str(report.get("id")) for report in existing}
    event_sources = {
        (report.get("position_id"), report.get("event_date"), report.get("type"), source.get("url"))
        for report in existing
        for source in report.get("sources", [])
    }
    accepted: List[Dict[str, Any]] = []
    for report in candidates:
        report_id = str(report.get("id"))
        keys = {
            (report.get("position_id"), report.get("event_date"), report.get("type"), source.get("url"))
            for source in report.get("sources", [])
        }
        if not report_id or report_id in ids or keys & event_sources:
            continue
        ids.add(report_id)
        event_sources.update(keys)
        accepted.append(report)
    return accepted


def fetch_position_inputs(
    position: Dict[str, Any], fx_cache: Dict[str, base.MarketRecord]
) -> Tuple[base.MarketRecord, float, List[Dict[str, Any]]]:
    record = base.fetch_market(str(position["market_symbol"]), str(position["currency"]))
    fx_to_pln, _ = base.fx_rate(str(position["currency"]), fx_cache)
    headlines = base.rss_news(str(position.get("news_query") or position.get("label") or ""), 10)
    return record, fx_to_pln, news_quality.clean_news(str(position.get("id")), headlines, limit=10)


def generate_candidates(
    position: Dict[str, Any], record: base.MarketRecord, fx_to_pln: float,
    headlines: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    price_alert = create_price_alert(position, record, fx_to_pln)
    if price_alert:
        candidates.append(price_alert)
    for item in headlines:
        report = create_confirmed_news_report(position, item, record, fx_to_pln)
        if report:
            candidates.append(report)
    return candidates


def run(
    portfolio_path: Path = base.DATA_PATH,
    reports_path: Path = REPORTS_PATH,
    fetcher: Callable[[Dict[str, Any], Dict[str, base.MarketRecord]], Tuple[base.MarketRecord, float, List[Dict[str, Any]]]] = fetch_position_inputs,
) -> int:
    portfolio = base.load_json(portfolio_path)
    base.validate_config(portfolio)
    payload = base.load_json(reports_path) if reports_path.exists() else empty_payload(str(portfolio["portfolio_id"]))
    before = copy.deepcopy(payload)
    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload.setdefault("portfolio_id", portfolio["portfolio_id"])
    payload.setdefault("reports", [])
    monitoring_state = payload.setdefault("monitoring_state", {})
    fx_cache: Dict[str, base.MarketRecord] = {}
    candidates: List[Dict[str, Any]] = []
    for position in portfolio.get("positions", []):
        if position.get("status") != "active":
            continue
        monitoring = position.get("report_monitoring") or {}
        if monitoring.get("enabled", True) is False:
            continue
        record, fx_to_pln, headlines = fetcher(position, fx_cache)
        candidates.extend(generate_candidates(position, record, fx_to_pln, headlines))
        monitoring_state[str(position.get("id"))] = {
            "market_date": record.market_date,
            "close": _round(record.price),
            "fx_to_pln": _round(fx_to_pln),
            "headline_keys": sorted({
                news_quality.headline_key(str(item.get("title") or ""))
                for item in headlines if item.get("title")
            }),
        }

    accepted = deduplicate_new_reports(payload["reports"], candidates)
    if accepted:
        payload["reports"].extend(accepted)
    if payload != before:
        payload["last_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        base.write_json_atomic(reports_path, payload)
    print(f"Material reports: {len(accepted)} added, {len(payload['reports'])} total")
    return len(accepted)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    run()


if __name__ == "__main__":
    main()
