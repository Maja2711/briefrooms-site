#!/usr/bin/env python3
"""Deterministic LearningCase + Error Attribution + Structured Reasoning.

Learning cases are derived, zero-authority research artifacts built only from
settled prospective Experience Store rows. They preserve observable facts,
explicit attribution hypotheses and safe counterfactual checks. They never
claim causal identification from one observational trade and never expose or
depend on hidden LLM chain-of-thought.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "briefrooms-learning-case-v1"
STATUS_SCHEMA = "briefrooms-learning-cases-status-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _confidence01(value: Any) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.0:
        return number
    if 0.0 <= number <= 100.0:
        return number / 100.0
    return None


def _hypothesis(
    code: str,
    status: str,
    confidence: float | None,
    evidence_for: list[str],
    evidence_against: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "confidence": None if confidence is None else round(max(0.0, min(1.0, confidence)), 4),
        "evidence_for": evidence_for,
        "evidence_against": evidence_against or [],
        "causal_proof": False,
        "note": note or "Attribution hypothesis only; observational evidence does not establish causality.",
    }


def _attributions(exp: Mapping[str, Any]) -> list[dict[str, Any]]:
    outcome = exp.get("outcome") if isinstance(exp.get("outcome"), Mapping) else {}
    net = _finite(outcome.get("net_return_fraction"))
    gross = _finite(outcome.get("gross_return_fraction"))
    cost = _finite(outcome.get("cost_fraction"))
    r_mult = _finite(outcome.get("r_multiple"))
    mfe = _finite(outcome.get("mfe_fraction"))
    mae = _finite(outcome.get("mae_fraction"))
    benchmark = _finite(outcome.get("benchmark_return_fraction"))
    conf = _confidence01(exp.get("confidence"))
    action = str(exp.get("action") or "UNKNOWN").upper()
    loss = net is not None and net < 0.0

    rows: list[dict[str, Any]] = []

    if gross is not None and net is not None and gross > 0.0 >= net and cost is not None and cost > 0:
        rows.append(_hypothesis(
            "execution_or_cost",
            "SUPPORTED",
            0.95,
            ["gross_return_positive", "net_return_non_positive", "positive_recorded_cost"],
            note="Recorded costs are sufficient to explain the sign flip; this is arithmetic attribution, not a market-causal claim.",
        ))
    elif cost is not None:
        rows.append(_hypothesis(
            "execution_or_cost",
            "NOT_SUPPORTED" if not loss else "SUSPECTED",
            0.35 if loss else 0.2,
            ["recorded_cost_available"],
            ["cost_did_not_flip_gross_to_net"] if gross is not None and net is not None else [],
        ))
    else:
        rows.append(_hypothesis("execution_or_cost", "NOT_EVALUABLE", None, ["cost_not_recorded"]))

    if conf is None:
        rows.append(_hypothesis("confidence_calibration", "NOT_EVALUABLE", None, ["normalized_confidence_unavailable"]))
    elif loss and conf >= 0.70:
        rows.append(_hypothesis(
            "confidence_calibration",
            "SUSPECTED",
            min(0.9, 0.45 + 0.5 * conf),
            [f"normalized_confidence={conf:.3f}", "realized_net_return_negative"],
            note="A high-confidence loss is calibration evidence, not proof that the confidence model is miscalibrated. Aggregate bucket testing is required.",
        ))
    elif net is not None and net > 0 and conf >= 0.70:
        rows.append(_hypothesis("confidence_calibration", "NOT_SUPPORTED", 0.25, ["high_confidence", "realized_net_return_positive"]))
    else:
        rows.append(_hypothesis("confidence_calibration", "INCONCLUSIVE", 0.25, ["confidence_and_outcome_available"]))

    if loss and mfe is not None and mfe > 0:
        rows.append(_hypothesis(
            "timing_or_exit",
            "SUSPECTED",
            0.60,
            ["realized_net_return_negative", "maximum_favorable_excursion_positive"],
            note="Positive favorable excursion before a loss makes timing/exit worth testing; path-level causal attribution requires the actual price path.",
        ))
    else:
        rows.append(_hypothesis(
            "timing_or_exit",
            "NOT_EVALUABLE" if mfe is None else "INCONCLUSIVE",
            None if mfe is None else 0.2,
            ["maximum_favorable_excursion_unavailable"] if mfe is None else ["mfe_available"],
        ))

    if loss and r_mult is not None and r_mult <= -1.0:
        rows.append(_hypothesis(
            "risk_geometry",
            "SUSPECTED",
            0.55,
            [f"r_multiple={r_mult:.3f}", "loss_reached_or_exceeded_one_R"],
            note="The realized loss consumed at least one planned risk unit. This flags geometry for review but does not prove the stop/target design caused the loss.",
        ))
    elif mae is None and r_mult is None:
        rows.append(_hypothesis("risk_geometry", "NOT_EVALUABLE", None, ["mae_and_r_multiple_unavailable"]))
    else:
        rows.append(_hypothesis("risk_geometry", "INCONCLUSIVE", 0.25, ["risk_outcome_metrics_available"]))

    if benchmark is not None and net is not None:
        if net < benchmark:
            rows.append(_hypothesis(
                "market_or_regime_context",
                "SUSPECTED",
                0.45,
                ["strategy_return_below_recorded_benchmark"],
                note="Relative underperformance is a context signal only; it does not identify the omitted regime variable.",
            ))
        else:
            rows.append(_hypothesis("market_or_regime_context", "NOT_SUPPORTED", 0.2, ["strategy_return_not_below_recorded_benchmark"]))
    else:
        rows.append(_hypothesis("market_or_regime_context", "NOT_EVALUABLE", None, ["benchmark_return_unavailable"]))

    if exp.get("epistemic_state_id"):
        rows.append(_hypothesis(
            "epistemic_context",
            "INCONCLUSIVE",
            0.2,
            ["epistemic_state_lineage_present"],
            note="The Experience Store carries EpistemicState lineage, but this case does not infer state contents from the identifier alone.",
        ))
    else:
        rows.append(_hypothesis(
            "epistemic_context",
            "NOT_EVALUABLE",
            None,
            ["epistemic_state_lineage_missing"],
            note="Do not invent epistemic context. A future enriched case may resolve the frozen EpistemicState by ID.",
        ))

    if exp.get("market_snapshot_id"):
        rows.append(_hypothesis("data_quality", "INCONCLUSIVE", 0.15, ["market_snapshot_lineage_present"]))
    else:
        rows.append(_hypothesis(
            "data_quality",
            "NOT_EVALUABLE",
            None,
            ["market_snapshot_lineage_missing"],
            note="Missing canonical MarketSnapshot lineage is a provenance gap, not evidence that bad data caused the outcome.",
        ))

    if loss and action in {"LONG", "SHORT"}:
        rows.append(_hypothesis(
            "selection_or_direction",
            "SUSPECTED",
            0.4,
            ["economic_exposure_taken", "realized_net_return_negative"],
            note="A losing exposed decision is a candidate selection/direction error, but one outcome cannot identify why the selection was wrong.",
        ))
    else:
        rows.append(_hypothesis("selection_or_direction", "INCONCLUSIVE", 0.2, ["decision_and_outcome_available"]))
    return rows


def _counterfactuals(exp: Mapping[str, Any]) -> list[dict[str, Any]]:
    outcome = exp.get("outcome") if isinstance(exp.get("outcome"), Mapping) else {}
    net = _finite(outcome.get("net_return_fraction"))
    benchmark = _finite(outcome.get("benchmark_return_fraction"))
    action = str(exp.get("action") or "UNKNOWN").upper()
    checks: list[dict[str, Any]] = []
    if net is not None and action in {"LONG", "SHORT"}:
        checks.append({
            "counterfactual": "FLAT",
            "available": True,
            "counterfactual_return_fraction": 0.0,
            "observed_return_fraction": net,
            "increment_vs_observed_fraction": round(-net, 10),
            "identification": "arithmetic_no_position_counterfactual",
        })
    else:
        checks.append({"counterfactual": "FLAT", "available": False, "reason": "no_observed_economic_exposure_or_return"})
    if net is not None and benchmark is not None:
        checks.append({
            "counterfactual": "BENCHMARK",
            "available": True,
            "counterfactual_return_fraction": benchmark,
            "observed_return_fraction": net,
            "increment_vs_observed_fraction": round(benchmark - net, 10),
            "identification": "recorded_benchmark_comparison",
        })
    else:
        checks.append({"counterfactual": "BENCHMARK", "available": False, "reason": "benchmark_return_unavailable"})
    checks.append({
        "counterfactual": "ALTERNATE_STOP_OR_TARGET",
        "available": False,
        "reason": "price_path_not_available_in_canonical_experience; refusing to invent path-dependent counterfactual",
    })
    return checks


def _lessons(attributions: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_code = {str(row["code"]): row for row in attributions}
    lessons: list[dict[str, Any]] = []
    if by_code["execution_or_cost"]["status"] == "SUPPORTED":
        lessons.append({
            "lesson": "Require candidate edge to remain positive after the frozen cost model.",
            "scope": "hypothesis_for_aggregate_validation",
            "automatic_writeback": False,
        })
    if by_code["confidence_calibration"]["status"] == "SUSPECTED":
        lessons.append({
            "lesson": "Test the relevant high-confidence bucket for calibration error across many comparable cases.",
            "scope": "aggregate_calibration_test_required",
            "automatic_writeback": False,
        })
    if by_code["timing_or_exit"]["status"] == "SUSPECTED":
        lessons.append({
            "lesson": "Compare frozen exit geometry with path-aware alternatives in a separate prospective shadow experiment.",
            "scope": "new_experiment_required",
            "automatic_writeback": False,
        })
    if by_code["epistemic_context"]["status"] == "NOT_EVALUABLE":
        lessons.append({
            "lesson": "Capture canonical EpistemicState lineage before attributing errors to missing or conflicting context.",
            "scope": "data_lineage_improvement",
            "automatic_writeback": False,
        })
    if by_code["data_quality"]["status"] == "NOT_EVALUABLE":
        lessons.append({
            "lesson": "Preserve canonical MarketSnapshot lineage so data-quality hypotheses become testable.",
            "scope": "data_lineage_improvement",
            "automatic_writeback": False,
        })
    if not lessons:
        lessons.append({
            "lesson": "Retain the case for aggregate analysis; a single settled outcome does not justify a policy change.",
            "scope": "observation_only",
            "automatic_writeback": False,
        })
    return lessons


def build_learning_case(exp: Mapping[str, Any]) -> dict[str, Any]:
    if exp.get("status") != "SETTLED":
        raise ValueError("LearningCase requires a settled experience")
    outcome = exp.get("outcome")
    if not isinstance(outcome, Mapping):
        raise ValueError("settled experience has no outcome")
    decision_at = str(exp.get("decision_at") or "")
    settled_at = str(outcome.get("settled_at") or "")
    if _parse_time(settled_at) <= _parse_time(decision_at):
        raise ValueError("anti-lookahead violation: outcome is not strictly later than decision")
    net = _finite(outcome.get("net_return_fraction"))
    if net is None:
        outcome_class = "UNKNOWN"
    elif net > 0:
        outcome_class = "WIN"
    elif net < 0:
        outcome_class = "LOSS"
    else:
        outcome_class = "FLAT"

    attributions = _attributions(exp)
    identity = {
        "experience_id": str(exp.get("experience_id") or ""),
        "decision_event_id": str(exp.get("decision_event_id") or ""),
        "outcome_event_id": str(exp.get("outcome_event_id") or ""),
    }
    case = {
        "schema_version": SCHEMA_VERSION,
        "learning_case_id": "lcase-" + _sha(identity)[:24],
        "experience_id": identity["experience_id"],
        "engine": exp.get("engine"),
        "engine_version": exp.get("engine_version"),
        "instrument": exp.get("instrument"),
        "decision_at": decision_at,
        "settled_at": settled_at,
        "reasoning_mode": "deterministic_structured_facts_hypotheses_and_safe_counterfactuals",
        "private_chain_of_thought_required": False,
        "causal_identification_claimed": False,
        "observation": {
            "action": exp.get("action"),
            "confidence": exp.get("confidence"),
            "market_snapshot_id": exp.get("market_snapshot_id"),
            "epistemic_state_id": exp.get("epistemic_state_id"),
            "decision_envelope_id": exp.get("decision_envelope_id"),
        },
        "decision_thesis": {
            "action": exp.get("action"),
            "entry": exp.get("entry"),
            "stop_loss": exp.get("stop_loss"),
            "take_profit": exp.get("take_profit"),
            "expected_return": exp.get("expected_return"),
            "confidence": exp.get("confidence"),
        },
        "outcome_assessment": {
            "classification": outcome_class,
            "net_return_fraction": net,
            "gross_return_fraction": _finite(outcome.get("gross_return_fraction")),
            "cost_fraction": _finite(outcome.get("cost_fraction")),
            "r_multiple": _finite(outcome.get("r_multiple")),
            "mae_fraction": _finite(outcome.get("mae_fraction")),
            "mfe_fraction": _finite(outcome.get("mfe_fraction")),
            "benchmark_return_fraction": _finite(outcome.get("benchmark_return_fraction")),
            "exit_reason": outcome.get("exit_reason"),
        },
        "error_attribution": attributions,
        "counterfactual_checks": _counterfactuals(exp),
        "lesson_candidates": _lessons(attributions),
        "open_questions": [
            row["code"]
            for row in attributions
            if row["status"] in {"SUSPECTED", "NOT_EVALUABLE", "INCONCLUSIVE"}
        ],
        "authority": {
            "policy_writeback": False,
            "belief_writeback": False,
            "ranking_writeback": False,
            "sizing_writeback": False,
            "trade_execution": False,
        },
    }
    case["case_hash"] = _sha({k: v for k, v in case.items() if k != "case_hash"})
    return case


def build_learning_cases(experiences: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for exp in experiences:
        if exp.get("status") != "SETTLED":
            continue
        cases.append(build_learning_case(exp))
    return sorted(cases, key=lambda row: (str(row.get("decision_at") or ""), str(row["learning_case_id"])))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def materialize(store: Path, output: Path, status_path: Path) -> dict[str, Any]:
    experiences = read_jsonl(store)
    cases = build_learning_cases(experiences)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(_canonical(row) + "\n" for row in cases), encoding="utf-8")
    attribution_counts: dict[str, dict[str, int]] = {}
    for case in cases:
        for row in case["error_attribution"]:
            code = str(row["code"])
            status = str(row["status"])
            attribution_counts.setdefault(code, {})
            attribution_counts[code][status] = attribution_counts[code].get(status, 0) + 1
    status = {
        "schema_version": STATUS_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "experience_count": len(experiences),
        "settled_experience_count": sum(1 for row in experiences if row.get("status") == "SETTLED"),
        "learning_case_count": len(cases),
        "attribution_counts": attribution_counts,
        "zero_authority": True,
        "causal_identification_claimed": False,
        "anti_lookahead": "only SETTLED experiences with outcome.settled_at strictly after decision_at are eligible",
        "structured_reasoning": "observable facts + explicit hypotheses + safe counterfactuals; no hidden chain-of-thought artifact",
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize zero-authority BriefRooms LearningCases")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.store, args.output, args.status), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
