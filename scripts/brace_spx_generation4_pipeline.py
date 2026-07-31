#!/usr/bin/env python3
"""Run, audit and publish BRACE-SPX Generation 4 development evidence."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import brace_spx_generation3_research as gen3
import brace_spx_generation4_research as gen4
import brace_spx_generation4_selection as selection
import brace_spx_generation4_verdict as verdicts
import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
PUBLIC = ROOT / "data" / "public"


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=16)
    args = parser.parse_args()

    pool = gen4.candidate_pool()
    ids = {candidate.candidate_id() for candidate in pool}
    if len(pool) != 16 or len(ids) != 16:
        raise RuntimeError("Generation 4 must contain 16 unique candidates")
    if not ids.isdisjoint({candidate.candidate_id() for candidate in gen3.candidate_pool()}):
        raise RuntimeError("Generation 4 repeats a Generation 3 candidate")

    symbols = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, gen4.engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = gen4.run(prices, args.budget, base.RANDOM_SEED + 4000)

    audit_path = RESEARCH / "brace_spx_generation4_selection.json"
    audit = selection.run(gen4.engine.LEDGER_PATH, gen4.engine.OUTPUT_PATH, audit_path)
    manifest = json.loads(gen4.engine.MANIFEST_PATH.read_text(encoding="utf-8"))
    final = verdicts.evaluate(report, audit, manifest)
    verdict_path = RESEARCH / "brace_spx_generation4_verdict.json"
    write(verdict_path, final)

    manifest["selection_governance"] = {
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selected_candidate_id": final.get("selected_candidate_id"),
        "strict_development_gate_passed": bool(final.get("strict_gate_passed")),
        "holdout_candidate": final.get("selected_candidate_id") if final.get("strict_gate_passed") else None,
        "holdout_authorization": "pending_explicit_human_approval" if final.get("strict_gate_passed") else "none_strict_gate_failed",
        "generation3_reopened": False,
    }
    write(gen4.engine.MANIFEST_PATH, manifest)

    selected = ((audit.get("selection") or {}).get("selected") or {})
    diversity = report.get("diversity") or {}
    public = {
        "schema_version": "1.0.0",
        "generation_id": "spx-diversified-v4",
        "generation_label": "BRACE-SPX Generation 4",
        "target_instrument": "SPY / S&P 500",
        "status": final.get("status"),
        "updated_at": report.get("generated_at"),
        "candidate_signature": manifest.get("candidate_signature"),
        "research_only": True,
        "live_activation": False,
        "progress": {
            "completed": report.get("experiments_total", 0),
            "total": report.get("candidate_space_size", 16),
            "remaining": report.get("experiments_remaining", 0),
        },
        "design": {
            "candidate_space_size": 16,
            "parameter_grid": False,
            "archetype_count": 6,
            "generation3_reopened": False,
        },
        "selected_development_result": {
            "cagr": selected.get("cagr"),
            "sharpe_excess": selected.get("sharpe_excess"),
            "max_drawdown": selected.get("max_drawdown"),
            "positive_folds": selected.get("positive_folds"),
            "folds": selected.get("folds"),
        },
        "overfitting_and_diversity": {
            "pbo_probability": final.get("pbo_probability"),
            "median_absolute_pairwise_correlation": diversity.get("median_absolute_pairwise_correlation"),
            "effective_independent_candidates": diversity.get("effective_independent_candidates"),
            "largest_cluster_share": diversity.get("largest_cluster_share"),
        },
        "strict_gate": {"passed": bool(final.get("strict_gate_passed"))},
        "holdout": {"status": "sealed", "months": 48, "accessed": False},
    }
    write(PUBLIC / "brace_spx_generation4_public.json", public)
    print(
        f"BRACE-SPX v4 pipeline: status={final['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']} "
        f"pbo={final['pbo_probability']}"
    )


if __name__ == "__main__":
    main()
