#!/usr/bin/env python3
"""Governed entry point for BRACE-SPX.

Keeps the legacy candidate search intact while adding model-agnostic statistical
controls and a genuinely single-use holdout per predeclared generation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import brace_spx_research as base
from brace_spx_integrity import (
    HoldoutRegistry,
    annualized_metrics,
    deflated_sharpe_probability,
    generation_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "research" / "brace_spx_holdout_registry.json"
GENERATION_SCHEMA = "governed-v1"


def _generation_payload() -> dict[str, Any]:
    pool_ids = sorted(candidate.candidate_id() for candidate in base.candidate_pool())
    return {
        "schema": GENERATION_SCHEMA,
        "base_model_version": base.MODEL_VERSION,
        "target": base.TARGET_SYMBOL,
        "candidate_pool_ids": pool_ids,
        "holdout_months": base.HOLDOUT_MONTHS,
        "purge_months": base.PURGE_MONTHS,
        "validation_months": base.VALIDATION_MONTHS,
        "cost": base.MONTHLY_COST,
    }


def _install_metric_patch() -> None:
    base.annualized_metrics = annualized_metrics


def _install_holdout_guard(registry: HoldoutRegistry, generation_id: str) -> None:
    original_train = base.train_and_evaluate_holdout
    original_gate = base.robustness_gate

    if registry.is_opened(generation_id):
        def locked_gate(
            candidate_metrics: Mapping[str, float],
            baselines: Mapping[str, Mapping[str, float]],
            fold_metrics: list[Mapping[str, float]],
        ) -> dict[str, Any]:
            result = original_gate(candidate_metrics, baselines, fold_metrics)
            result.update({
                "passed": False,
                "holdout_locked": True,
                "reason": "single-use holdout already opened for this predeclared generation",
            })
            return result
        base.robustness_gate = locked_gate
        return

    def guarded_train(development, holdout, candidate, seed):
        candidate_id = candidate.candidate_id()
        registry.open_once(
            generation_id,
            candidate_id,
            {
                "base_model_version": base.MODEL_VERSION,
                "development_end": development.index.max().date().isoformat(),
                "holdout_start": holdout.index.min().date().isoformat(),
                "holdout_end": holdout.index.max().date().isoformat(),
            },
        )
        return original_train(development, holdout, candidate, seed)

    base.train_and_evaluate_holdout = guarded_train


def _statistical_audit(report: dict[str, Any], ledger_path: Path, output_path: Path, generation_id: str, registry: HoldoutRegistry) -> None:
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ledger = {"experiments": []}
    experiments = ledger.get("experiments") if isinstance(ledger, dict) else []
    experiments = experiments if isinstance(experiments, list) else []
    champion = report.get("champion") or {}
    metrics = (champion.get("walk_forward") or {}).get("metrics") or {}
    sharpe = float(metrics.get("sharpe_excess", metrics.get("sharpe_zero_rf", 0.0)))
    observations = int(metrics.get("months", 0))
    trials = max(1, len(experiments))
    report["statistical_integrity"] = {
        "generation_id": generation_id,
        "single_use_holdout": True,
        "holdout_status": "opened" if registry.is_opened(generation_id) else "sealed",
        "conventional_excess_return_sharpe": True,
        "number_of_trials": trials,
        "deflated_sharpe_probability": round(
            deflated_sharpe_probability(sharpe, observations, trials), 6
        ),
        "pbo": {
            "available": False,
            "reason": "legacy ledger does not yet persist aligned candidate return vectors; aggregate fold metrics are insufficient for a valid CSCV estimate",
        },
        "interpretation": "DSR is an approximate multiple-testing diagnostic, not a promotion rule by itself.",
    }
    report.setdefault("governance", {}).update({
        "hypothesis_agnostic": True,
        "human_ideas_are_optional_challengers": True,
        "prefer_simpler_when_statistically_indistinguishable": True,
    })
    base.write_json(output_path, report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=24)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_PATH)
    parser.add_argument("--ledger", type=Path, default=base.LEDGER_PATH)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED)
    args = parser.parse_args()

    generation_id = generation_fingerprint(_generation_payload())
    registry = HoldoutRegistry(args.registry)
    _install_metric_patch()
    _install_holdout_guard(registry, generation_id)
    prices = base.download_prices([*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS], args.start)
    report = base.run_research(prices, args.budget, args.output, args.ledger, args.seed)
    _statistical_audit(report, args.ledger, args.output, generation_id, registry)
    champion = report.get("champion") or {}
    print(
        f"BRACE-SPX governed research complete: status={report['status']}, "
        f"experiments={report['experiments_total']}, champion={champion.get('candidate_id', 'none')}, "
        f"holdout={'opened' if registry.is_opened(generation_id) else 'sealed'}"
    )


if __name__ == "__main__":
    main()
