#!/usr/bin/env python3
"""Merge verified events and ranked source headlines into material reports.

Only factual, entity-specific events that pass the analysis-news-v2 quality gate
become model inputs. Opinion pieces remain excluded even when their titles
contain words such as earnings, valuation or price target.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import portfolio_10k_news_quality as news_quality

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data/investments/portfolio_10k.json"
REPORTS = ROOT / "data/investments/portfolio_10k_material_reports.json"
VERIFIED = ROOT / "data/investments/portfolio_10k_verified_material_events.json"
METHODOLOGY_VERSION = news_quality.METHODOLOGY_VERSION

PL = {
    "GUIDANCE": "zmiana prognozy spółki",
    "EARNINGS": "publikacja wyników finansowych",
    "ANALYST_CHANGE": "potwierdzona zmiana oceny analitycznej",
    "REGULATORY": "istotne zdarzenie regulacyjne",
    "OPERATIONS": "istotne zdarzenie operacyjne",
    "PRODUCT": "istotne zdarzenie produktowe",
    "DIVIDEND": "decyzja dotycząca dywidendy",
    "BUYBACK": "decyzja dotycząca skupu akcji",
}
EN = {
    "GUIDANCE": "company guidance update",
    "EARNINGS": "financial-results release",
    "ANALYST_CHANGE": "confirmed analyst-rating change",
    "REGULATORY": "material regulatory event",
    "OPERATIONS": "material operational event",
    "PRODUCT": "material product event",
    "DIVIDEND": "dividend decision",
    "BUYBACK": "share-buyback decision",
}


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(default)


def write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        return parsed.scheme == "https" and bool(parsed.netloc)
    except ValueError:
        return False


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
        "unrealized_pnl_local": round(quantity * (price - entry), 4)
        if quantity and price and entry
        else None,
        "unrealized_pnl_percent": position.get("pnl_percent"),
        "position_currency": position.get("currency"),
        "current_fx_to_pln": position.get("current_fx_to_pln"),
        "cost_basis_pln": position.get("entry_value_pln"),
        "market_value_pln": position.get("current_value_pln"),
        "unrealized_pnl_pln": position.get("pnl_pln"),
        "entry_fee_pln": position.get("entry_fee_pln"),
    }


def _materiality_reasons(item: Mapping[str, Any]) -> list[str]:
    return [
        "specific_factual_event",
        f"source_tier_{int(item.get('source_tier') or 0)}",
        f"entity_match_{int(item.get('entity_match_strength') or 0)}",
        "fresh_timestamp",
    ]


def headline_report(
    position: Mapping[str, Any], item: Mapping[str, Any]
) -> dict[str, Any] | None:
    position_id = str(position.get("id") or "")
    annotated = news_quality.annotate_item(position_id, item)
    if not annotated or not annotated.get("material_candidate"):
        return None

    title = str(annotated.get("title") or "").strip()
    source = str(annotated.get("source") or "").strip()
    url = annotated.get("link")
    published_at = str(annotated.get("published_at") or "")
    event_type = str(annotated.get("event_type") or "")
    if (
        event_type not in PL
        or not title
        or not source
        or not https(url)
        or not published_at
        or int(annotated.get("quality_score") or 0) < 70
    ):
        return None

    title = clean_title(title, source)
    event_impact = str(annotated.get("impact") or "NEUTRAL")
    symbol = str(position.get("broker_symbol") or position.get("id") or "")
    material_negative = event_impact == "NEGATIVE" and event_type in {
        "GUIDANCE",
        "EARNINGS",
        "REGULATORY",
        "OPERATIONS",
    }
    action = (
        "THESIS_REVIEW"
        if material_negative or position.get("review_flag") == "THESIS_REVIEW"
        else "HOLD"
    )
    effect_pl = (
        f"{PL[event_type].capitalize()} jest wejściem do oceny tezy i ryzyka. "
        + (
            "Negatywny sygnał wymaga pogłębionego przeglądu BRACE."
            if material_negative
            else "Potwierdzone zdarzenie nie jest samo w sobie automatycznym zleceniem."
        )
    )
    effect_en = (
        f"The {EN[event_type]} is an input to the thesis and risk assessment. "
        + (
            "The negative signal requires a deeper BRACE review."
            if material_negative
            else "A confirmed event is not, by itself, an automatic order."
        )
    )
    event_date = published_at[:10]
    quality_score = int(annotated.get("quality_score") or 0)
    severity = (
        "HIGH"
        if material_negative or int(annotated.get("source_tier") or 0) >= 4
        else "MEDIUM"
    )

    return {
        "id": f"{position_id}-{event_date}-{event_type.lower()}-{slug(title)}",
        "position_id": position_id,
        "symbol": symbol,
        "published_at": published_at,
        "event_date": event_date,
        "type": event_type,
        "category": "VERIFIED_SOURCE_EVENT",
        "severity": severity,
        "impact": event_impact,
        "impact_score": 4 if material_negative else 3,
        "title_pl": f"{symbol}: {PL[event_type]}",
        "title_en": f"{symbol}: {EN[event_type]}",
        "summary_pl": f"Zweryfikowane zdarzenie źródłowe ({source}): „{title}”.",
        "summary_en": f"Verified source event ({source}): “{title}”.",
        "thesis_effect_pl": effect_pl,
        "thesis_effect_en": effect_en,
        "model_action": action,
        "quote": {
            "value": position.get("current_price"),
            "currency": position.get("currency"),
            "kind": "LATEST_COMPLETED",
            "market": None,
            "quoted_at": position.get("current_price_updated_at"),
            "source": position.get("current_price_source"),
        }
        if position.get("current_price") and position.get("current_price_updated_at")
        else None,
        "position_snapshot": position_snapshot(position),
        "decision_inputs": {
            "review_flag": position.get("review_flag"),
            "model_score": position.get("model_score"),
            "positive_signals": list(position.get("positive_signals") or []),
            "risk_signals": list(position.get("risk_signals") or []),
        },
        "materiality": {
            "methodology_version": METHODOLOGY_VERSION,
            "quality_score": quality_score,
            "source_tier": annotated.get("source_tier"),
            "entity_match_strength": annotated.get("entity_match_strength"),
            "reasons": _materiality_reasons(annotated),
        },
        "methodology_version": METHODOLOGY_VERSION,
        "sources": [{"label": source, "url": url}],
    }


def composite_report(
    position: Mapping[str, Any], reports: list[Mapping[str, Any]]
) -> dict[str, Any] | None:
    risks = set(position.get("risk_signals") or [])
    negative_price = next(
        (
            report
            for report in reports
            if report.get("position_id") == position.get("id")
            and report.get("type") == "PRICE_ALERT"
            and report.get("impact") == "NEGATIVE"
        ),
        None,
    )
    if negative_price is None or not {
        "negative_six_month_momentum",
        "drawdown_above_twenty_percent",
    } <= risks:
        return None
    symbol = str(position.get("broker_symbol") or position.get("id") or "")
    event_date = str(
        negative_price.get("event_date") or position.get("market_date") or ""
    )
    return {
        "id": f"{position.get('id')}-{event_date}-composite-thesis-review",
        "position_id": position.get("id"),
        "symbol": symbol,
        "published_at": negative_price.get("published_at"),
        "event_date": event_date,
        "type": "PRICE_ALERT",
        "category": "COMPOSITE_RISK_REVIEW",
        "severity": "HIGH",
        "impact": "NEGATIVE",
        "impact_score": 4,
        "title_pl": f"{symbol}: pogłębiony przegląd tezy po spadku kursu",
        "title_en": f"{symbol}: deeper thesis review after the price decline",
        "summary_pl": "Duży spadek dzienny wystąpił równocześnie z ujemnym momentum sześciomiesięcznym i obsunięciem przekraczającym 20%.",
        "summary_en": "A large one-day decline coincided with negative six-month momentum and a drawdown exceeding 20%.",
        "thesis_effect_pl": "BRACE ma rozważyć redukcję. Pełne wyjście wymaga potwierdzenia złamania tezy albo osiągnięcia progu EXIT.",
        "thesis_effect_en": "BRACE must consider a reduction. A full exit requires confirmed thesis invalidation or reaching the EXIT threshold.",
        "model_action": "THESIS_REVIEW",
        "quote": deepcopy(negative_price.get("quote")),
        "position_snapshot": position_snapshot(position),
        "decision_inputs": {
            "review_flag": position.get("review_flag"),
            "model_score": position.get("model_score"),
            "risk_signals": sorted(risks),
        },
        "methodology_version": METHODOLOGY_VERSION,
        "sources": deepcopy(negative_price.get("sources") or []),
    }


def valid_verified(report: Mapping[str, Any]) -> bool:
    return bool(
        report.get("id")
        and report.get("position_id")
        and report.get("published_at")
        and report.get("sources")
    ) and all(https(source.get("url")) for source in report.get("sources") or [])


def _keep_existing(report: Mapping[str, Any]) -> bool:
    category = str(report.get("category") or "")
    if category == "VERIFIED_SOURCE_HEADLINE":
        return False
    if category == "VERIFIED_SOURCE_EVENT":
        return report.get("methodology_version") == METHODOLOGY_VERSION
    return True


def enrich(
    portfolio: Mapping[str, Any],
    payload: Mapping[str, Any],
    verified: Mapping[str, Any],
) -> dict[str, Any]:
    updated = deepcopy(dict(payload))
    reports = [
        deepcopy(dict(report))
        for report in updated.get("reports") or []
        if _keep_existing(report)
    ]
    ids = {str(report.get("id")) for report in reports}

    for report in verified.get("reports") or []:
        if valid_verified(report) and str(report.get("id")) not in ids:
            reports.append(deepcopy(dict(report)))
            ids.add(str(report.get("id")))

    for position in portfolio.get("positions") or []:
        if position.get("status") not in {None, "active"}:
            continue
        for item in position.get("recent_news") or []:
            report = headline_report(position, item)
            if report and report["id"] not in ids:
                reports.append(report)
                ids.add(report["id"])
        report = composite_report(position, reports)
        if report and report["id"] not in ids:
            reports.append(report)
            ids.add(report["id"])

    reports.sort(
        key=lambda report: str(
            report.get("published_at") or report.get("event_date") or ""
        ),
        reverse=True,
    )
    updated["reports"] = reports
    updated["last_updated_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    updated["editorial_policy"] = {
        "methodology_version": METHODOLOGY_VERSION,
        "latest_information_pl": "Ranking świeżych nagłówków według jakości źródła, zgodności z instrumentem, znaczenia zdarzenia i daty publikacji.",
        "latest_information_en": "Fresh headlines ranked by source quality, entity match, event significance and publication time.",
        "material_reports_pl": "Wyłącznie konkretne, zweryfikowane zdarzenia spełniające próg jakości; opinie inwestycyjne i ogólne porównania są wykluczone.",
        "material_reports_en": "Only specific verified events that pass the quality threshold; investment opinions and generic comparisons are excluded.",
        "facts_not_inferred": True,
    }
    return updated


def main() -> int:
    portfolio = read(PORTFOLIO, {})
    payload = read(
        REPORTS,
        {
            "schema_version": "1.0.0",
            "portfolio_id": portfolio.get("portfolio_id"),
            "reports": [],
        },
    )
    verified = read(VERIFIED, {"reports": []})
    updated = enrich(portfolio, payload, verified)
    write(REPORTS, updated)
    print(f"Material reports enriched: {len(updated.get('reports') or [])} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
