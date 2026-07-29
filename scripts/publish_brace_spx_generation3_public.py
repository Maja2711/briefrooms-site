#!/usr/bin/env python3
"""Publish a minimal, aggregate-only BRACE-SPX generation 3 snapshot."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

FORBIDDEN_KEYS = {
    "candidate", "candidate_id", "family", "feature_set", "params",
    "threshold_high", "threshold_low", "monthly_returns", "experiments",
    "predictions", "probabilities", "fold_metrics"
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def assert_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_KEYS:
                raise ValueError(f"Forbidden public key at {path}.{key}")
            assert_safe(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_safe(child, f"{path}[{index}]")


def sanitize(report: Mapping[str, Any], manifest: Mapping[str, Any], verdict: Mapping[str, Any]) -> dict[str, Any]:
    champion = _mapping(report.get("champion"))
    metrics = _mapping(champion.get("metrics"))
    holdout = _mapping(report.get("holdout"))
    completed = _int(report.get("experiments_total"))
    total = max(completed, _int(report.get("candidate_space_size"), 48))
    remaining = max(0, total - completed)
    payload = {
        "schema_version": "1.0.0",
        "generation_id": "spx-focused-v3",
        "generation_label": "BRACE-SPX Generation 3",
        "target_instrument": "SPY / S&P 500",
        "status": str(verdict.get("status") or report.get("status") or "research_in_progress"),
        "updated_at": str(report.get("generated_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "candidate_signature": str(manifest.get("candidate_signature") or "pending-first-run"),
        "research_only": True,
        "live_activation": False,
        "progress": {
            "completed": completed,
            "total": total,
            "remaining": remaining,
            "ratio": round(completed / total, 6) if total else 0.0,
        },
        "design": {
            "scope": "focused_refinement",
            "candidate_space_size": 48,
            "model_family_count": 1,
            "feature_group_count": 2,
            "derived_from_generation": "spx-sealed-v2",
        },
        "development_leader": {
            "cagr": _float(metrics.get("cagr")),
            "sharpe_excess": _float(metrics.get("sharpe_excess")),
            "max_drawdown": _float(metrics.get("max_drawdown")),
            "calmar": _float(metrics.get("calmar")),
        },
        "strict_gate": {
            "passed": bool(verdict.get("strict_gate_passed", False)),
            "dsr_probability": _float(verdict.get("deflated_sharpe_probability")),
            "pbo_probability": _float(verdict.get("pbo_probability"), 1.0),
            "sharpe_advantage_over_best_baseline": _float(verdict.get("sharpe_advantage_over_best_baseline")),
        },
        "holdout": {
            "status": "sealed",
            "months": _int(holdout.get("months"), 48),
            "accessed": False,
        },
        "public_note": {
            "pl": "Badanie rozwojowe dla SPY. Brak zleceń live, brak dźwigni, holdout pozostaje zamknięty.",
            "en": "Development research for SPY. No live orders, no leverage, and the holdout remains sealed."
        }
    }
    assert_safe(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verdict", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = sanitize(
        json.loads(args.report.read_text(encoding="utf-8")),
        json.loads(args.manifest.read_text(encoding="utf-8")),
        json.loads(args.verdict.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
