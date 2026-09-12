#!/usr/bin/env python3
"""Read-only observer wiring Learning Loop v2 to existing frozen artifacts.

This script never mutates trading decisions, thresholds, champion state or
promotion policy. It writes diagnostics only when --output is requested.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import learning_loop_v2 as ll


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _finite(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deep(mapping: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = mapping
        ok = True
        for token in path.split("."):
            if not isinstance(current, Mapping) or token not in current:
                ok = False
                break
            current = current[token]
        if ok and current is not None:
            return current
    return None


def _history(root: Path) -> list[dict[str, Any]]:
    base = root / "data" / "investments" / "gpw_daily_pick_history"
    rows = []
    if not base.exists():
        return rows
    for path in sorted(base.glob("????-??-??.json")):
        payload = _read(path)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _realized_return(row: Mapping[str, Any]) -> float | None:
    outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
    value = _deep(outcome, "return_percent", "realized_return_percent", "return", "realized_return")
    number = _finite(value)
    if number is None:
        return None
    if "return_percent" in outcome or "realized_return_percent" in outcome:
        return number / 100.0
    return number


def _dq(row: Mapping[str, Any]) -> dict[str, Any]:
    selection = row.get("selection") if isinstance(row.get("selection"), Mapping) else {}
    components = row.get("decision_quality_components") or selection.get("decision_quality_components")
    if not isinstance(components, Mapping):
        return {"status": "INSUFFICIENT_INPUT", "score": None,
                "reason": "explicit_ex_ante_decision_quality_components_not_emitted",
                "outcome_independent": True}
    try:
        return ll.assess_decision_quality(components)
    except ValueError as exc:
        return {"status": "INVALID_INPUT", "score": None, "reason": str(exc), "outcome_independent": True}


def _model_outputs(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    selection = row.get("selection") if isinstance(row.get("selection"), Mapping) else {}
    outputs = selection.get("model_outputs") or row.get("model_outputs")
    if not isinstance(outputs, Mapping):
        return None
    numeric = {str(key): value for key, value in outputs.items() if _finite(value) is not None}
    return numeric if len(numeric) >= 2 else None


def gpw_summary(root: Path) -> tuple[dict[str, Any], list[str]]:
    all_rows = _history(root)
    rows = [row for row in all_rows if str(row.get("decision") or "").upper() == "TRANSAKCJA"]
    dq_rows, outcome_rows, calibration_rows, ablations, deltas = [], [], [], [], []
    previous_evidence: Mapping[str, Any] | None = None
    shadow_candidates, near_misses, path_metrics = 0, [], []

    # Selected-trade diagnostics: decision/outcome separation, calibration,
    # model ablation and evidence deltas.
    for row in rows:
        dq_rows.append({"date": row.get("date"), **_dq(row)})
        realized = _realized_return(row)
        if realized is not None:
            outcome_rows.append({"date": row.get("date"), **ll.assess_outcome_quality(realized)})
        selection = row.get("selection") if isinstance(row.get("selection"), Mapping) else {}
        confidence = selection.get("confidence") or row.get("confidence")
        if confidence is not None and realized is not None:
            calibration_rows.append({"confidence": confidence, "outcome": int(realized > 0)})
        outputs = _model_outputs(row)
        if outputs:
            ablations.append({"date": row.get("date"), **ll.model_marginal_contribution(
                outputs, outcome=None if realized is None else int(realized > 0))})
        evidence = selection.get("evidence") or row.get("evidence")
        if isinstance(evidence, Mapping):
            if previous_evidence is not None:
                deltas.append({"date": row.get("date"), **ll.evidence_delta(previous_evidence, evidence)})
            previous_evidence = evidence

    # Shadow Book must cover ALL frozen decision days, including BRAK_TRANSAKCJI.
    # Those days are especially important for learning from false negatives.
    for row in all_rows:
        freeze = row.get("counterfactual_rejected_candidate_freeze")
        if not isinstance(freeze, Mapping):
            continue
        candidates = freeze.get("candidates") or freeze.get("rejected_candidates") or []
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            shadow_candidates += 1
            score = _deep(candidate, "score", "composite_score", "score_state.score", "score_state.composite_score")
            threshold = _deep(candidate, "threshold", "score_threshold", "first_blocking_gate.threshold", "score_state.threshold")
            nm = ll.near_miss_analysis(score, threshold, 3.0)
            if nm.get("is_near_miss"):
                near_misses.append({"date": row.get("date"), "candidate": candidate.get("ticker") or candidate.get("symbol"), **nm})

    # Existing rejected-candidate outcome store already carries MFE/MAE. Reuse it;
    # do not fetch/reconstruct market history a second time.
    for path in (root / "data").glob("**/*rejected*candidate*outcome*.json") if (root / "data").exists() else []:
        payload = _read(path)
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                mfe, mae = _finite(value.get("mfe_percent")), _finite(value.get("mae_percent"))
                if mfe is not None or mae is not None:
                    path_metrics.append({"candidate": value.get("ticker") or value.get("symbol"),
                                         "mfe_percent": mfe, "mae_percent": mae})
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    calibration = ll.confidence_calibration(calibration_rows, min_samples=30)
    gaps = []
    if rows and not any(item.get("status") == "ASSESSED" for item in dq_rows):
        gaps.append("GPW_DECISION_QUALITY_COMPONENTS_NOT_EMITTED")
    if rows and not calibration_rows:
        gaps.append("GPW_CONFIDENCE_NOT_EMITTED")
    if rows and not ablations:
        gaps.append("GPW_MODEL_OUTPUTS_NOT_EMITTED")
    if not shadow_candidates:
        gaps.append("GPW_SHADOW_BOOK_NOT_DISCOVERED")
    if not path_metrics:
        gaps.append("GPW_SHADOW_MFE_MAE_NOT_DISCOVERED")

    return {
        "selected_trade_count": len(rows),
        "history_day_count": len(all_rows),
        "decision_quality": {"principle": "ex_ante_only", "records": dq_rows[-20:]},
        "outcome_quality": {"principle": "ex_post_only", "records": outcome_rows[-20:]},
        "shadow_book": {"candidate_count": shadow_candidates, "prospective_only": True,
                        "includes_no_trade_days": True,
                        "near_miss_count": len(near_misses), "near_misses": near_misses[-50:]},
        "mfe_mae": {"status": "ASSESSED" if path_metrics else "NOT_AVAILABLE", "records": path_metrics[-100:]},
        "confidence_calibration": calibration,
        "model_marginal_contribution": {"status": "ASSESSED" if ablations else "NOT_AVAILABLE", "records": ablations[-20:]},
        "evidence_delta": {"status": "ASSESSED" if deltas else "NOT_AVAILABLE", "records": deltas[-20:]},
    }, gaps


def portfolio_summary(root: Path) -> tuple[dict[str, Any], list[str]]:
    memory = _read(root / "data" / "portfolio10k" / "learning_memory.json")
    if not isinstance(memory, dict):
        return {"status": "NOT_AVAILABLE"}, ["PORTFOLIO10K_LEARNING_MEMORY_NOT_FOUND"]
    reviews = memory.get("decision_reviews") if isinstance(memory.get("decision_reviews"), list) else []
    deltas, dq_rows, calibration_rows, ablations = [], [], [], []
    previous: Mapping[str, Any] | None = None
    for review in reviews:
        if not isinstance(review, Mapping):
            continue
        dq_rows.append({"as_of": review.get("as_of"), **_dq(review)})
        evidence = review.get("evidence")
        if isinstance(evidence, Mapping):
            if previous is not None:
                deltas.append({"as_of": review.get("as_of"), **ll.evidence_delta(previous, evidence)})
            previous = evidence
        confidence, correct = review.get("confidence"), review.get("outcome_correct")
        if confidence is not None and correct in (True, False, 0, 1):
            calibration_rows.append({"confidence": confidence, "outcome": int(bool(correct))})
        outputs = _model_outputs(review)
        if outputs:
            outcome = int(bool(correct)) if correct in (True, False, 0, 1) else None
            ablations.append({"as_of": review.get("as_of"), **ll.model_marginal_contribution(outputs, outcome=outcome)})
    gaps = []
    if reviews and not any(item.get("status") == "ASSESSED" for item in dq_rows):
        gaps.append("PORTFOLIO10K_DECISION_QUALITY_COMPONENTS_NOT_EMITTED")
    if reviews and not calibration_rows:
        gaps.append("PORTFOLIO10K_CONFIDENCE_NOT_EMITTED")
    if reviews and not ablations:
        gaps.append("PORTFOLIO10K_MODEL_OUTPUTS_NOT_EMITTED")
    return {
        "status": "ACTIVE", "review_count": len(reviews),
        "decision_quality": {"records": dq_rows[-20:]},
        "confidence_calibration": ll.confidence_calibration(calibration_rows, min_samples=30),
        "model_marginal_contribution": {"status": "ASSESSED" if ablations else "NOT_AVAILABLE", "records": ablations[-20:]},
        "evidence_delta": {"status": "ASSESSED" if deltas else "NOT_AVAILABLE", "records": deltas[-20:]},
    }, gaps


def challenger_summary(root: Path) -> tuple[dict[str, Any], list[str]]:
    payload = _read(root / "data" / "learning" / "live_challenger_input.json")
    if not isinstance(payload, Mapping):
        return {"status": "NOT_AVAILABLE", "decision": "NO_CHANGE",
                "reason": "no_explicit_live_challenger_input", "production_writeback_allowed": False}, ["LIVE_CHALLENGER_FEED_NOT_EMITTED"]
    try:
        result = ll.live_challenger_evaluation(
            input_hash_champion=str(payload.get("input_hash_champion") or ""),
            input_hash_challenger=str(payload.get("input_hash_challenger") or ""),
            champion=payload.get("champion") if isinstance(payload.get("champion"), Mapping) else {},
            challenger=payload.get("challenger") if isinstance(payload.get("challenger"), Mapping) else {},
            min_samples=int(payload.get("min_samples") or 30),
            min_excess_return_edge=float(payload.get("min_excess_return_edge") or 0.0),
            max_drawdown_worsening=float(payload.get("max_drawdown_worsening") or 0.0),
            max_brier_worsening=float(payload.get("max_brier_worsening") or 0.0),
        )
        return result, []
    except (TypeError, ValueError) as exc:
        return {"status": "INVALID_INPUT", "decision": "NO_CHANGE", "reason": str(exc),
                "production_writeback_allowed": False}, ["LIVE_CHALLENGER_INPUT_INVALID"]


def build_report(root: Path) -> dict[str, Any]:
    gpw, gaps_gpw = gpw_summary(root)
    portfolio, gaps_portfolio = portfolio_summary(root)
    challenger, gaps_challenger = challenger_summary(root)
    gaps = sorted(set(gaps_gpw + gaps_portfolio + gaps_challenger))
    report = {
        "schema_version": ll.SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "OBSERVATIONAL_ONLY",
        "production_writeback_allowed": False,
        "contracts": {
            "decision_quality": "ex_ante_only; P&L/outcomes forbidden",
            "outcome_quality": "ex_post_only and separate",
            "shadow_book": "existing prospective freeze only; no hindsight reconstruction",
            "model_marginal_contribution": "real model outputs only; no heuristic substitution",
            "live_challenger": "same-input shadow; candidate only; governed promotion gate required",
        },
        "gpw_daily": gpw,
        "portfolio10k": portfolio,
        "live_challenger": challenger,
        "instrumentation_gaps": gaps,
        "status": "ACTIVE_WITH_GAPS" if gaps else "ACTIVE",
    }
    report["report_sha256"] = ll.sha256_payload(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default=None, help="Optional diagnostics JSON path relative to repo root")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    report = build_report(root)
    if args.output:
        output = root / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "instrumentation_gaps": report["instrumentation_gaps"],
                      "production_writeback_allowed": False, "report_sha256": report["report_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
