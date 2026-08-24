#!/usr/bin/env python3
"""Aggregate periodic report for PR31 ARIS-inspired Belief Core shadow diagnostics.

The report is research-only. It summarizes whether simpler competing
representations preserve decision-relevant Belief information with lower
complexity. It never promotes a representation or changes Belief Core.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Dict, Iterable, List, Mapping, Optional

REPORT_VERSION = "belief-aris-shadow-report-v1"


def _mean(values: Iterable[float]) -> Optional[float]:
    rows = list(values)
    return round(fmean(rows), 6) if rows else None


def load_reports(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("aris_belief_shadow.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("contract_version") != "belief-aris-shadow-v1":
            continue
        if payload.get("mode") != "research_shadow":
            continue
        rows.append(payload)
    return rows


def aggregate(reports: List[Mapping[str, Any]]) -> Dict[str, Any]:
    winner_counts: Counter[str] = Counter()
    candidate_seen: Counter[str] = Counter()
    candidate_pruned: Counter[str] = Counter()
    selected_retention: List[float] = []
    selected_complexity_reduction: List[float] = []
    selected_residual_ratio: List[float] = []
    selected_probability_gap: List[float] = []
    disagreements: List[float] = []
    by_belief: Dict[str, Counter[str]] = defaultdict(Counter)
    beliefs_total = 0

    for report in reports:
        authority = report.get("authority") or {}
        if authority.get("decision_influence") is not False or authority.get("belief_core_writeback_enabled") is not False:
            continue
        for belief_id, row in (report.get("beliefs") or {}).items():
            beliefs_total += 1
            selected_name = str(row.get("selected_representation") or "unknown")
            winner_counts[selected_name] += 1
            by_belief[str(belief_id)][selected_name] += 1
            disagreements.append(float(row.get("representation_disagreement") or 0.0))
            selected_probability_gap.append(abs(float(row.get("residual_probability_gap") or 0.0)))
            representations = [x for x in (row.get("representations") or []) if isinstance(x, Mapping)]
            by_name = {str(x.get("name")): x for x in representations}
            full = by_name.get("full_representatives")
            selected = by_name.get(selected_name)
            for rep in representations:
                name = str(rep.get("name") or "unknown")
                candidate_seen[name] += 1
                if bool(rep.get("pruned")):
                    candidate_pruned[name] += 1
            if selected:
                selected_retention.append(float(selected.get("information_retention") or 0.0))
                full_complexity = float((full or {}).get("complexity_units") or 0.0)
                selected_complexity = float(selected.get("complexity_units") or 0.0)
                if full_complexity > 0:
                    selected_complexity_reduction.append(max(0.0, 1.0 - selected_complexity / full_complexity))
                    full_mass = float((full or {}).get("retained_effective_mass") or 0.0)
                    residual_mass = float(selected.get("residual_effective_mass") or 0.0)
                    if full_mass > 0:
                        selected_residual_ratio.append(max(0.0, residual_mass / full_mass))

    non_full_wins = beliefs_total - winner_counts.get("full_representatives", 0)
    non_full_share = (non_full_wins / beliefs_total) if beliefs_total else 0.0
    mean_retention = _mean(selected_retention)
    mean_complexity_reduction = _mean(selected_complexity_reduction)
    mean_gap = _mean(selected_probability_gap)

    blockers: List[str] = []
    if len(reports) < 10:
        blockers.append("fewer_than_10_cycles")
    if beliefs_total < 100:
        blockers.append("fewer_than_100_belief_evaluations")
    if non_full_share < 0.10:
        blockers.append("simpler_representations_rarely_win")
    if mean_retention is not None and mean_retention < 0.90:
        blockers.append("selected_information_retention_below_90pct")
    if mean_gap is not None and mean_gap > 0.05:
        blockers.append("selected_probability_gap_above_5pp")
    blockers.append("outcome_calibration_not_yet_part_of_pr31_report")
    blockers.append("decision_quality_not_yet_part_of_pr31_report")

    if len(reports) < 10 or beliefs_total < 100:
        recommendation = "COLLECT_MORE_SHADOW_DATA"
    elif non_full_share >= 0.20 and (mean_retention or 0.0) >= 0.90 and (mean_complexity_reduction or 0.0) >= 0.20 and (mean_gap or 1.0) <= 0.03:
        recommendation = "REVIEW_CANDIDATES_FOR_CALIBRATION_TESTING"
    else:
        recommendation = "KEEP_SHADOW"

    return {
        "report_version": REPORT_VERSION,
        "mode": "research_shadow",
        "authority": {
            "decision_influence": False,
            "belief_core_writeback_enabled": False,
            "automatic_promotion_enabled": False,
            "automatic_tuning_enabled": False,
        },
        "sample": {"cycles": len(reports), "belief_evaluations": beliefs_total},
        "winner_counts": dict(sorted(winner_counts.items())),
        "non_full_wins": non_full_wins,
        "non_full_win_share": round(non_full_share, 6),
        "mean_selected_information_retention": mean_retention,
        "mean_selected_complexity_reduction": mean_complexity_reduction,
        "mean_selected_residual_mass_ratio": _mean(selected_residual_ratio),
        "mean_absolute_probability_gap": mean_gap,
        "mean_representation_disagreement": _mean(disagreements),
        "candidate_prune_rates": {
            name: round(candidate_pruned[name] / count, 6) if count else None
            for name, count in sorted(candidate_seen.items())
        },
        "belief_winner_counts": {belief_id: dict(sorted(counts.items())) for belief_id, counts in sorted(by_belief.items())},
        "recommendation": recommendation,
        "promotion_blockers": blockers,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    sample = report["sample"]
    lines = [
        "# ARIS Shadow Report",
        "",
        f"Cycles: **{sample['cycles']}**  ",
        f"Belief evaluations: **{sample['belief_evaluations']}**  ",
        f"Recommendation: **{report['recommendation']}**",
        "",
        "## Representation winners",
    ]
    for name, count in report.get("winner_counts", {}).items():
        lines.append(f"- `{name}`: {count}")
    lines += [
        "",
        "## Aggregate diagnostics",
        f"- Non-full win share: {report.get('non_full_win_share')}",
        f"- Mean selected information retention: {report.get('mean_selected_information_retention')}",
        f"- Mean selected complexity reduction: {report.get('mean_selected_complexity_reduction')}",
        f"- Mean selected residual mass ratio: {report.get('mean_selected_residual_mass_ratio')}",
        f"- Mean absolute probability gap: {report.get('mean_absolute_probability_gap')}",
        f"- Mean representation disagreement: {report.get('mean_representation_disagreement')}",
        "",
        "## Promotion blockers",
    ]
    for row in report.get("promotion_blockers", []):
        lines.append(f"- {row}")
    lines += [
        "",
        "No result in this report can alter Belief Core, EpistemicState, BRACE, WES, ranking, sizing or execution.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build periodic ARIS shadow aggregate report")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = aggregate(load_reports(Path(args.input_dir)))
    (output / "ARIS_SHADOW_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "ARIS_SHADOW_REPORT.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"cycles": report["sample"]["cycles"], "beliefs": report["sample"]["belief_evaluations"], "recommendation": report["recommendation"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
