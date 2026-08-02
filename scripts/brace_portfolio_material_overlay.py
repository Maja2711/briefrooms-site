#!/usr/bin/env python3
"""Apply verified material-event context to BRACE scores and decisions."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from brace_portfolio_config import load_config
from brace_portfolio_data import ENGINE_DATA_ROOT, read_json, write_json_atomic
from brace_portfolio_decision import build_pending_decisions

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data/investments/portfolio_10k_material_reports.json"
VERIFIED = ROOT / "data/investments/portfolio_10k_verified_material_events.json"
ANALYSIS = ENGINE_DATA_ROOT / "analysis.json"
PENDING = ENGINE_DATA_ROOT / "pending_decisions.json"
SHADOW = ENGINE_DATA_ROOT / "shadow_log.json"


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_reports() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for path in (REPORTS, VERIFIED):
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("reports") or []:
            if row.get("id"):
                by_id[str(row["id"])] = dict(row)
    return list(by_id.values())


def recent(rows: Iterable[Mapping[str, Any]], instrument_id: str, now: datetime) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=45)
    selected = []
    for row in rows:
        observed = parse_time(row.get("published_at"))
        if str(row.get("position_id")) == instrument_id and observed and observed >= cutoff:
            selected.append(dict(row))
    return sorted(selected, key=lambda row: str(row.get("published_at") or ""), reverse=True)


def context(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    negative = [row for row in items if row.get("impact") == "NEGATIVE"]
    positive = [row for row in items if row.get("impact") == "POSITIVE"]
    reviews = [row for row in items if row.get("model_action") == "THESIS_REVIEW"]
    critical = [row for row in items if row.get("severity") == "CRITICAL"]
    high_negative = [row for row in negative if row.get("severity") in {"HIGH", "CRITICAL"}]
    return {
        "report_count": len(items), "negative_count": len(negative), "positive_count": len(positive),
        "thesis_review_count": len(reviews), "critical_count": len(critical),
        "high_negative_count": len(high_negative), "requires_thesis_review": bool(reviews or critical),
        "latest_report_ids": [str(row.get("id")) for row in items[:8]],
    }


def apply(position: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, Any]:
    item = deepcopy(dict(position))
    original = float(item.get("final_score") or 0.0)
    thesis = float(item.get("thesis_score") or 50.0)
    risk = float(item.get("risk_score") or 50.0)
    cap, penalty = 100.0, 0.0
    if ctx.get("critical_count"):
        cap, penalty = 25.0, 22.0
    elif ctx.get("requires_thesis_review"):
        cap, penalty = 42.0, 14.0
    elif ctx.get("high_negative_count"):
        cap, penalty = 50.0, 9.0
    elif ctx.get("negative_count"):
        penalty = 4.0
    bonus = min(4.0, float(ctx.get("positive_count") or 0))
    negatives = set(item.get("negative_factors") or [])
    positives = set(item.get("positive_factors") or [])
    if ctx.get("negative_count"): negatives.add("confirmed_material_report")
    if ctx.get("requires_thesis_review"): negatives.add("material_thesis_review")
    if ctx.get("positive_count"): positives.add("confirmed_positive_material_report")
    conditions = list(item.get("conditions_for_change") or [])
    if "confirmed material report" not in conditions: conditions.append("confirmed material report")
    item.update({
        "pre_material_overlay_final_score": round(original, 2),
        "final_score": round(min(cap, max(0.0, original - penalty + bonus)), 2),
        "thesis_score": round(max(10.0, thesis - penalty * 1.5 + bonus), 2),
        "risk_score": round(max(10.0, risk - min(12.0, penalty * 0.5)), 2),
        "material_event_context": dict(ctx), "negative_factors": sorted(negatives),
        "positive_factors": sorted(positives), "conditions_for_change": conditions,
    })
    return item


def add_rationale(pending: dict[str, Any], contexts: Mapping[str, Mapping[str, Any]]) -> None:
    recommendations = {str(row.get("instrument")): row for row in pending.get("recommendations") or []}
    for instrument_id, row in recommendations.items():
        ctx = contexts.get(instrument_id) or {}
        if not ctx.get("report_count"): continue
        row["material_event_context"] = dict(ctx)
        row["rationale_pl"] = (f"{row.get('rationale_pl') or ''} BRACE uwzględnił {ctx['report_count']} zweryfikowanych raportów, w tym {ctx['negative_count']} negatywnych; bieżąca rekomendacja to {row.get('action')}.").strip()
        row["rationale_en"] = (f"{row.get('rationale_en') or ''} BRACE incorporated {ctx['report_count']} verified reports, including {ctx['negative_count']} negative; the current recommendation is {row.get('action')}.").strip()
    for decision in pending.get("decisions") or []:
        source = recommendations.get(str(decision.get("instrument") or ""))
        if source:
            decision["material_event_context"] = deepcopy(source.get("material_event_context"))
            decision["rationale_pl"] = source.get("rationale_pl")
            decision["rationale_en"] = source.get("rationale_en")


def run(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    analysis = read_json(ANALYSIS)
    reports = load_reports(); contexts: dict[str, dict[str, Any]] = {}; positions = []
    for position in analysis.get("positions") or []:
        instrument_id = str(position.get("instrument_id") or position.get("id") or "")
        ctx = context(recent(reports, instrument_id, now)); contexts[instrument_id] = ctx; positions.append(apply(position, ctx))
    analysis["positions"] = positions
    analysis["material_reports_overlay"] = {"applied_at":now.isoformat(timespec="seconds"),"facts_not_inferred":True,"contexts":contexts}
    write_json_atomic(ANALYSIS, analysis)
    config, _ = load_config(); shadow = read_json(SHADOW)
    pending = build_pending_decisions(
        positions, analysis.get("candidates") or [], analysis.get("optimization") or {}, config, now,
        str(analysis.get("methodology_version") or "brace-portfolio-v3.1.0"), str(analysis.get("generated_at") or now.isoformat()),
        False, read_json(PENDING), [item for run in (shadow.get("runs") or []) for item in run.get("decisions") or []],
    )
    add_rationale(pending, contexts); pending["material_reports_overlay_applied_at"] = now.isoformat(timespec="seconds"); write_json_atomic(PENDING, pending)
    return {"positions":len(positions),"material_positions":sum(bool(c.get("report_count")) for c in contexts.values()),"decisions":len(pending.get("decisions") or [])}


def main() -> int:
    result = run(); print(f"BRACE material overlay: {result['material_positions']}/{result['positions']} positions, {result['decisions']} decision(s)"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
