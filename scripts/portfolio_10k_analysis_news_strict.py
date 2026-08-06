#!/usr/bin/env python3
"""Enforce the final high-authority public standard for Analytics news.

Quality is preferred over cardinality: an empty section is better than a
valuation opinion, listicle, aggregator rewrite or generic market commentary.
Only tier-3/4 reporting and official material events remain public.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import portfolio_10k_analysis_news_guard as guard
import portfolio_10k_news_quality as quality

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data/investments/portfolio_10k.json"
REPORTS = ROOT / "data/investments/portfolio_10k_material_reports.json"
METHOD = quality.METHODOLOGY_VERSION

SOFT_OPINION_RE = re.compile(
    r"\b(secret .{0,30} story|competitive weapon|insiders? (?:are )?buying|"
    r"bullish signal|as bullish as|earnings call signals?|investment case|"
    r"why .{0,40} matters|what to know|key takeaways?|stock analysis|"
    r"margin story|the case for|the case against|can .{0,40} keep|"
    r"isn'?t .{0,30} alone|looks like)\b",
    re.I,
)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def too_close_material(
    selected: list[dict[str, Any]], candidate: dict[str, Any], days: int = 3
) -> bool:
    event_type = str(candidate.get("event_type") or candidate.get("type") or "")
    candidate_day = parse_day(candidate.get("published_at") or candidate.get("event_date"))
    if not event_type or candidate_day is None:
        return False
    for existing in selected:
        existing_type = str(existing.get("event_type") or existing.get("type") or "")
        existing_day = parse_day(existing.get("published_at") or existing.get("event_date"))
        if existing_type == event_type and existing_day is not None:
            if abs((candidate_day - existing_day).days) <= days:
                return True
    return False


def strict_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for original in items:
        item = dict(original)
        title = str(item.get("title") or "")
        tier = int(item.get("source_tier") or 0)
        if tier < 3 or guard.noisy(title) or SOFT_OPINION_RE.search(title):
            continue
        if item.get("material_candidate"):
            if item.get("event_type") not in guard.ALLOWED_MATERIAL_TYPES:
                continue
            if int(item.get("quality_score") or 0) < 70:
                continue
        candidates.append(item)

    candidates.sort(
        key=lambda item: (
            int(bool(item.get("material_candidate"))),
            int(item.get("quality_score") or 0),
            str(item.get("published_at") or ""),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    for item in candidates:
        if any(
            quality._near_duplicate(
                str(item.get("title") or ""), str(existing.get("title") or "")
            )
            for existing in selected
        ):
            continue
        if item.get("material_candidate") and too_close_material(selected, item):
            continue
        selected.append(item)
        if len(selected) >= 5:
            break
    return selected


def strict_reports(payload: dict[str, Any]) -> None:
    historical: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for report in payload.get("reports") or []:
        if report.get("methodology_version") != METHOD:
            historical.append(report)
            continue
        materiality = report.get("materiality") or {}
        if int(materiality.get("source_tier") or 0) < 3:
            continue
        if report.get("type") not in guard.ALLOWED_MATERIAL_TYPES:
            continue
        text = f"{report.get('summary_pl', '')} {report.get('summary_en', '')}"
        if guard.noisy(text) or SOFT_OPINION_RE.search(text):
            continue
        current.append(report)

    current.sort(
        key=lambda report: (
            int((report.get("materiality") or {}).get("quality_score") or 0),
            str(report.get("published_at") or ""),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    by_position: dict[str, list[dict[str, Any]]] = {}
    for report in current:
        position_id = str(report.get("position_id") or "")
        position_reports = by_position.setdefault(position_id, [])
        if too_close_material(position_reports, report):
            continue
        position_reports.append(report)
        selected.append(report)

    payload["reports"] = historical + selected
    payload["reports"].sort(
        key=lambda report: str(report.get("published_at") or report.get("event_date") or ""),
        reverse=True,
    )
    policy = payload.setdefault("editorial_policy", {})
    policy["public_source_threshold"] = 3
    policy["quality_over_quantity"] = True
    policy["material_event_deduplication_days"] = 3


def main() -> int:
    portfolio = read(PORTFOLIO)
    for position in portfolio.get("positions") or []:
        if position.get("status") not in {None, "active"}:
            continue
        position["recent_news"] = strict_news(list(position.get("recent_news") or []))
    methodology = portfolio.setdefault("analysis_news_methodology", {})
    methodology["public_source_threshold"] = 3
    methodology["quality_over_quantity"] = True
    write(PORTFOLIO, portfolio)

    reports = read(REPORTS)
    strict_reports(reports)
    write(REPORTS, reports)
    print("High-authority Analytics source threshold applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
