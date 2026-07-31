#!/usr/bin/env python3
"""Build aggregate, non-sensitive economics diagnostics for BRACE-SPX A2.

The script reads the private development trace and emits only aggregate
exposure, cost and benchmark-comparison evidence. It never reads the sealed
holdout and never publishes candidate parameters, forecasts or daily paths.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

COST_PER_UNIT_TURNOVER = 0.0005


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cagr(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna().astype(float)
    if clean.empty:
        return 0.0
    years = max(len(clean) / 252.0, 1.0 / 252.0)
    total = float((1.0 + clean).prod())
    if total <= 0.0:
        return -1.0
    return float(total ** (1.0 / years) - 1.0)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def build(report: Mapping[str, Any], trace: pd.DataFrame) -> dict[str, Any]:
    raw_best = report.get("raw_best_diagnostic_only") or {}
    candidate = raw_best.get("candidate") or {}
    name = str(candidate.get("name") or "").strip()
    if not name:
        raise ValueError("Diagnostic leader name is missing")

    prefix = name.replace("-", "_")
    required = {
        "asset": "spy_return",
        "risk_free": "risk_free_return",
        "applied": f"{prefix}_applied",
        "turnover": f"{prefix}_turnover",
        "net": f"{prefix}_return",
    }
    missing = [column for column in required.values() if column not in trace.columns]
    if missing:
        raise ValueError(f"Trace is missing required columns: {missing}")

    asset = pd.to_numeric(trace[required["asset"]], errors="coerce").fillna(0.0)
    risk_free = pd.to_numeric(trace[required["risk_free"]], errors="coerce").fillna(0.0)
    applied = pd.to_numeric(trace[required["applied"]], errors="coerce").ffill().fillna(0.0)
    turnover = pd.to_numeric(trace[required["turnover"]], errors="coerce").fillna(0.0)
    published_net = pd.to_numeric(trace[required["net"]], errors="coerce").fillna(0.0)

    if (applied < -1e-12).any() or (applied > 1.0 + 1e-12).any():
        raise ValueError("Architecture 2 trace violates the long/flat [0,1] mandate")

    gross = applied * asset + (1.0 - applied) * risk_free
    net = gross - turnover * COST_PER_UNIT_TURNOVER
    max_difference = float((net - published_net).abs().max())
    if max_difference > 1e-10:
        raise ValueError(f"Trace return reconciliation failed: {max_difference}")

    raw_metrics = raw_best.get("metrics") or {}
    trend = (report.get("baselines") or {}).get("trend_200d_weekly") or {}
    net_cagr = cagr(net)
    gross_cagr = cagr(gross)
    annualized_turnover = safe_float(raw_metrics.get("annualized_turnover"), float(turnover.mean() * 252.0))
    annualized_linear_cost_drag = float(turnover.mean() * 252.0 * COST_PER_UNIT_TURNOVER)

    cagr_delta = net_cagr - safe_float(trend.get("cagr"))
    sharpe_delta = safe_float(raw_metrics.get("sharpe_excess")) - safe_float(trend.get("sharpe_excess"))
    calmar_delta = safe_float(raw_metrics.get("calmar")) - safe_float(trend.get("calmar"))
    trend_turnover = safe_float(trend.get("annualized_turnover"))
    turnover_multiple = annualized_turnover / trend_turnover if trend_turnover > 0.0 else None
    edge_confirmed = bool(cagr_delta > 0.0 and sharpe_delta > 0.0 and calmar_delta > 0.0)

    return {
        "schema_version": "1.0.0",
        "architecture_id": report.get("architecture_id"),
        "candidate_signature": report.get("candidate_signature"),
        "source_snapshot_at": report.get("generated_at"),
        "mandate": {
            "position_set": "long_flat",
            "long_allowed": True,
            "flat_allowed": True,
            "short_allowed": False,
            "leverage_allowed": False,
            "orders_allowed": False,
        },
        "cost_model": {
            "basis_points_per_unit_turnover": 5.0,
            "metrics_are_net_of_costs": True,
            "applied_equally_to_candidate_and_benchmarks": True,
        },
        "diagnostic_leader": {
            "average_exposure": round(float(applied.mean()), 6),
            "time_long": round(float((applied > 0.0).mean()), 6),
            "time_flat": round(float((applied == 0.0).mean()), 6),
            "time_short": round(float((applied < 0.0).mean()), 6),
            "time_full_long": round(float((applied >= 1.0 - 1e-12).mean()), 6),
            "gross_cagr_before_costs": round(gross_cagr, 6),
            "net_cagr_after_costs": round(net_cagr, 6),
            "cagr_cost_drag": round(gross_cagr - net_cagr, 6),
            "annualized_turnover": round(annualized_turnover, 6),
            "annualized_linear_cost_drag": round(annualized_linear_cost_drag, 6),
            "observations": int(len(trace)),
        },
        "comparison_vs_trend_200d": {
            "cagr_delta": round(cagr_delta, 6),
            "sharpe_delta": round(sharpe_delta, 6),
            "calmar_delta": round(calmar_delta, 6),
            "turnover_multiple": round(turnover_multiple, 6) if turnover_multiple is not None else None,
            "lower_volatility": safe_float(raw_metrics.get("annualized_volatility")) < safe_float(trend.get("annualized_volatility")),
            "shallower_drawdown": abs(safe_float(raw_metrics.get("max_drawdown"))) < abs(safe_float(trend.get("max_drawdown"))),
            "edge_confirmed": edge_confirmed,
            "assessment": "confirmed" if edge_confirmed else "not_confirmed",
        },
        "public_boundary": {
            "daily_paths_exposed": False,
            "candidate_identity_exposed": False,
            "parameters_exposed": False,
            "raw_predictions_exposed": False,
            "holdout_used": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = read_json(args.report)
    trace = pd.read_csv(args.trace)
    payload = build(report, trace)
    write_json(args.output, payload)
    print(f"Built BRACE-SPX aggregate economics evidence: {args.output}")


if __name__ == "__main__":
    main()
