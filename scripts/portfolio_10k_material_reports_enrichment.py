#!/usr/bin/env python3
"""Merge verified events and source-backed headlines into material reports.

Facts are never invented. Automated headlines are accepted only with an HTTPS
source, a parseable timestamp and a recognised material-event category.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data/investments/portfolio_10k.json"
REPORTS = ROOT / "data/investments/portfolio_10k_material_reports.json"
VERIFIED = ROOT / "data/investments/portfolio_10k_verified_material_events.json"

PATTERNS = (
    ("GUIDANCE", re.compile(r"\b(guidance|outlook|forecast)\b", re.I)),
    ("EARNINGS", re.compile(r"\b(earnings|quarterly results?|revenue|profit|margin)\b", re.I)),
    ("ANALYST_CHANGE", re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|price target|rating)\b", re.I)),
    ("REGULATORY", re.compile(r"\b(regulator|regulatory|antitrust|investigation|fine|approval|lawsuit|court)\b", re.I)),
    ("OPERATIONS", re.compile(r"\b(clinical trial|phase 3|endpoint|trial failed|fails? to|recall|cyberattack|outage|production halt|supply disruption)\b", re.I)),
    ("DIVIDEND", re.compile(r"\b(dividend|special distribution)\b", re.I)),
    ("BUYBACK", re.compile(r"\b(buyback|share repurchase)\b", re.I)),
)
NEGATIVE = re.compile(r"\b(cut|cuts|lower|miss|failed?|falls?|fell|decline|investigation|fine|lawsuit|downgrade|recall|pressure|slowdown|weak|slump|drop)\b", re.I)
POSITIVE = re.compile(r"\b(beat|beats|raise|approval|record|buyback|partnership|growth|jump|surge|upgrade|expansion|strong)\b", re.I)
PL = {"GUIDANCE":"zmiana prognozy","EARNINGS":"wyniki finansowe","ANALYST_CHANGE":"zmiana oceny analitycznej","REGULATORY":"zdarzenie regulacyjne","OPERATIONS":"zdarzenie operacyjne","DIVIDEND":"dywidenda","BUYBACK":"skup akcji"}
EN = {"GUIDANCE":"guidance update","EARNINGS":"earnings update","ANALYST_CHANGE":"analyst-rating update","REGULATORY":"regulatory event","OPERATIONS":"operational event","DIVIDEND":"dividend update","BUYBACK":"share-buyback update"}


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(default)


def write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        return parsed.scheme == "https" and bool(parsed.netloc)
    except ValueError:
        return False


def stamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def classify(title: str) -> str | None:
    return next((name for name, pattern in PATTERNS if pattern.search(title)), None)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60]


def clean_title(title: str, source: str) -> str:
    text = re.sub(r"\s+", " ", title).strip()
    return re.sub(rf"\s+-\s+{re.escape(source)}\s*$", "", text, flags=re.I)[:280]


def position_snapshot(position: Mapping[str, Any]) -> dict[str, Any]:
    quantity = float(position.get("quantity") or 0)
    price = float(position.get("current_price") or 0)
    entry = float(position.get("entry_price") or 0)
    return {
        "quantity": position.get("quantity"),
        "entry_price": position.get("entry_price"),
        "entry_fx_to_pln": position.get("entry_fx_to_pln"),
        "cost_basis_local": position.get("entry_value_local"),
        "market_value_local": round(quantity * price, 4) if quantity and price else None,
        "unrealized_pnl_local": round(quantity * (price - entry), 4) if quantity and price and entry else None,
        "unrealized_pnl_percent": position.get("pnl_percent"),
        "position_currency": position.get("currency"),
        "current_fx_to_pln": position.get("current_fx_to_pln"),
        "cost_basis_pln": position.get("entry_value_pln"),
        "market_value_pln": position.get("current_value_pln"),
        "unrealized_pnl_pln": position.get("pnl_pln"),
        "entry_fee_pln": position.get("entry_fee_pln"),
    }


def headline_report(position: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("title") or "").strip()
    source = str(item.get("source") or "").strip()
    url = item.get("link")
    published = stamp(item.get("published_at") or item.get("published"))
    event_type = classify(title)
    if not title or not source or not https(url) or published is None or event_type is None:
        return None
    title = clean_title(title, source)
    event_impact = "NEGATIVE" if item.get("risk_keywords") or NEGATIVE.search(title) else "POSITIVE" if item.get("positive_keywords") or POSITIVE.search(title) else "NEUTRAL"
    symbol = str(position.get("broker_symbol") or position.get("id") or "")
    material_negative = event_impact == "NEGATIVE" and event_type in {"GUIDANCE","EARNINGS","REGULATORY","OPERATIONS"}
    action = "THESIS_REVIEW" if material_negative or position.get("review_flag") == "THESIS_REVIEW" else "HOLD"
    effect_pl = f"{PL[event_type].capitalize()} jest wejściem do oceny tezy i ryzyka. " + ("Negatywny sygnał wymaga pogłębionego przeglądu BRACE." if material_negative else "Sam nagłówek nie jest automatycznym zleceniem.")
    effect_en = f"The {EN[event_type]} is an input to the thesis and risk assessment. " + ("The negative signal requires a deeper BRACE review." if material_negative else "A headline alone is not an automatic order.")
    return {
        "id": f"{position.get('id')}-{published.date().isoformat()}-{slug(title)}",
        "position_id": position.get("id"), "symbol": symbol,
        "published_at": published.isoformat().replace("+00:00", "Z"),
        "event_date": published.date().isoformat(), "type": event_type,
        "category": "VERIFIED_SOURCE_HEADLINE", "severity": "HIGH" if material_negative else "MEDIUM",
        "impact": event_impact, "impact_score": 3 if material_negative else 2,
        "title_pl": f"{symbol}: {PL[event_type]}", "title_en": title,
        "summary_pl": f"Zweryfikowany nagłówek źródłowy ({source}): „{title}”.",
        "summary_en": f"Verified source headline ({source}): “{title}”.",
        "thesis_effect_pl": effect_pl, "thesis_effect_en": effect_en,
        "model_action": action,
        "quote": {"value":position.get("current_price"),"currency":position.get("currency"),"kind":"LATEST_COMPLETED","market":None,"quoted_at":position.get("current_price_updated_at"),"source":position.get("current_price_source")} if position.get("current_price") and position.get("current_price_updated_at") else None,
        "position_snapshot": position_snapshot(position),
        "decision_inputs": {"review_flag":position.get("review_flag"),"model_score":position.get("model_score"),"positive_signals":list(position.get("positive_signals") or []),"risk_signals":list(position.get("risk_signals") or [])},
        "sources": [{"label": source, "url": url}],
    }


def composite_report(position: Mapping[str, Any], reports: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    risks = set(position.get("risk_signals") or [])
    negative_price = next((r for r in reports if r.get("position_id") == position.get("id") and r.get("type") == "PRICE_ALERT" and r.get("impact") == "NEGATIVE"), None)
    if negative_price is None or not {"negative_six_month_momentum","drawdown_above_twenty_percent"} <= risks:
        return None
    symbol = str(position.get("broker_symbol") or position.get("id") or "")
    event_date = str(negative_price.get("event_date") or position.get("market_date") or "")
    return {
        "id": f"{position.get('id')}-{event_date}-composite-thesis-review", "position_id":position.get("id"), "symbol":symbol,
        "published_at":negative_price.get("published_at"), "event_date":event_date, "type":"PRICE_ALERT", "category":"COMPOSITE_RISK_REVIEW",
        "severity":"HIGH", "impact":"NEGATIVE", "impact_score":4,
        "title_pl":f"{symbol}: pogłębiony przegląd tezy po spadku kursu", "title_en":f"{symbol}: deeper thesis review after the price decline",
        "summary_pl":"Duży spadek dzienny wystąpił równocześnie z ujemnym momentum sześciomiesięcznym i obsunięciem przekraczającym 20%.",
        "summary_en":"A large one-day decline coincided with negative six-month momentum and a drawdown exceeding 20%.",
        "thesis_effect_pl":"BRACE ma rozważyć redukcję. Pełne wyjście wymaga potwierdzenia złamania tezy albo osiągnięcia progu EXIT.",
        "thesis_effect_en":"BRACE must consider a reduction. A full exit requires confirmed thesis invalidation or reaching the EXIT threshold.",
        "model_action":"THESIS_REVIEW", "quote":deepcopy(negative_price.get("quote")), "position_snapshot":position_snapshot(position),
        "decision_inputs":{"review_flag":position.get("review_flag"),"model_score":position.get("model_score"),"risk_signals":sorted(risks)},
        "sources":deepcopy(negative_price.get("sources") or []),
    }


def valid_verified(report: Mapping[str, Any]) -> bool:
    return bool(report.get("id") and report.get("position_id") and report.get("published_at") and report.get("sources")) and all(https(s.get("url")) for s in report.get("sources") or [])


def enrich(portfolio: Mapping[str, Any], payload: Mapping[str, Any], verified: Mapping[str, Any]) -> dict[str, Any]:
    updated = deepcopy(dict(payload)); reports = list(updated.get("reports") or []); ids = {str(r.get("id")) for r in reports}
    for report in verified.get("reports") or []:
        if valid_verified(report) and str(report.get("id")) not in ids:
            reports.append(deepcopy(dict(report))); ids.add(str(report.get("id")))
    for position in portfolio.get("positions") or []:
        for item in position.get("recent_news") or []:
            report = headline_report(position, item)
            if report and report["id"] not in ids:
                reports.append(report); ids.add(report["id"])
        report = composite_report(position, reports)
        if report and report["id"] not in ids:
            reports.append(report); ids.add(report["id"])
    reports.sort(key=lambda r: str(r.get("published_at") or r.get("event_date") or ""), reverse=True)
    updated["reports"] = reports
    updated["last_updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    updated["editorial_policy"] = {"latest_information":"Broader source headlines for context; not every headline changes a decision.","material_reports":"Verified classified events and deterministic composite-risk reviews used by the decision model.","facts_not_inferred":True}
    return updated


def main() -> int:
    portfolio = read(PORTFOLIO, {}); payload = read(REPORTS, {"schema_version":"1.0.0","portfolio_id":portfolio.get("portfolio_id"),"reports":[]}); verified = read(VERIFIED, {"reports":[]})
    updated = enrich(portfolio, payload, verified); write(REPORTS, updated)
    print(f"Material reports enriched: {len(updated.get('reports') or [])} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
