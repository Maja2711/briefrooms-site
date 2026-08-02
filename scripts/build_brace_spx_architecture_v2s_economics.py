#!/usr/bin/env python3
"""Build aggregate economics diagnostics for BRACE-SPX Architecture 2S."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

COST_PER_UNIT_TURNOVER = 0.0005
SHORT_BORROW_ANNUAL = 0.01
SHORT_BORROW_DAILY = (1.0 + SHORT_BORROW_ANNUAL) ** (1.0 / 252.0) - 1.0


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def cagr(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna().astype(float)
    if clean.empty:
        return 0.0
    years = max(len(clean) / 252.0, 1.0 / 252.0)
    total = float((1.0 + clean).prod())
    return -1.0 if total <= 0.0 else float(total ** (1.0 / years) - 1.0)


def build(report: Mapping[str, Any], trace: pd.DataFrame) -> dict[str, Any]:
    if report.get("architecture_id") != "spx-multisignal-regime-a2s":
        raise ValueError("Expected Architecture 2S report")
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
    if (applied < -1.0 - 1e-12).any() or (applied > 1.0 + 1e-12).any():
        raise ValueError("Architecture 2S violates the [-1,1] no-leverage mandate")

    cash_weight = (1.0 - applied.abs()).clip(0.0, 1.0)
    borrow = applied.clip(upper=0.0).abs() * SHORT_BORROW_DAILY
    gross_before_borrow = applied * asset + cash_weight * risk_free
    gross_after_borrow = gross_before_borrow - borrow
    net = gross_after_borrow - turnover * COST_PER_UNIT_TURNOVER
    max_difference = float((net - published_net).abs().max())
    if max_difference > 1e-10:
        raise ValueError(f"Trace return reconciliation failed: {max_difference}")

    raw_metrics = raw_best.get("metrics") or {}
    trend = (report.get("baselines") or {}).get("trend_200d_weekly") or {}
    net_cagr = cagr(net)
    pre_cost_cagr = cagr(gross_after_borrow)
    pre_borrow_cagr = cagr(gross_before_borrow)
    annualized_turnover = safe_float(raw_metrics.get("annualized_turnover"), float(turnover.mean() * 252.0))
    annualized_linear_cost_drag = float(turnover.mean() * 252.0 * COST_PER_UNIT_TURNOVER)
    annualized_borrow_drag = float(borrow.mean() * 252.0)
    cagr_delta = net_cagr - safe_float(trend.get("cagr"))
    sharpe_delta = safe_float(raw_metrics.get("sharpe_excess")) - safe_float(trend.get("sharpe_excess"))
    calmar_delta = safe_float(raw_metrics.get("calmar")) - safe_float(trend.get("calmar"))
    edge_confirmed = bool(cagr_delta > 0.0 and sharpe_delta > 0.0 and calmar_delta > 0.0)

    return {
        "schema_version": "2.0.0",
        "architecture_id": report.get("architecture_id"),
        "candidate_signature": report.get("candidate_signature"),
        "source_snapshot_at": report.get("generated_at"),
        "mandate": {
            "position_set": "long_short_flat",
            "minimum_exposure": -1.0,
            "maximum_exposure": 1.0,
            "long_allowed": True,
            "short_allowed": True,
            "flat_allowed": True,
            "leverage_allowed": False,
            "orders_allowed": False,
        },
        "cost_model": {
            "basis_points_per_unit_turnover": 5.0,
            "short_borrow_annual": SHORT_BORROW_ANNUAL,
            "metrics_are_net_of_costs": True,
            "applied_equally_to_candidate_and_benchmarks": True,
        },
        "diagnostic_leader": {
            "average_net_exposure": round(float(applied.mean()), 6),
            "average_gross_exposure": round(float(applied.abs().mean()), 6),
            "time_long": round(float((applied > 1e-12).mean()), 6),
            "time_flat": round(float((applied.abs() <= 1e-12).mean()), 6),
            "time_short": round(float((applied < -1e-12).mean()), 6),
            "time_full_long": round(float((applied >= 1.0 - 1e-12).mean()), 6),
            "time_full_short": round(float((applied <= -1.0 + 1e-12).mean()), 6),
            "gross_cagr_before_borrow_and_costs": round(pre_borrow_cagr, 6),
            "gross_cagr_after_borrow_before_turnover_costs": round(pre_cost_cagr, 6),
            "net_cagr_after_all_costs": round(net_cagr, 6),
            "cagr_total_cost_drag": round(pre_borrow_cagr - net_cagr, 6),
            "annualized_turnover": round(annualized_turnover, 6),
            "annualized_linear_cost_drag": round(annualized_linear_cost_drag, 6),
            "annualized_borrow_drag": round(annualized_borrow_drag, 6),
            "observations": int(len(trace)),
        },
        "comparison_vs_trend_200d": {
            "cagr_delta": round(cagr_delta, 6),
            "sharpe_delta": round(sharpe_delta, 6),
            "calmar_delta": round(calmar_delta, 6),
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
    payload = build(read_json(args.report), pd.read_csv(args.trace))
    write_json(args.output, payload)
    print(f"Built BRACE-SPX Architecture 2S economics: {args.output}")


if __name__ == "__main__":
    main()
