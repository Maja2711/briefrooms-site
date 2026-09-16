#!/usr/bin/env python3
"""Daily Stock actionable-MISS observer v2.

This module converts already-settled, prospectively frozen rejected-candidate
outcomes into *research evidence*.  It has no ranking, execution, policy or
promotion authority.

A MISS is deliberately narrower than "a stock went up later": the rejected
candidate must have been a legal opportunity at decision time (no failed hard
safety/data/liquidity gate), its configured horizon must be resolved, and it
must outperform the actual Daily Stock decision by a material amount while
finishing positive.  Hard-gate rejects remain auditable but are excluded from
learnable MISS patterns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data/investments/daily_stock_miss_learning_report.json"
DEFAULT_MARKET_ROOTS = {
    "gpw": ROOT / "data/investments/rejected_candidate_outcomes/gpw",
    "us": ROOT / "data/investments/rejected_candidate_outcomes/us",
}
SCHEMA_VERSION = "daily-stock-miss-learning-report-v2"
DEFAULT_HORIZON = 2
DEFAULT_MIN_REGRET_PP = 0.50

# These are safety/data/executability concepts.  They can be measured, but this
# observer may never turn evidence about them into a weakening proposal.
NON_LEARNABLE_HARD_GATES = {
    "market_data",
    "source_availability",
    "data_integrity",
    "freshness",
    "liquidity",
    "position_risk",
    "reward_risk",
    "execution_freshness",
    "quant_candidate",
}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.replace(path)


def _horizon(candidate: Mapping[str, Any], horizon_sessions: int) -> Mapping[str, Any] | None:
    for row in candidate.get("horizons") or []:
        if not isinstance(row, Mapping):
            continue
        try:
            horizon = int(row.get("horizon_sessions"))
        except (TypeError, ValueError):
            continue
        if horizon == horizon_sessions:
            return row
    return None


def _first_gate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    gate = candidate.get("first_blocking_gate")
    if isinstance(gate, Mapping):
        return {
            "name": str(gate.get("name") or "unknown"),
            "hard": bool(gate.get("hard")),
            "stage": str(gate.get("stage") or "unknown"),
            "reason": str(gate.get("reason") or ""),
            "observed_value": gate.get("observed_value"),
            "threshold": gate.get("threshold"),
        }
    return {"name": "unknown", "hard": False, "stage": "unknown", "reason": "", "observed_value": None, "threshold": None}


def _source_contract_safe(record: Mapping[str, Any]) -> bool:
    source = record.get("source_snapshot") if isinstance(record.get("source_snapshot"), Mapping) else {}
    governance = source.get("governance") if isinstance(source.get("governance"), Mapping) else {}
    if governance:
        if governance.get("prospective_only") is not True:
            return False
        if governance.get("historical_backfill") is not False:
            return False
        for field in (
            "decision_influence",
            "ranking_writeback",
            "gate_writeback",
            "production_trade_writeback",
            "automatic_learning_writeback",
            "automatic_promotion",
        ):
            if governance.get(field) is not False:
                return False
    contract = record.get("settlement") if isinstance(record.get("settlement"), Mapping) else {}
    contract = contract.get("contract") if isinstance(contract.get("contract"), Mapping) else {}
    if contract and contract.get("source_snapshot_immutable") is not True:
        return False
    return True


def analyze_record(
    record: Mapping[str, Any],
    *,
    horizon_sessions: int = DEFAULT_HORIZON,
    min_regret_pp: float = DEFAULT_MIN_REGRET_PP,
) -> dict[str, Any]:
    """Analyze one immutable outcome record without mutating it."""
    settlement = record.get("settlement") if isinstance(record.get("settlement"), Mapping) else {}
    summary = settlement.get("summary") if isinstance(settlement.get("summary"), Mapping) else {}
    market = str(record.get("market") or (record.get("source_snapshot") or {}).get("market") or "unknown").lower()
    decision_date = str(record.get("decision_date") or "")
    out: dict[str, Any] = {
        "market": market,
        "decision_date": decision_date,
        "status": "IGNORED",
        "reason": "unresolved_record",
        "selected_symbol": summary.get("selected_symbol"),
        "selected_return_percent": _finite(summary.get("selected_return_percent")),
        "observations": [],
        "excluded_hard_gate_candidates": 0,
        "unresolved_candidates": 0,
    }
    if settlement.get("status") != "RESOLVED" or summary.get("status") != "RESOLVED":
        return out
    if not _source_contract_safe(record):
        out["reason"] = "unsafe_or_nonprospective_source_contract"
        return out
    selected_return = _finite(summary.get("selected_return_percent"))
    if selected_return is None:
        out["reason"] = "selected_return_missing"
        return out

    observations: list[dict[str, Any]] = []
    for candidate in settlement.get("rejected_candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        gate = _first_gate(candidate)
        hard_blocked = bool(candidate.get("hard_blocked")) or bool(candidate.get("hard_blocking_gates")) or gate["hard"]
        legal = candidate.get("opportunity_candidate") is True and candidate.get("economically_evaluable") is True and not hard_blocked
        if not legal:
            if hard_blocked:
                out["excluded_hard_gate_candidates"] += 1
            continue
        horizon = _horizon(candidate, horizon_sessions)
        if not isinstance(horizon, Mapping) or horizon.get("status") != "RESOLVED":
            out["unresolved_candidates"] += 1
            continue
        candidate_return = _finite(horizon.get("net_return_percent"))
        if candidate_return is None:
            out["unresolved_candidates"] += 1
            continue
        delta = candidate_return - selected_return
        if delta >= min_regret_pp and candidate_return > 0.0:
            classification = "MISS"
        elif delta <= -min_regret_pp:
            classification = "AVOIDED_LOSS"
        elif delta >= min_regret_pp:
            classification = "RELATIVE_IMPROVEMENT"
        else:
            classification = "NEUTRAL"
        observations.append({
            "observation_id": f"missobs-{_sha([market, decision_date, candidate.get('candidate_id'), horizon_sessions])[:24]}",
            "candidate_id": candidate.get("candidate_id"),
            "symbol": candidate.get("symbol"),
            "classification": classification,
            "horizon_sessions": horizon_sessions,
            "activated": bool(horizon.get("activated")),
            "candidate_return_percent": round(candidate_return, 6),
            "selected_return_percent": round(selected_return, 6),
            "candidate_minus_selected_percent": round(delta, 6),
            "first_blocking_gate": gate,
            "learnable_gate": (not gate["hard"] and gate["name"] not in NON_LEARNABLE_HARD_GATES),
            "source_snapshot_sha256": record.get("source_snapshot_sha256"),
        })
    out.update({
        "status": "RESOLVED",
        "reason": "actionable_legal_alternatives_analyzed",
        "observations": observations,
    })
    return out


def aggregate_patterns(analyses: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for analysis in analyses:
        for observation in analysis.get("observations") or []:
            if not isinstance(observation, Mapping):
                continue
            gate = observation.get("first_blocking_gate") if isinstance(observation.get("first_blocking_gate"), Mapping) else {}
            buckets[(str(analysis.get("market") or "unknown"), str(gate.get("name") or "unknown"))].append(observation)

    patterns: list[dict[str, Any]] = []
    for (market, gate_name), rows in sorted(buckets.items()):
        deltas = [float(row["candidate_minus_selected_percent"]) for row in rows]
        returns = [float(row["candidate_return_percent"]) for row in rows]
        misses = [row for row in rows if row.get("classification") == "MISS"]
        avoided = [row for row in rows if row.get("classification") == "AVOIDED_LOSS"]
        relative = [row for row in rows if row.get("classification") == "RELATIVE_IMPROVEMENT"]
        learnable = all(bool(row.get("learnable_gate")) for row in rows)
        hard = any(bool((row.get("first_blocking_gate") or {}).get("hard")) for row in rows)
        pattern = {
            "pattern_id": f"misspat-{_sha([market, gate_name])[:20]}",
            "market": market,
            "gate": gate_name,
            "hard_gate": hard,
            "learnable": bool(learnable and not hard),
            "observations": len(rows),
            "misses": len(misses),
            "avoided_losses": len(avoided),
            "relative_improvements": len(relative),
            "neutral": len(rows) - len(misses) - len(avoided) - len(relative),
            "miss_rate": round(len(misses) / len(rows), 6) if rows else 0.0,
            "mean_candidate_minus_selected_percent": round(sum(deltas) / len(deltas), 6),
            "mean_candidate_return_percent": round(sum(returns) / len(returns), 6),
            "unique_symbols": len({str(row.get("symbol") or "") for row in rows if row.get("symbol")}),
            "evidence_observation_ids": [str(row.get("observation_id")) for row in rows],
        }
        patterns.append(pattern)
    return patterns


def iter_records(root: Path) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return
    for path in sorted(root.glob("*.json")):
        if path.name in {"index.json", "component_value_attribution.json"}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("settlement"), Mapping):
            yield payload


def build_report(
    records_by_market: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    horizon_sessions: int = DEFAULT_HORIZON,
    min_regret_pp: float = DEFAULT_MIN_REGRET_PP,
    generated_at: str | None = None,
) -> dict[str, Any]:
    analyses: list[dict[str, Any]] = []
    for market, records in sorted(records_by_market.items()):
        for record in records:
            row = analyze_record(record, horizon_sessions=horizon_sessions, min_regret_pp=min_regret_pp)
            row["market"] = market.lower()
            analyses.append(row)
    patterns = aggregate_patterns(analyses)
    actionable = [obs for row in analyses for obs in row.get("observations") or []]
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "horizon_sessions": int(horizon_sessions),
        "minimum_regret_percent_points": float(min_regret_pp),
        "records_seen": len(analyses),
        "resolved_records": sum(1 for row in analyses if row.get("status") == "RESOLVED"),
        "actionable_observations": len(actionable),
        "misses": sum(1 for row in actionable if row.get("classification") == "MISS"),
        "avoided_losses": sum(1 for row in actionable if row.get("classification") == "AVOIDED_LOSS"),
        "excluded_hard_gate_candidates": sum(int(row.get("excluded_hard_gate_candidates") or 0) for row in analyses),
        "patterns": patterns,
        "record_analyses": analyses,
        "governance": {
            "observational_only": True,
            "prospective_evidence_required": True,
            "production_decision_influence": False,
            "ranking_writeback": False,
            "gate_writeback": False,
            "automatic_policy_writeback": False,
            "automatic_promotion": False,
            "trade_execution": False,
            "hard_safety_gates_are_learnable": False,
            "no_lookahead": True,
        },
    }
    hash_body = dict(report)
    hash_body.pop("report_sha256", None)
    report["report_sha256"] = _sha(hash_body)
    return report


def validate_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Daily Stock MISS report schema mismatch")
    body = dict(report)
    stored = str(body.pop("report_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("Daily Stock MISS report hash mismatch")
    governance = report.get("governance") if isinstance(report.get("governance"), Mapping) else {}
    required_false = (
        "production_decision_influence",
        "ranking_writeback",
        "gate_writeback",
        "automatic_policy_writeback",
        "automatic_promotion",
        "trade_execution",
        "hard_safety_gates_are_learnable",
    )
    if any(governance.get(key) is not False for key in required_false):
        raise ValueError("Daily Stock MISS zero-authority contract violated")
    if governance.get("observational_only") is not True or governance.get("no_lookahead") is not True:
        raise ValueError("Daily Stock MISS observational/no-lookahead contract violated")
    for pattern in report.get("patterns") or []:
        if not isinstance(pattern, Mapping):
            raise ValueError("invalid MISS pattern")
        if pattern.get("hard_gate") is True and pattern.get("learnable") is True:
            raise ValueError("hard safety gate may not become a learnable pattern")


def build_from_repo(*, horizon_sessions: int = DEFAULT_HORIZON, min_regret_pp: float = DEFAULT_MIN_REGRET_PP) -> dict[str, Any]:
    records = {market: list(iter_records(path)) for market, path in DEFAULT_MARKET_ROOTS.items()}
    return build_report(records, horizon_sessions=horizon_sessions, min_regret_pp=min_regret_pp)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the zero-authority Daily Stock actionable-MISS report")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--min-regret-pp", type=float, default=DEFAULT_MIN_REGRET_PP)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        validate_report(payload)
        print(json.dumps({"status": "OK", "patterns": len(payload.get("patterns") or []), "misses": payload.get("misses")}, sort_keys=True))
        return 0
    report = build_from_repo(horizon_sessions=args.horizon, min_regret_pp=args.min_regret_pp)
    validate_report(report)
    _atomic_json(args.output, report)
    print(json.dumps({"status": "BUILT", "records": report["records_seen"], "patterns": len(report["patterns"]), "misses": report["misses"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
