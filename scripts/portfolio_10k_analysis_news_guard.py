#!/usr/bin/env python3
"""Final editorial guard for Portfolio 10K Analytics news.

This pass is intentionally narrow. It does not change prices, accounting,
weights, BRACE decisions or transaction history. It only:
- removes valuation/buy-sell/listicle and pure price-commentary headlines;
- deduplicates multiple write-ups of the same material event;
- demotes unsupported event classes from material-report status;
- keeps newly generated material-report snapshots internally consistent.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import portfolio_10k_news_quality as quality

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "data/investments/portfolio_10k.json"
REPORTS = ROOT / "data/investments/portfolio_10k_material_reports.json"
METHOD = quality.METHODOLOGY_VERSION
ALLOWED_MATERIAL_TYPES = {
    "EARNINGS",
    "GUIDANCE",
    "ANALYST_CHANGE",
    "REGULATORY",
    "OPERATIONS",
    "DIVIDEND",
    "BUYBACK",
}

EDITORIAL_NOISE_RE = re.compile(
    r"\b(undervalued|overvalued|valuation|fair value|fairly valued|"
    r"is .{0,50} (?:a )?(?:buy|sell|hold)|should you buy|worth buying|"
    r"better buy|best stock|stocks? to buy|top \d+ stocks?|price prediction|"
    r"billionaire .{0,60} bought|here'?s why|why i (?:bought|sold)|"
    r"could (?:soar|double|rally)|what investors should know|outshines)\b",
    re.I,
)
PURE_PRICE_RE = re.compile(
    r"\b(stock|shares?)\b.{0,35}\b(moved|moves|rose|rises|fell|falls|"
    r"jumped|jumps|dropped|drops|gained|gains|lost|slides?|slumps?)\b",
    re.I,
)


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def noisy(title: str) -> bool:
    text = str(title or "").strip()
    return bool(EDITORIAL_NOISE_RE.search(text) or PURE_PRICE_RE.search(text))


def event_cluster(item: dict[str, Any]) -> tuple[str, str]:
    event_type = str(item.get("event_type") or "CONTEXT")
    title = quality.headline_key(str(item.get("title") or ""))
    tokens = sorted(quality._tokens(title))
    # Stable compact fingerprint; enough to group syndicated descriptions.
    fingerprint = " ".join(tokens[:10])
    return event_type, fingerprint


def sanitize_news(position_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for original in items:
        title = str(original.get("title") or "").strip()
        if not title or noisy(title) or not https(original.get("link")):
            continue
        item = dict(original)
        event_type = str(item.get("event_type") or "") or None
        if event_type not in ALLOWED_MATERIAL_TYPES:
            item["event_type"] = None
            item["material_candidate"] = False
            item["information_role"] = "context"
        if int(item.get("source_tier") or 0) <= 1 and not item.get("material_candidate"):
            continue
        item["methodology_version"] = METHOD
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
    material_days: set[tuple[str, str]] = set()
    for item in candidates:
        if any(
            quality._near_duplicate(
                str(item.get("title") or ""), str(existing.get("title") or "")
            )
            for existing in selected
        ):
            continue
        if item.get("material_candidate"):
            day_key = (
                str(item.get("event_type") or ""),
                str(item.get("published_at") or "")[:10],
            )
            # Keep the highest-ranked write-up for a given event class/day.
            if day_key in material_days:
                item["material_candidate"] = False
                item["information_role"] = "context"
            else:
                material_days.add(day_key)
        selected.append(item)
        if len(selected) >= 5:
            break
    return selected


def fix_snapshot(report: dict[str, Any]) -> None:
    snapshot = report.get("position_snapshot")
    if not isinstance(snapshot, dict):
        return
    cost = finite(snapshot.get("cost_basis_local"))
    value = finite(snapshot.get("market_value_local"))
    if cost is not None and cost > 0 and value is not None:
        pnl = value - cost
        snapshot["unrealized_pnl_local"] = round(pnl, 4)
        snapshot["unrealized_pnl_percent"] = round(pnl / cost, 6)
    cost_pln = finite(snapshot.get("cost_basis_pln"))
    value_pln = finite(snapshot.get("market_value_pln"))
    if cost_pln is not None and value_pln is not None:
        snapshot["unrealized_pnl_pln"] = round(value_pln - cost_pln, 2)


def sanitize_reports(payload: dict[str, Any]) -> None:
    reports = list(payload.get("reports") or [])
    preserved: list[dict[str, Any]] = []
    new_reports: list[dict[str, Any]] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        if report.get("methodology_version") != METHOD:
            preserved.append(report)
            continue
        if (
            report.get("category") != "VERIFIED_SOURCE_EVENT"
            or report.get("type") not in ALLOWED_MATERIAL_TYPES
            or int((report.get("materiality") or {}).get("quality_score") or 0) < 70
        ):
            continue
        sources = report.get("sources") or []
        if not sources or not all(https(source.get("url")) for source in sources):
            continue
        summary = f"{report.get('summary_pl', '')} {report.get('summary_en', '')}"
        if noisy(summary):
            continue
        fix_snapshot(report)
        new_reports.append(report)

    new_reports.sort(
        key=lambda report: (
            int((report.get("materiality") or {}).get("quality_score") or 0),
            str(report.get("published_at") or ""),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for report in new_reports:
        key = (
            str(report.get("position_id") or ""),
            str(report.get("event_date") or ""),
            str(report.get("type") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(report)

    payload["reports"] = preserved + selected
    payload["reports"].sort(
        key=lambda report: str(
            report.get("published_at") or report.get("event_date") or ""
        ),
        reverse=True,
    )


def main() -> int:
    portfolio = read(PORTFOLIO)
    changed_positions = 0
    for position in portfolio.get("positions") or []:
        if position.get("status") not in {None, "active"}:
            continue
        before = position.get("recent_news") or []
        after = sanitize_news(str(position.get("id") or ""), list(before))
        position["recent_news"] = after
        changed_positions += int(before != after)
    write(PORTFOLIO, portfolio)

    reports = read(REPORTS)
    sanitize_reports(reports)
    write(REPORTS, reports)
    print(
        f"Analytics editorial guard applied; positions_changed={changed_positions}, "
        f"reports={len(reports.get('reports') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
