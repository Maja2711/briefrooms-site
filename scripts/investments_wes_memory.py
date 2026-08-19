#!/usr/bin/env python3
"""Build a canonical, read-only historical memory for WES.

The migration never edits historical weekly JSON files and never affects active
WES decisions. It normalizes closed historical observations into two separate
views:

* Market Experience Memory: what happened after a recorded directional episode.
* Strategy Performance Memory: only episodes with an explicit strategy id that
  actually existed at the time.

Quality weights preserve useful old history without pretending reconstructed or
legacy episodes have the same evidential strength as canonical closed legs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
MEMORY_PATH = ROOT / "data" / "investments" / "wes_historical_memory.json"
REPORT_PATH = ROOT / "data" / "investments" / "wes_memory_report.json"
SCHEMA_VERSION = "wes-historical-memory-v1"
DEFAULT_FROM_WEEK = "2026-W24"
DEFAULT_THROUGH_WEEK = "2026-W34"

QUALITY_WEIGHTS = {"A": 1.0, "B": 0.70, "C": 0.40, "D": 0.0}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _week_number(week_id: str) -> tuple[int, int]:
    try:
        year, week = week_id.split("-W", 1)
        return int(year), int(week)
    except (TypeError, ValueError):
        return (0, 0)


def _within_scope(week_id: str, start: str, end: str) -> bool:
    key = _week_number(week_id)
    return _week_number(start) <= key <= _week_number(end)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _execution_alignment_issue(week: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    entry_at = _parse_dt(record.get("entry_captured_at"))
    target_at = _parse_dt((week.get("market_window") or {}).get("entry_target_local") if isinstance(week.get("market_window"), Mapping) else None)
    forecast_at = _parse_dt(week.get("forecast_created_at"))
    if entry_at and target_at and entry_at < target_at:
        return True
    if entry_at and forecast_at and entry_at < forecast_at:
        return True
    return bool(week.get("late_forecast_recovery"))


def _is_reconstructed(week: Mapping[str, Any], item: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    texts = [
        str(week.get("method_version") or ""),
        str(week.get("model_status") or ""),
        str(item.get("entry_quality_status") or ""),
        str(item.get("close_quality_status") or ""),
        str(record.get("exit_time_precision") or ""),
        str(record.get("exit_source") or ""),
    ]
    joined = " ".join(texts).lower()
    return bool(week.get("reconstruction")) or _execution_alignment_issue(week, record) or any(
        token in joined
        for token in (
            "reconstruct",
            "exact_first_hit_bar_not_preserved",
            "closed_after_week_deadline",
            "after_week_deadline",
            "historical_governance_record",
        )
    )


def _strategy_id(item: Mapping[str, Any], record: Mapping[str, Any]) -> str | None:
    value = record.get("strategy_id")
    if value:
        return str(value)
    decision = record.get("entry_decision") if isinstance(record.get("entry_decision"), Mapping) else {}
    if decision.get("strategy_id"):
        return str(decision.get("strategy_id"))
    decision = item.get("continuous_entry_decision") if isinstance(item.get("continuous_entry_decision"), Mapping) else {}
    return str(decision.get("strategy_id")) if decision.get("strategy_id") else None


def _entry_regime(item: Mapping[str, Any], record: Mapping[str, Any]) -> str | None:
    for value in (
        record.get("entry_regime"),
        (record.get("entry_decision") or {}).get("regime") if isinstance(record.get("entry_decision"), Mapping) else None,
        item.get("continuous_entry_regime"),
        (item.get("continuous_entry_decision") or {}).get("regime") if isinstance(item.get("continuous_entry_decision"), Mapping) else None,
    ):
        if value:
            return str(value)
    return None


def _entry_class(record: Mapping[str, Any], *, canonical_leg: bool) -> str:
    risk = record.get("risk_plan") if isinstance(record.get("risk_plan"), Mapping) else {}
    if risk.get("wes_entry_class"):
        return str(risk.get("wes_entry_class"))
    return "continuous_entry" if canonical_leg else "legacy_weekly"


def _net_result(item: Mapping[str, Any], record: Mapping[str, Any]) -> tuple[float | None, str]:
    net = _finite(record.get("net_result_percent"))
    if net is not None:
        return net, "recorded_net_result_percent"
    gross = _finite(record.get("gross_result_percent"))
    if gross is None:
        gross = _finite(record.get("result_percent"))
    if gross is None:
        gross = _finite(item.get("result_percent"))
    if gross is None:
        return None, "missing_result"
    cost = _finite(record.get("estimated_round_trip_cost_percent"))
    if cost is not None:
        return gross - cost, "gross_minus_recorded_round_trip_cost"
    return gross, "recorded_result_percent_cost_unknown"


def _record_quality(
    week: Mapping[str, Any],
    item: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    canonical_leg: bool,
    strategy_id: str | None,
    net_result: float | None,
) -> tuple[str, float, list[str]]:
    entry = _finite(record.get("entry_price"))
    exit_price = _finite(record.get("exit_price"))
    entry_at = record.get("entry_captured_at")
    exit_at = record.get("exit_captured_at")
    reconstructed = _is_reconstructed(week, item, record)
    reasons: list[str] = []

    if entry is None or exit_price is None or not entry_at or not exit_at or net_result is None:
        reasons.append("insufficient_closed_execution_record")
        return "D", QUALITY_WEIGHTS["D"], reasons

    if reconstructed:
        reasons.append("historical_or_partially_reconstructed_execution")
        return "C", QUALITY_WEIGHTS["C"], reasons

    if canonical_leg and strategy_id:
        reasons.append("canonical_closed_leg_with_explicit_strategy")
        return "A", QUALITY_WEIGHTS["A"], reasons

    if strategy_id:
        reasons.append("explicit_strategy_with_complete_top_level_execution")
        return "B", QUALITY_WEIGHTS["B"], reasons

    reasons.append("legacy_complete_execution_without_strategy_identity")
    return "B", QUALITY_WEIGHTS["B"], reasons


def _record_id(week_id: str, instrument_id: str, record: Mapping[str, Any], ordinal: int, canonical_leg: bool) -> str:
    identity = {
        "week_id": week_id,
        "instrument_id": instrument_id,
        "leg_id": record.get("leg_id"),
        "entry_captured_at": record.get("entry_captured_at"),
        "exit_captured_at": record.get("exit_captured_at"),
        "direction": record.get("direction"),
        "ordinal": ordinal,
        "canonical_leg": canonical_leg,
    }
    return "wes-memory-" + _sha256_text(_canonical_json(identity))[:20]


def _normalize_record(
    *,
    week: Mapping[str, Any],
    item: Mapping[str, Any],
    record: Mapping[str, Any],
    source_path: str,
    source_sha256: str,
    ordinal: int,
    canonical_leg: bool,
) -> dict[str, Any]:
    week_id = str(week.get("week_id") or "")
    instrument_id = str(record.get("instrument_id") or item.get("instrument_id") or "")
    direction = str(record.get("direction") or item.get("effective_direction") or item.get("direction") or "neutral")
    strategy_id = _strategy_id(item, record)
    net_result, result_basis = _net_result(item, record)
    quality, quality_weight, quality_reasons = _record_quality(
        week, item, record, canonical_leg=canonical_leg, strategy_id=strategy_id, net_result=net_result
    )
    reconstructed = _is_reconstructed(week, item, record)
    closed = quality != "D"
    risk = record.get("risk_plan") if isinstance(record.get("risk_plan"), Mapping) else {}
    wes_native = bool(risk.get("wes_entry_class"))

    market_eligible = closed and direction in {"long", "short"} and quality_weight > 0
    strategy_eligible = market_eligible and bool(strategy_id) and not reconstructed

    normalized = {
        "record_id": _record_id(week_id, instrument_id, record, ordinal, canonical_leg),
        "week_id": week_id,
        "method_version": week.get("method_version"),
        "model_status": week.get("model_status"),
        "instrument_id": instrument_id,
        "symbol": record.get("symbol") or item.get("symbol"),
        "direction": direction,
        "strategy_id": strategy_id,
        "entry_class": _entry_class(record, canonical_leg=canonical_leg),
        "entry_regime": _entry_regime(item, record),
        "entry_price": _finite(record.get("entry_price")),
        "entry_captured_at": record.get("entry_captured_at"),
        "entry_source": record.get("entry_source"),
        "exit_price": _finite(record.get("exit_price")),
        "exit_captured_at": record.get("exit_captured_at"),
        "exit_source": record.get("exit_source"),
        "exit_reason": record.get("exit_reason"),
        "net_result_percent": round(net_result, 8) if net_result is not None else None,
        "result_basis": result_basis,
        "canonical_leg": canonical_leg,
        "wes_native": wes_native,
        "reconstructed": reconstructed,
        "quality_grade": quality,
        "quality_weight": quality_weight,
        "quality_reasons": quality_reasons,
        "market_memory_eligible": market_eligible,
        "strategy_memory_eligible": strategy_eligible,
        "source": {
            "path": source_path,
            "sha256": source_sha256,
            "original_leg_id": record.get("leg_id"),
        },
    }
    normalized["record_sha256"] = _sha256_text(_canonical_json(normalized))
    return normalized


def _candidate_records(item: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], bool]]:
    legs = [
        leg for leg in (item.get("position_legs") or [])
        if isinstance(leg, Mapping) and leg.get("exit_captured_at")
    ]
    if legs:
        return [(leg, True) for leg in legs]
    return [(item, False)]


def build_memory(
    weekly_dir: Path = WEEKLY_DIR,
    *,
    from_week: str = DEFAULT_FROM_WEEK,
    through_week: str = DEFAULT_THROUGH_WEEK,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    for path in sorted(weekly_dir.glob("*.json")):
        week = _read(path)
        week_id = str(week.get("week_id") or path.stem)
        if not _within_scope(week_id, from_week, through_week):
            continue
        source_rel = f"data/investments/weekly/{path.name}"
        source_records: list[dict[str, Any]] = []
        for item in week.get("instruments") or []:
            if not isinstance(item, Mapping):
                continue
            for ordinal, (candidate, canonical_leg) in enumerate(_candidate_records(item)):
                normalized = _normalize_record(
                        week=week,
                        item=item,
                        record=candidate,
                        source_path=source_rel,
                        source_sha256="pending",
                        ordinal=ordinal,
                        canonical_leg=canonical_leg,
                    )
                source_records.append(normalized)
        projection = [{
            "record_id": row["record_id"],
            "record_sha256": row["record_sha256"],
            "quality_grade": row["quality_grade"],
        } for row in source_records]
        source_projection_sha = _sha256_text(_canonical_json(projection))
        for row in source_records:
            row["source"]["sha256"] = source_projection_sha
            row.pop("record_sha256", None)
            row["record_sha256"] = _sha256_text(_canonical_json(row))
            records.append(row)
        sources.append({"week_id": week_id, "path": source_rel, "learning_projection_sha256": source_projection_sha})

    source_set_sha = _sha256_text(_canonical_json(sources))
    records.sort(key=lambda row: (row["week_id"], row["instrument_id"], row["entry_captured_at"] or "", row["record_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"from_week": from_week, "through_week": through_week},
        "active_decision_influence": False,
        "policy": {
            "purpose": "derived_historical_memory_only",
            "historical_weekly_files_are_immutable": True,
            "market_and_strategy_memory_are_separate": True,
            "legacy_history_never_infers_later_strategy_performance": True,
            "quality_weights": QUALITY_WEIGHTS,
        },
        "source_set_sha256": source_set_sha,
        "sources": sources,
        "records": records,
    }


def _weighted_summary(records: Sequence[Mapping[str, Any]], eligible_field: str) -> dict[str, Any]:
    eligible = [row for row in records if row.get(eligible_field)]
    raw_weight = sum(float(row.get("quality_weight") or 0.0) for row in eligible)
    by_week_instrument: dict[tuple[str, str], float] = defaultdict(float)
    for row in eligible:
        by_week_instrument[(str(row.get("week_id")), str(row.get("instrument_id")))] += float(row.get("quality_weight") or 0.0)
    capped = sum(min(1.0, value) for value in by_week_instrument.values())
    wins_weight = sum(float(row.get("quality_weight") or 0.0) for row in eligible if float(row.get("net_result_percent") or 0.0) > 0)
    pnl_weight = sum(float(row.get("quality_weight") or 0.0) * float(row.get("net_result_percent") or 0.0) for row in eligible)
    return {
        "records": len(eligible),
        "effective_samples_raw": round(raw_weight, 6),
        "effective_samples_week_instrument_capped": round(capped, 6),
        "weighted_win_rate": round(wins_weight / raw_weight, 6) if raw_weight else None,
        "weighted_mean_net_percent": round(pnl_weight / raw_weight, 8) if raw_weight else None,
    }


def build_report(memory: Mapping[str, Any]) -> dict[str, Any]:
    records = list(memory.get("records") or [])
    grades = {grade: 0 for grade in QUALITY_WEIGHTS}
    for row in records:
        grades[str(row.get("quality_grade") or "D")] = grades.get(str(row.get("quality_grade") or "D"), 0) + 1

    by_instrument: dict[str, dict[str, Any]] = {}
    for instrument in sorted({str(row.get("instrument_id")) for row in records if row.get("instrument_id")}):
        rows = [row for row in records if row.get("instrument_id") == instrument]
        by_instrument[instrument] = {
            "market_memory": _weighted_summary(rows, "market_memory_eligible"),
            "strategy_memory": _weighted_summary(rows, "strategy_memory_eligible"),
        }

    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy in sorted({str(row.get("strategy_id")) for row in records if row.get("strategy_id")}):
        rows = [row for row in records if row.get("strategy_id") == strategy]
        by_strategy[strategy] = _weighted_summary(rows, "strategy_memory_eligible")

    by_entry_class: dict[str, dict[str, Any]] = {}
    for cls in sorted({str(row.get("entry_class")) for row in records if row.get("entry_class")}):
        rows = [row for row in records if row.get("entry_class") == cls]
        by_entry_class[cls] = _weighted_summary(rows, "market_memory_eligible")

    return {
        "schema_version": "wes-memory-report-v1",
        "source_memory_schema_version": memory.get("schema_version"),
        "source_set_sha256": memory.get("source_set_sha256"),
        "scope": memory.get("scope"),
        "active_decision_influence": False,
        "total_records": len(records),
        "quality_grade_counts": grades,
        "market_memory": _weighted_summary(records, "market_memory_eligible"),
        "strategy_memory": _weighted_summary(records, "strategy_memory_eligible"),
        "by_instrument": by_instrument,
        "by_strategy": by_strategy,
        "by_entry_class": by_entry_class,
        "interpretation": {
            "effective_samples_raw": "sum of quality weights across eligible records",
            "effective_samples_week_instrument_capped": "caps all same-week same-instrument episodes at one effective market episode to expose within-week correlation",
            "strategy_memory": "contains only explicitly recorded strategy identities; legacy v1.x outcomes cannot validate later strategies",
        },
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_outputs(
    memory: Mapping[str, Any],
    report: Mapping[str, Any],
    memory_path: Path = MEMORY_PATH,
    report_path: Path = REPORT_PATH,
) -> None:
    _write_json(memory_path, memory)
    _write_json(report_path, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-week", default=DEFAULT_FROM_WEEK)
    parser.add_argument("--through-week", default=DEFAULT_THROUGH_WEEK)
    parser.add_argument("--write", action="store_true", help="write canonical memory and report")
    parser.add_argument("--check", action="store_true", help="verify committed outputs match a fresh deterministic build")
    args = parser.parse_args()

    memory = build_memory(from_week=args.from_week, through_week=args.through_week)
    report = build_report(memory)

    if args.check:
        committed_memory = _read(MEMORY_PATH)
        committed_report = _read(REPORT_PATH)
        if committed_memory != memory or committed_report != report:
            raise SystemExit("WES historical memory outputs are stale; run with --write")
    if args.write:
        write_outputs(memory, report)
    if not args.write and not args.check:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
