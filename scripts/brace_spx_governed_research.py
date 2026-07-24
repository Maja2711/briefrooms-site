#!/usr/bin/env python3
"""Governed BRACE-SPX research runner.

This runner preserves the existing experiment ledger while enforcing a genuinely
sealed holdout by default.  Development candidates are evaluated and ranked on
purged chronological folds only.  The final holdout can be opened once, only by
an explicit release command and only after predeclared statistical gates pass.

The multiple-testing diagnostics are deliberately labelled as fold-based
proxies.  They are useful safeguards, not substitutes for a full CSCV/PBO and
Deflated Sharpe implementation based on complete return paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import pandas as pd

import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "research" / "brace_spx_research.json"
LEDGER_PATH = ROOT / "data" / "research" / "brace_spx_experiments.json"
HOLDOUT_REGISTRY_PATH = ROOT / "data" / "research" / "brace_spx_holdout_registry.json"
MODEL_VERSION = "0.2.0-governed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path: Path, default: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generation_id(development: pd.DataFrame, pool_size: int) -> str:
    descriptor = {
        "model_version": MODEL_VERSION,
        "development_start": development.index.min().date().isoformat(),
        "development_end": development.index.max().date().isoformat(),
        "candidate_pool_size": int(pool_size),
        "fold_policy": [base.MIN_TRAIN_MONTHS, base.VALIDATION_MONTHS, base.PURGE_MONTHS],
        "holdout_months": base.HOLDOUT_MONTHS,
    }
    raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def fold_dsr_proxy(experiments: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Estimate selection risk from fold Sharpe dispersion and trial count.

    This proxy uses only stored fold summaries.  It intentionally does not call
    itself a full Deflated Sharpe Ratio because skew, kurtosis and complete
    return paths are not present in the legacy ledger.
    """
    if not experiments:
        return {"available": False, "reason": "no_experiments"}
    ranked = sorted(
        experiments,
        key=lambda row: float((row.get("walk_forward") or {}).get("objective", -999.0)),
        reverse=True,
    )
    folds = (ranked[0].get("walk_forward") or {}).get("fold_metrics") or []
    values = np.asarray([float(item.get("sharpe_zero_rf", np.nan)) for item in folds], dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return {"available": False, "reason": "insufficient_fold_statistics"}
    mean_sr = float(values.mean())
    sigma = float(values.std(ddof=1))
    trials = max(2, len(experiments))
    normal = NormalDist()
    gamma = 0.5772156649015329
    q1 = normal.inv_cdf(max(1e-9, min(1 - 1e-9, 1 - 1 / trials)))
    q2 = normal.inv_cdf(max(1e-9, min(1 - 1e-9, 1 - 1 / (trials * math.e))))
    expected_max = sigma * ((1 - gamma) * q1 + gamma * q2)
    if sigma <= 1e-12:
        probability = 1.0 if mean_sr > expected_max else 0.0
    else:
        z = (mean_sr - expected_max) * math.sqrt(values.size) / sigma
        probability = normal.cdf(z)
    return {
        "available": True,
        "method": "fold_dispersion_dsr_proxy",
        "trials": trials,
        "folds": int(values.size),
        "mean_fold_sharpe_legacy": round(mean_sr, 6),
        "fold_sharpe_std": round(sigma, 6),
        "expected_max_under_selection": round(expected_max, 6),
        "probability_skill_after_selection": round(float(probability), 6),
        "warning": "Proxy only; legacy ledger lacks full return paths, skew and kurtosis.",
    }


def pbo_rank_proxy(experiments: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Measure how often the overall champion falls below the fold median."""
    if len(experiments) < 3:
        return {"available": False, "reason": "insufficient_experiments"}
    ranked = sorted(
        experiments,
        key=lambda row: float((row.get("walk_forward") or {}).get("objective", -999.0)),
        reverse=True,
    )
    champion = ranked[0]
    champion_folds = (champion.get("walk_forward") or {}).get("fold_metrics") or []
    fold_count = len(champion_folds)
    if fold_count < 2:
        return {"available": False, "reason": "insufficient_folds"}
    failures = 0
    usable = 0
    for index in range(fold_count):
        cross_section = []
        champion_value = None
        for row in experiments:
            folds = (row.get("walk_forward") or {}).get("fold_metrics") or []
            if index >= len(folds):
                continue
            value = float(folds[index].get("objective_advantage", np.nan))
            if not math.isfinite(value):
                continue
            cross_section.append(value)
            if row.get("candidate_id") == champion.get("candidate_id"):
                champion_value = value
        if champion_value is None or len(cross_section) < 3:
            continue
        usable += 1
        if champion_value <= float(np.median(cross_section)):
            failures += 1
    if usable == 0:
        return {"available": False, "reason": "no_comparable_folds"}
    return {
        "available": True,
        "method": "champion_below_fold_median_proxy",
        "folds": usable,
        "failures": failures,
        "pbo_proxy": round(failures / usable, 6),
        "warning": "Proxy only; full CSCV requires synchronized return paths for all candidates.",
    }


def _release_allowed(robust: Mapping[str, Any], dsr: Mapping[str, Any], pbo: Mapping[str, Any]) -> bool:
    return bool(
        robust.get("passed")
        and dsr.get("available")
        and float(dsr.get("probability_skill_after_selection", 0.0)) >= 0.95
        and pbo.get("available")
        and float(pbo.get("pbo_proxy", 1.0)) <= 0.20
    )


def run_governed_research(
    prices: pd.DataFrame,
    budget: int,
    output_path: Path = OUTPUT_PATH,
    ledger_path: Path = LEDGER_PATH,
    registry_path: Path = HOLDOUT_REGISTRY_PATH,
    seed: int = base.RANDOM_SEED,
    release_holdout: bool = False,
) -> Dict[str, Any]:
    frame = base.monthly_dataset(prices)
    development, holdout = base.holdout_split(frame)
    ledger = _load(ledger_path, {
        "schema_version": "1.0.0",
        "model": "BRACE-SPX Research Lab",
        "experiments": [],
        "created_at": _now(),
    })
    experiments = ledger.setdefault("experiments", [])
    seen = {str(item.get("candidate_id")) for item in experiments}
    pool = base.candidate_pool()
    unseen = [candidate for candidate in pool if candidate.candidate_id() not in seen]
    rng = np.random.default_rng(seed + len(experiments))
    if unseen:
        order = rng.permutation(len(unseen)).tolist()
        selected = [unseen[i] for i in order[: max(0, int(budget))]]
    else:
        selected = []

    new_rows = []
    for offset, candidate in enumerate(selected):
        result = base.evaluate_candidate_walk_forward(development, candidate, seed + offset * 17)
        row = base.serialize_experiment(candidate, result)
        experiments.append(row)
        new_rows.append(row)

    ranked = sorted(
        experiments,
        key=lambda row: float((row.get("walk_forward") or {}).get("objective", -999.0)),
        reverse=True,
    )
    baseline_development = {
        name: base.evaluate_exposure(development, exposure)["metrics"]
        for name, exposure in base.baseline_exposures(development).items()
    }
    champion_row = ranked[0] if ranked else None
    champion_summary = None
    robust: Dict[str, Any] = {"passed": False, "reason": "no_champion"}
    if champion_row:
        wf = champion_row["walk_forward"]
        robust = base.robustness_gate(wf["metrics"], wf.get("baseline_metrics") or baseline_development, wf["fold_metrics"])
        champion_summary = {
            "candidate_id": champion_row["candidate_id"],
            "candidate": champion_row["candidate"],
            "walk_forward": wf,
            "robustness_gate": robust,
        }

    dsr = fold_dsr_proxy(ranked)
    pbo = pbo_rank_proxy(ranked)
    gen_id = generation_id(development, len(pool))
    registry = _load(registry_path, {"schema_version": "1.0.0", "generations": {}})
    generations = registry.setdefault("generations", {})
    generation = generations.setdefault(gen_id, {
        "created_at": _now(),
        "holdout_accessed": False,
        "holdout_access_count": 0,
        "candidate_pool_size": len(pool),
        "development_end": development.index.max().date().isoformat(),
        "holdout_start": holdout.index.min().date().isoformat(),
        "holdout_end": holdout.index.max().date().isoformat(),
    })

    release_eligible = _release_allowed(robust, dsr, pbo)
    status = "development_searching" if unseen else "candidate_space_exhausted"
    holdout_payload: Dict[str, Any] = {
        "status": "sealed" if not generation.get("holdout_accessed") else "consumed",
        "accessed": bool(generation.get("holdout_accessed")),
        "access_count": int(generation.get("holdout_access_count", 0)),
        "release_eligible": release_eligible,
    }
    if release_holdout:
        if generation.get("holdout_accessed"):
            raise RuntimeError(f"Holdout for generation {gen_id} has already been consumed")
        if not release_eligible or champion_row is None:
            raise RuntimeError("Predeclared development gates do not permit holdout release")
        candidate = base.Candidate(**champion_row["candidate"])
        holdout_result = base.train_and_evaluate_holdout(development, holdout, candidate, seed + 999)
        holdout_baselines = {
            name: base.evaluate_exposure(holdout, exposure)["metrics"]
            for name, exposure in base.baseline_exposures(holdout).items()
        }
        comparator_name, comparator = max(holdout_baselines.items(), key=lambda item: base.objective(item[1]))
        wow = base.wow_gate(holdout_result["metrics"], comparator)
        generation.update({
            "holdout_accessed": True,
            "holdout_access_count": 1,
            "accessed_at": _now(),
            "champion_candidate_id": champion_row["candidate_id"],
            "wow_passed": bool(wow.get("passed")),
        })
        holdout_payload = {
            "status": "consumed",
            "accessed": True,
            "access_count": 1,
            "comparator": comparator_name,
            "result": holdout_result,
            "wow_gate": wow,
        }
        status = "wow_candidate" if wow.get("passed") else "holdout_failed"

    now = _now()
    ledger.update({
        "updated_at": now,
        "experiments": ranked[:2000],
        "champion_candidate_id": champion_row.get("candidate_id") if champion_row else None,
        "active_generation_id": gen_id,
    })
    registry["updated_at"] = now
    _write(ledger_path, ledger)
    _write(registry_path, registry)

    report = {
        "schema_version": "2.0.0",
        "model": "BRACE-SPX Research Lab",
        "model_version": MODEL_VERSION,
        "status": status,
        "generated_at": now,
        "research_only": True,
        "live_activation": False,
        "target_instrument": base.TARGET_SYMBOL,
        "generation_id": gen_id,
        "data_start": frame.index.min().date().isoformat(),
        "data_end": frame.index.max().date().isoformat(),
        "development_end": development.index.max().date().isoformat(),
        "sealed_holdout_start": holdout.index.min().date().isoformat(),
        "sealed_holdout_end": holdout.index.max().date().isoformat(),
        "new_experiments": len(new_rows),
        "experiments_total": len(ranked),
        "candidate_space_size": len(pool),
        "candidates_remaining": max(0, len(pool) - len(seen) - len(new_rows)),
        "development_baselines": baseline_development,
        "champion": champion_summary,
        "selection_diagnostics": {"fold_dsr_proxy": dsr, "pbo_rank_proxy": pbo},
        "sealed_holdout": holdout_payload,
        "governance": {
            "hypothesis_agnostic": True,
            "no_lookahead": True,
            "chronological_purged_folds": True,
            "single_use_holdout": True,
            "holdout_release_requires_explicit_flag": True,
            "holdout_baselines_hidden_before_release": True,
            "single_traded_instrument": base.TARGET_SYMBOL,
            "transaction_cost_per_turnover": base.MONTHLY_COST,
            "no_leverage": True,
            "no_live_orders": True,
            "promotion_requires_human_review": True,
        },
    }
    _write(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=24)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--registry", type=Path, default=HOLDOUT_REGISTRY_PATH)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED)
    parser.add_argument("--release-holdout", action="store_true")
    args = parser.parse_args()
    prices = base.download_prices([*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS], args.start)
    report = run_governed_research(
        prices,
        args.budget,
        args.output,
        args.ledger,
        args.registry,
        args.seed,
        args.release_holdout,
    )
    print(
        "BRACE-SPX governed research complete: "
        f"status={report['status']}, experiments={report['experiments_total']}, "
        f"holdout={report['sealed_holdout']['status']}"
    )


if __name__ == "__main__":
    main()
