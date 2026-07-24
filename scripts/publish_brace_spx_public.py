#!/usr/bin/env python3
"""Publish a strictly whitelisted BRACE-SPX browser report.

This boundary script is intentionally safe to keep in the public website repo. It
accepts a full private research report and emits only aggregate progress,
benchmark and development metrics. Model identities, features, parameters,
thresholds, predictions and experiment rows are never copied.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

FORBIDDEN_PUBLIC_KEYS = {
    "candidate",
    "candidate_id",
    "family",
    "feature_set",
    "params",
    "threshold_high",
    "threshold_low",
    "entry_return",
    "full_return",
    "min_exposure",
    "max_exposure",
    "volatility_target",
    "probabilities",
    "probability_tail",
    "predictions",
    "exposure_tail",
    "fold_metrics",
    "top_candidates",
    "experiments",
}

METRIC_KEYS = (
    "cagr",
    "annualized_volatility",
    "sharpe_zero_rf",
    "max_drawdown",
    "calmar",
    "annualized_turnover",
    "positive_year_ratio",
    "months",
    "average_exposure",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _metrics(source: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in METRIC_KEYS:
        if key not in source:
            continue
        result[key] = _integer(source[key]) if key == "months" else _number(source[key])
    return result


def _walk_forward(report: Mapping[str, Any]) -> Mapping[str, Any]:
    champion = _mapping(report.get("champion"))
    return _mapping(champion.get("walk_forward"))


def sanitize(report: Mapping[str, Any]) -> dict[str, Any]:
    walk = _walk_forward(report)
    champion_metrics = _metrics(_mapping(walk.get("metrics")))
    if "average_exposure" not in champion_metrics and "average_exposure" in walk:
        champion_metrics["average_exposure"] = _number(walk.get("average_exposure"))

    gate = _mapping(walk.get("robustness_gate"))
    if not gate:
        gate = _mapping(report.get("robustness_gate"))

    baseline_metrics = _mapping(walk.get("baseline_metrics"))
    completed = _integer(report.get("experiments_total"))
    total = max(completed, _integer(report.get("candidate_space_size"), completed))
    remaining = max(0, total - completed)
    ratio = completed / total if total else 0.0

    holdout = _mapping(report.get("sealed_holdout"))
    holdout_status = str(holdout.get("status") or "sealed")
    holdout_accessed = holdout_status.lower() not in {"sealed", "not_accessed", "untouched"}

    model_version = str(report.get("model_version") or "unknown")
    generated_at = str(report.get("generated_at") or "")
    public_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    payload = {
        "schema_version": "1.0.0",
        "model": "BRACE-SPX Lab",
        "target_instrument": "SPY / S&P 500",
        "status": "researching",
        "status_labels": {
            "pl": "Prywatne badania w toku",
            "en": "Private research in progress",
        },
        "research_only": True,
        "live_activation": False,
        "source_snapshot_at": generated_at,
        "public_report_at": public_at,
        "generation": {
            "version": model_version,
            "labels": {
                "pl": "Walidacja stabilności kolejnej generacji",
                "en": "Next-generation stability validation",
            },
        },
        "progress": {
            "experiments_completed": completed,
            "candidate_space_size": total,
            "experiments_remaining": remaining,
            "completion_ratio": round(ratio, 6),
        },
        "sealed_holdout": {
            "status": "sealed" if not holdout_accessed else "reviewed",
            "months": _integer(holdout.get("months"), 48),
            "accessed": holdout_accessed,
            "labels": {
                "pl": "Nienaruszony — wynik końcowy nie był używany do strojenia" if not holdout_accessed else "Otwarty wyłącznie dla zamrożonego kandydata",
                "en": "Untouched — the final result has not been used for tuning" if not holdout_accessed else "Opened only for a frozen candidate",
            },
        },
        "development_champion": {
            "metrics": champion_metrics,
            "robustness_gate": {
                "passed": bool(gate.get("passed", False)),
                "positive_folds": _integer(gate.get("positive_folds", gate.get("positive_robust_folds", 0))),
                "required_positive_folds": _integer(gate.get("required_positive_folds", 0)),
            },
        },
        "benchmarks": {
            "buy_and_hold": _metrics(_mapping(baseline_metrics.get("buy_hold"))),
            "trend_200d": _metrics(_mapping(baseline_metrics.get("trend_200d"))),
        },
        "public_boundary": {
            "code_exposed": False,
            "parameters_exposed": False,
            "raw_predictions_exposed": False,
            "full_experiment_ledger_exposed": False,
        },
        "notes": {
            "pl": "To raport badawczy, nie sygnał transakcyjny. Silnik nie składa zleceń i nie został dopuszczony do użycia live.",
            "en": "This is a research report, not a trading signal. The engine places no orders and has not been approved for live use.",
        },
    }

    positive = payload["development_champion"]["robustness_gate"]["positive_folds"]
    required = payload["development_champion"]["robustness_gate"]["required_positive_folds"]
    payload["development_champion"]["robustness_gate"]["labels"] = {
        "pl": f"{positive} prób dodatnich; wymagane minimum {required}",
        "en": f"{positive} folds positive; at least {required} required",
    }
    assert_public_boundary(payload)
    return payload


def assert_public_boundary(payload: Any, path: str = "root") -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Forbidden public key at {path}.{key}")
            assert_public_boundary(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            assert_public_boundary(value, f"{path}[{index}]")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    payload = sanitize(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
