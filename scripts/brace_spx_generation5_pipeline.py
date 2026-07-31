#!/usr/bin/env python3
"""Run, audit and publish BRACE-SPX Generation 5 development evidence."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import brace_spx_generation4_research as gen4
import brace_spx_generation5_research as gen5
import brace_spx_generation5_selection as selection
import brace_spx_generation5_verdict as verdicts
import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
PUBLIC = ROOT / "data" / "public"


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_predecessor() -> None:
    manifest_path = RESEARCH / "brace_spx_generation4_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("Generation 4 manifest is required before Generation 5")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    holdout = manifest.get("holdout") or {}
    if str(manifest.get("generation_id")) != "spx-diversified-v4":
        raise RuntimeError("Unexpected Generation 4 identity")
    if holdout.get("start") != "2022-08-31" or holdout.get("end") != "2026-07-31":
        raise RuntimeError("Generation 5 refuses to shift the inherited Generation 4 holdout")
    if bool(holdout.get("accessed", False)) or int(holdout.get("access_count", 0)) != 0:
        raise RuntimeError("Generation 4 holdout is no longer sealed")


def enrich_exposure_correlations(
    report: dict,
    development: pd.DataFrame,
    pool: list[base.Candidate],
    seed: int,
) -> None:
    exposures = {
        candidate.candidate_id(): gen5._validation_exposure(development, candidate, seed + offset * 101)
        for offset, candidate in enumerate(pool)
    }
    matrix = pd.DataFrame(exposures).sort_index()
    correlations = matrix.corr().abs()
    stats = ((report.get("geometry") or {}).get("candidate_stats") or {})
    for candidate_id in matrix.columns:
        peers = correlations[candidate_id].drop(candidate_id, errors="ignore").dropna()
        stats.setdefault(candidate_id, {})["mean_absolute_exposure_correlation_to_pool"] = round(
            float(peers.mean()) if len(peers) else 1.0,
            6,
        )
    report.setdefault("geometry", {})["candidate_stats"] = stats
    report["geometry"]["exposure_diversity"] = gen5._matrix_diagnostics(matrix)
    write(gen5.engine.OUTPUT_PATH, report)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=12)
    args = parser.parse_args()

    validate_predecessor()
    pool = gen5.candidate_pool()
    ids = {candidate.candidate_id() for candidate in pool}
    if len(pool) != 12 or len(ids) != 12:
        raise RuntimeError("Generation 5 must contain twelve unique candidates")
    if not ids.isdisjoint({candidate.candidate_id() for candidate in gen4.candidate_pool()}):
        raise RuntimeError("Generation 5 repeats a Generation 4 candidate")

    symbols = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, gen5.engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    seed = base.RANDOM_SEED + 5000
    report = gen5.run(prices, args.budget, seed)
    development, _sealed_holdout = gen5.fixed_holdout_split(base.monthly_dataset(prices))
    enrich_exposure_correlations(report, development, pool, seed)
    report = json.loads(gen5.engine.OUTPUT_PATH.read_text(encoding="utf-8"))

    audit_path = RESEARCH / "brace_spx_generation5_selection.json"
    audit = selection.run(gen5.engine.LEDGER_PATH, gen5.engine.OUTPUT_PATH, audit_path)
    manifest = json.loads(gen5.engine.MANIFEST_PATH.read_text(encoding="utf-8"))
    ledger = json.loads(gen5.engine.LEDGER_PATH.read_text(encoding="utf-8"))
    final = verdicts.evaluate(report, audit, manifest, ledger)
    verdict_path = RESEARCH / "brace_spx_generation5_verdict.json"
    write(verdict_path, final)

    selected = ((audit.get("selection") or {}).get("selected") or {})
    raw_champion = report.get("champion") or {}
    manifest["selection_governance"] = {
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_excess_sharpe_champion_id": raw_champion.get("candidate_id"),
        "hypothesis_agnostic_selected_candidate_id": final.get("selected_candidate_id"),
        "selection_rule_changed_candidate": bool(
            raw_champion.get("candidate_id")
            and final.get("selected_candidate_id")
            and raw_champion.get("candidate_id") != final.get("selected_candidate_id")
        ),
        "strict_development_gate_passed": bool(final.get("strict_gate_passed")),
        "holdout_candidate": final.get("selected_candidate_id") if final.get("strict_gate_passed") else None,
        "holdout_authorization": (
            "pending_explicit_human_approval"
            if final.get("strict_gate_passed")
            else "none_strict_gate_failed"
        ),
        "generation4_reopened": False,
    }
    write(gen5.engine.MANIFEST_PATH, manifest)

    return_diversity = report.get("return_diversity") or {}
    exposure_diversity = ((report.get("geometry") or {}).get("exposure_diversity") or {})
    public = {
        "schema_version": "1.0.0",
        "generation_id": "spx-state-geometry-v5",
        "generation_label": "BRACE-SPX Generation 5",
        "target_instrument": "SPY / S&P 500",
        "status": final.get("status"),
        "updated_at": report.get("generated_at"),
        "candidate_signature": manifest.get("candidate_signature"),
        "research_only": True,
        "live_activation": False,
        "progress": {
            "completed": report.get("experiments_total", 0),
            "total": report.get("candidate_space_size", 12),
            "remaining": report.get("experiments_remaining", 0),
        },
        "design": {
            "candidate_space_size": 12,
            "shared_signal_count": 1,
            "geometry_family_count": 4,
            "generation4_reopened": False,
            "fixed_holdout": True,
        },
        "selected_development_result": {
            "candidate_name": selected.get("candidate_name"),
            "geometry_family": selected.get("geometry_family"),
            "cagr": selected.get("cagr"),
            "sharpe_excess": selected.get("sharpe_excess"),
            "max_drawdown": selected.get("max_drawdown"),
            "positive_folds": selected.get("positive_folds"),
            "folds": selected.get("folds"),
            "active_exposure_buckets": selected.get("active_exposure_buckets"),
        },
        "overfitting_and_stability": {
            "pbo_probability": final.get("pbo_probability"),
            "selected_dsr_probability": (final.get("selected_deflated_sharpe_ratio") or {}).get("probability"),
            "median_absolute_return_correlation": return_diversity.get("median_absolute_pairwise_correlation"),
            "median_absolute_exposure_correlation": exposure_diversity.get("median_absolute_pairwise_correlation"),
            "effective_return_candidates": return_diversity.get("effective_independent_candidates"),
            "effective_exposure_candidates": exposure_diversity.get("effective_independent_candidates"),
            "largest_return_cluster_share": return_diversity.get("largest_cluster_share"),
            "largest_exposure_cluster_share": exposure_diversity.get("largest_cluster_share"),
        },
        "strict_gate": {"passed": bool(final.get("strict_gate_passed"))},
        "holdout": {
            "status": "sealed",
            "months": 48,
            "start": "2022-08-31",
            "end": "2026-07-31",
            "accessed": False,
        },
    }
    write(PUBLIC / "brace_spx_generation5_public.json", public)
    print(
        f"BRACE-SPX v5 pipeline: status={final['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"pbo={final['pbo_probability']}"
    )


if __name__ == "__main__":
    main()
