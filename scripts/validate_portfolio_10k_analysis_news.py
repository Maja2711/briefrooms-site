#!/usr/bin/env python3
"""Validate the Portfolio 10K Analytics news methodology in PL and EN.

Historical reports are immutable event records and are not compared with today's
position valuation. This validator checks only the current ranked news feed and
new ``analysis-news-v2`` material reports.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import portfolio_10k_analysis_news_guard as guard
import portfolio_10k_news_quality as quality

ROOT = Path(__file__).resolve().parents[1]
PL = ROOT / "data/investments/portfolio_10k.json"
EN = ROOT / "data/investments/portfolio_10k_usd.json"
REPORTS = ROOT / "data/investments/portfolio_10k_material_reports.json"
METHOD = quality.METHODOLOGY_VERSION


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_time(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None


def https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def news_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("title"),
        item.get("link"),
        item.get("source"),
        item.get("published_at"),
        item.get("event_type"),
        bool(item.get("material_candidate")),
        int(item.get("quality_score") or 0),
    )


def validate_news(portfolio: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    methodology = portfolio.get("analysis_news_methodology") or {}
    if methodology.get("version") != METHOD:
        errors.append(f"{label}: missing {METHOD} methodology marker")

    for position in portfolio.get("positions") or []:
        if position.get("status") not in {None, "active"}:
            continue
        position_id = str(position.get("id") or "")
        items = position.get("recent_news") or []
        if not isinstance(items, list):
            errors.append(f"{label}/{position_id}: recent_news is not a list")
            continue
        if len(items) > 5:
            errors.append(f"{label}/{position_id}: more than 5 recent headlines")
        seen_titles: list[str] = []
        seen_material_days: set[tuple[str, str]] = set()
        for index, item in enumerate(items):
            prefix = f"{label}/{position_id}/news[{index}]"
            title = str(item.get("title") or "")
            if not title:
                errors.append(f"{prefix}: missing title")
            if guard.noisy(title):
                errors.append(f"{prefix}: editorial/opinion headline was not rejected")
            if not https(item.get("link")):
                errors.append(f"{prefix}: source link must be HTTPS")
            if not valid_time(item.get("published_at")):
                errors.append(f"{prefix}: published_at must be timestamped")
            if item.get("methodology_version") != METHOD:
                errors.append(f"{prefix}: wrong methodology version")
            if int(item.get("source_tier") or 0) < 2:
                errors.append(f"{prefix}: source tier below public threshold")
            if int(item.get("entity_match_strength") or 0) < 1:
                errors.append(f"{prefix}: no entity match")
            if int(item.get("quality_score") or 0) < 38:
                errors.append(f"{prefix}: quality score below context threshold")
            if any(quality._near_duplicate(title, previous) for previous in seen_titles):
                errors.append(f"{prefix}: duplicate event headline")
            seen_titles.append(title)
            if item.get("material_candidate"):
                event_type = str(item.get("event_type") or "")
                if event_type not in guard.ALLOWED_MATERIAL_TYPES:
                    errors.append(f"{prefix}: unsupported material event type {event_type!r}")
                if int(item.get("quality_score") or 0) < 70:
                    errors.append(f"{prefix}: material score below 70")
                day_key = (event_type, str(item.get("published_at") or "")[:10])
                if day_key in seen_material_days:
                    errors.append(f"{prefix}: repeated material class on the same day")
                seen_material_days.add(day_key)
    return errors


def validate_language_parity(pl: dict[str, Any], en: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pl_positions = {
        str(position.get("id")): position
        for position in pl.get("positions") or []
        if position.get("status") in {None, "active"}
    }
    en_positions = {
        str(position.get("id")): position
        for position in en.get("positions") or []
        if position.get("status") in {None, "active"}
    }
    if set(pl_positions) != set(en_positions):
        errors.append("PL/EN: active position sets differ")
        return errors
    for position_id in sorted(pl_positions):
        left = [news_signature(item) for item in pl_positions[position_id].get("recent_news") or []]
        right = [news_signature(item) for item in en_positions[position_id].get("recent_news") or []]
        if left != right:
            errors.append(f"PL/EN/{position_id}: ranked source-news feeds differ")
    return errors


def validate_new_reports(payload: dict[str, Any], pl: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    active = {
        str(position.get("id")): position
        for position in pl.get("positions") or []
        if position.get("status") in {None, "active"}
    }
    seen: set[tuple[str, str, str]] = set()
    for report in payload.get("reports") or []:
        if report.get("methodology_version") != METHOD:
            continue
        report_id = str(report.get("id") or "unnamed")
        prefix = f"report/{report_id}"
        position_id = str(report.get("position_id") or "")
        if position_id not in active:
            errors.append(f"{prefix}: new report does not reference an active position")
        if report.get("category") != "VERIFIED_SOURCE_EVENT":
            errors.append(f"{prefix}: invalid v2 category")
        event_type = str(report.get("type") or "")
        if event_type not in guard.ALLOWED_MATERIAL_TYPES:
            errors.append(f"{prefix}: unsupported event type {event_type!r}")
        if not valid_time(report.get("published_at")):
            errors.append(f"{prefix}: invalid published_at")
        materiality = report.get("materiality") or {}
        if materiality.get("methodology_version") != METHOD:
            errors.append(f"{prefix}: missing materiality audit marker")
        if int(materiality.get("quality_score") or 0) < 70:
            errors.append(f"{prefix}: quality score below 70")
        sources = report.get("sources") or []
        if not sources or not all(https(source.get("url")) for source in sources):
            errors.append(f"{prefix}: verified HTTPS source required")
        cluster = (position_id, str(report.get("event_date") or ""), event_type)
        if cluster in seen:
            errors.append(f"{prefix}: duplicate position/day/type material report")
        seen.add(cluster)

        snapshot = report.get("position_snapshot") or {}
        cost = finite(snapshot.get("cost_basis_local"))
        value = finite(snapshot.get("market_value_local"))
        pnl = finite(snapshot.get("unrealized_pnl_local"))
        pct = finite(snapshot.get("unrealized_pnl_percent"))
        if cost is not None and cost > 0 and value is not None:
            expected_pnl = value - cost
            if pnl is None or abs(pnl - expected_pnl) > 0.02:
                errors.append(f"{prefix}: inconsistent local P/L snapshot")
            if pct is None or abs(pct - expected_pnl / cost) > 0.0002:
                errors.append(f"{prefix}: inconsistent P/L percentage snapshot")
    return errors


def main() -> int:
    pl = read(PL)
    en = read(EN)
    reports = read(REPORTS)
    errors = (
        validate_news(pl, "PL")
        + validate_news(en, "EN")
        + validate_language_parity(pl, en)
        + validate_new_reports(reports, pl)
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Portfolio 10K Analytics news methodology is valid in PL and EN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
