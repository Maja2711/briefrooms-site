#!/usr/bin/env python3
"""Build a sanitized, read-only frontend projection of the private Experience Store.

The source Experience Store remains the research authority. This projection only
contains aggregate evidence and a short, sanitized activity view suitable for the
static Portfolio10K Lab UI. Raw decision payloads, signal snapshots, hashes and
ledger paths are deliberately excluded.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "briefrooms-experience-store-public-v1"
ALLOWED_ACTIONS = {"LONG", "SHORT", "FLAT"}
ENGINE_LABELS = {
    "gpw": "GPW Daily",
    "us": "US Daily",
    "without": "EURUSD Daily",
    "eurusd": "EURUSD Daily",
    "eurusd-abc-a": "EURUSD A/B/C · Arm A",
    "eurusd-abc-b": "EURUSD A/B/C · Arm B",
    "eurusd-abc-c": "EURUSD A/B/C · Arm C",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"expected object at {path}:{line_no}")
        rows.append(row)
    return rows


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _ratio(value: Any) -> float | str | None:
    if isinstance(value, str) and value.lower() == "inf":
        return "inf"
    return _number(value)


def _action(value: Any) -> str:
    action = str(value or "").upper()
    return action if action in ALLOWED_ACTIONS else "OTHER"


def _label(engine: str) -> str:
    return ENGINE_LABELS.get(engine, engine.replace("-", " ").title())


def _confidence_fraction(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    if number <= 1:
        return number
    if number <= 100:
        return number / 100.0
    return None


def _metric_block(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    evidence = evidence if isinstance(evidence, Mapping) else {}
    raw = evidence.get("raw_edge") if isinstance(evidence.get("raw_edge"), Mapping) else {}
    formal = evidence.get("formal_alpha") if isinstance(evidence.get("formal_alpha"), Mapping) else {}
    trading = evidence.get("trading_performance") if isinstance(evidence.get("trading_performance"), Mapping) else {}
    return {
        "assessment": evidence.get("assessment") or "INSUFFICIENT_DATA",
        "assessment_basis": evidence.get("assessment_basis"),
        "minimum_sample": evidence.get("minimum_sample_gate"),
        "sample_size": raw.get("sample_size"),
        "mean_return_fraction": _number(raw.get("mean_net_return_fraction")),
        "win_rate": _number(raw.get("win_rate")),
        "profit_factor": _ratio(raw.get("profit_factor")),
        "max_drawdown_fraction": _number(raw.get("max_drawdown_fraction")),
        "avg_mae_fraction": _number(raw.get("avg_mae_fraction")),
        "avg_mfe_fraction": _number(raw.get("avg_mfe_fraction")),
        "formal_alpha_status": formal.get("status") or "NOT_MEASURABLE",
        "formal_alpha_available": bool(formal.get("available", False)),
        "mean_excess_return_fraction": _number(formal.get("mean_excess_return_fraction")),
        "trading_performance": {
            "status": trading.get("status") or "NOT_MEASURABLE",
            "n_trades": int(trading.get("n_trades") or 0),
            "settled_with_return": int(trading.get("settled_with_return") or 0),
            "expectancy_return_fraction": _number(trading.get("expectancy_return_fraction")),
            "hit_rate": _number(trading.get("hit_rate")),
            "average_r_multiple": _number(trading.get("average_r_multiple")),
            "r_multiple_coverage_fraction": _number(trading.get("r_multiple_coverage_fraction")),
            "profit_factor": _ratio(trading.get("profit_factor")),
            "max_drawdown_fraction": _number(trading.get("max_drawdown_fraction")),
            "cumulative_compounded_return_fraction": _number(trading.get("cumulative_compounded_return_fraction")),
            "sharpe_per_trade": _number(trading.get("sharpe_per_trade")),
            "sortino_per_trade": _number(trading.get("sortino_per_trade")),
            "risk_adjusted_basis": trading.get("risk_adjusted_basis"),
            "mean_cost_fraction": _number(trading.get("mean_cost_fraction")),
            "sum_cost_fraction_across_trades": _number(trading.get("sum_cost_fraction_across_trades")),
            "cost_coverage_fraction": _number(trading.get("cost_coverage_fraction")),
            "time_in_market_fraction": _number(trading.get("time_in_market_fraction")),
            "exposure_interval_coverage_fraction": _number(trading.get("exposure_interval_coverage_fraction")),
            "cumulative_turnover_fraction": _number(trading.get("cumulative_turnover_fraction")),
            "mean_turnover_fraction_per_trade": _number(trading.get("mean_turnover_fraction_per_trade")),
            "turnover_coverage_fraction": _number(trading.get("turnover_coverage_fraction")),
        },
    }


def _engine_rows(experiences: list[Mapping[str, Any]], report: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_engine = report.get("by_engine") if isinstance(report.get("by_engine"), Mapping) else {}
    engines = sorted({str(row.get("engine") or "unknown") for row in experiences} | set(map(str, by_engine.keys())))
    result: list[dict[str, Any]] = []
    for engine in engines:
        rows = [row for row in experiences if str(row.get("engine") or "unknown") == engine]
        counts = Counter(_action(row.get("action")) for row in rows)
        settled = sum(str(row.get("status") or "").upper() == "SETTLED" for row in rows)
        result.append({
            "engine": engine,
            "label": _label(engine),
            "experience_count": len(rows),
            "settled_count": settled,
            "pending_count": len(rows) - settled,
            "actions": {
                "LONG": counts["LONG"],
                "SHORT": counts["SHORT"],
                "FLAT": counts["FLAT"],
                "OTHER": counts["OTHER"],
            },
            "evidence": _metric_block(by_engine.get(engine) if isinstance(by_engine, Mapping) else None),
        })
    return result


def _recent_rows(experiences: list[Mapping[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    rows = sorted(experiences, key=lambda row: str(row.get("decision_at") or ""), reverse=True)[:limit]
    public: list[dict[str, Any]] = []
    for row in rows:
        outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        return_value = _number(outcome.get("net_return_fraction"))
        return_basis = "NET"
        if return_value is None:
            return_value = _number(outcome.get("gross_return_fraction"))
            return_basis = "GROSS" if return_value is not None else None
        public.append({
            "decision_at": row.get("decision_at"),
            "engine": row.get("engine"),
            "engine_label": _label(str(row.get("engine") or "unknown")),
            "engine_version": row.get("engine_version"),
            "instrument": row.get("instrument"),
            "action": _action(row.get("action")),
            "confidence_fraction": _confidence_fraction(row.get("confidence")),
            "status": row.get("status"),
            "settled_at": outcome.get("settled_at"),
            "exit_reason": outcome.get("exit_reason"),
            "return_fraction": return_value,
            "return_basis": return_basis,
            "r_multiple": _number(outcome.get("r_multiple")),
            "mae_fraction": _number(outcome.get("mae_fraction")),
            "mfe_fraction": _number(outcome.get("mfe_fraction")),
        })
    return public


def build_projection(experience_path: Path, status_path: Path, report_path: Path) -> dict[str, Any]:
    experiences = _load_jsonl(experience_path)
    status = _load_json(status_path)
    report = _load_json(report_path)
    if status.get("schema_version") != "briefrooms-experience-store-v1":
        raise ValueError("unexpected Experience Store status schema")
    if report.get("schema_version") != "briefrooms-shadow-alpha-report-v1":
        raise ValueError("unexpected Shadow Alpha report schema")
    overall = report.get("overall") if isinstance(report.get("overall"), Mapping) else {}
    source_ledgers = status.get("source_ledgers") if isinstance(status.get("source_ledgers"), list) else []
    source_count = len(source_ledgers)
    sources = [
        {"id": "learning-outcome-loop", "label": "Learning Outcome Loop", "kind": "canonical"},
    ] if source_count else []
    if source_count >= 2:
        sources.append({"id": "eurusd-abc-live-shadow", "label": "EURUSD A/B/C Live Shadow", "kind": "shadow"})
    for index in range(len(sources), source_count):
        sources.append({"id": f"source-{index + 1}", "label": f"Research source {index + 1}", "kind": "research"})

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": status.get("generated_at") or report.get("generated_at"),
        "authority": {
            "read_only": True,
            "production_decision_influence": False,
            "automatic_tuning": False,
            "automatic_promotion": False,
            "source_writeback": False,
        },
        "summary": {
            "experience_count": int(status.get("experience_count") or len(experiences)),
            "settled_count": int(status.get("settled_count") or 0),
            "pending_count": int(status.get("pending_count") or 0),
            "source_count": source_count,
            "engine_count": len({str(row.get("engine") or "unknown") for row in experiences}),
            "minimum_sample": overall.get("minimum_sample_gate"),
            "assessment": overall.get("assessment") or "INSUFFICIENT_DATA",
            "assessment_basis": overall.get("assessment_basis"),
            "formal_alpha_status": ((overall.get("formal_alpha") or {}).get("status") if isinstance(overall.get("formal_alpha"), Mapping) else None) or "NOT_MEASURABLE",
        },
        "overall_evidence": _metric_block(overall),
        "sources": sources,
        "engines": _engine_rows(experiences, report),
        "recent_experiences": _recent_rows(experiences),
        "privacy": {
            "raw_payloads_exposed": False,
            "signal_snapshots_exposed": False,
            "ledger_hashes_exposed": False,
            "ledger_paths_exposed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sanitized Experience Store frontend projection")
    parser.add_argument("--experience-store", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--alpha-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_projection(args.experience_store, args.status, args.alpha_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
