#!/usr/bin/env python3
"""Historical Replay Tournament for exact Stock Trading v2 factory candidates.

Tournament evidence is discovery evidence only. A winner earns a fresh,
future-only holdout Challenger; it never earns production authority directly.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_challenger_factory as factory
    from scripts import stock_trading_v2_challenger_eval as evaluator
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_exact_replay as exact
    from scripts import stock_trading_v2_policy_learner as learner
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_challenger_factory as factory
    import stock_trading_v2_challenger_eval as evaluator
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_exact_replay as exact
    import stock_trading_v2_policy_learner as learner

ROOT = Path(__file__).resolve().parents[1]
TOURNAMENT_ROOT = ROOT / "data/investments/stock_trading_v2_tournaments"
SCHEMA_VERSION = "stock-trading-v2-replay-tournament-v1"


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _iter(root: Path) -> list[dict[str, Any]]:
    return [_read(path) for path in sorted(root.rglob("*.json"))] if root.exists() else []


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)


def candidate_metrics(candidate: Mapping[str, Any], samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = [float(row["incremental_net_return_percent"]) for row in samples]
    symbols = {str(row.get("symbol") or "") for row in samples}
    dates = sorted({str(row.get("session_date") or "") for row in samples if row.get("session_date")})
    positive_rate = (sum(value > 0 for value in values) / len(values)) if values else 0.0
    mean = statistics.mean(values) if values else 0.0
    median = statistics.median(values) if values else 0.0
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    midpoint = max(1, len(values) // 2)
    first = values[:midpoint]
    second = values[midpoint:]
    first_mean = statistics.mean(first) if first else 0.0
    second_mean = statistics.mean(second) if second else 0.0
    positive = [max(0.0, value) for value in values]
    positive_total = sum(positive)
    concentration = (max(positive) / positive_total) if positive_total > 0 else 1.0
    lower = evaluator._bootstrap_lower_bound(
        values,
        confidence=0.80,
        draws=2000,
        seed_text=str(candidate.get("candidate_id") or ""),
    ) if values else None
    checks = {
        "minimum_samples": len(values) >= 12,
        "minimum_symbols": len(symbols) >= 3,
        "positive_mean": mean > 0.0,
        "positive_rate": positive_rate >= 0.55,
        "both_time_halves_non_negative": first_mean >= 0.0 and second_mean >= 0.0,
        "positive_bootstrap_floor": lower is not None and lower > 0.0,
        "concentration": concentration <= 0.60,
    }
    robustness_pass = all(checks.values())
    tournament_score = (float(lower) if lower is not None else -math.inf) - 0.10 * stdev
    return {
        "sample_n": len(values),
        "unique_symbols": len(symbols),
        "date_span": [dates[0], dates[-1]] if dates else None,
        "mean_incremental_net_return_percent": round(mean, 8),
        "median_incremental_net_return_percent": round(median, 8),
        "positive_rate": round(positive_rate, 8),
        "stdev_percent": round(stdev, 8),
        "first_half_mean_percent": round(first_mean, 8),
        "second_half_mean_percent": round(second_mean, 8),
        "maximum_single_positive_contribution_share": round(concentration, 8),
        "bootstrap_lower_bound_percent": round(float(lower), 8) if lower is not None else None,
        "tournament_score": round(tournament_score, 8) if math.isfinite(tournament_score) else None,
        "checks": checks,
        "robustness_pass": robustness_pass,
    }


def make_challenger(candidate: Mapping[str, Any], tournament_sha: str, promotion_config: Mapping[str, Any], *, start_at: str) -> dict[str, Any]:
    identity = {
        "deployment_sha256": candidate["deployment_sha256"],
        "base_manifest_revision": candidate["base_manifest_revision"],
        "base_component_version": candidate["base_component_version"],
        "horizon_sessions": candidate["horizon_sessions"],
    }
    challenger_id = "stchallv2-exact-" + contracts.payload_sha256(identity)[:20]
    payload: dict[str, Any] = {
        "schema_version": learner.SCHEMA_VERSION,
        "challenger_id": challenger_id,
        "created_at": start_at,
        "validation_start_at": start_at,
        "component": candidate["research_component"],
        "horizon_sessions": candidate["horizon_sessions"],
        "champion": {
            "manifest_revision": candidate["base_manifest_revision"],
            "component_version": candidate["base_component_version"],
        },
        "change": {
            "type": "exact_config_patch_challenger",
            "deployment_id": candidate["deployment_id"],
            "deployment_sha256": candidate["deployment_sha256"],
            "safety_gates_unchanged": True,
        },
        "production_candidate": {
            "component": candidate["production_component"],
            "deployment_id": candidate["deployment_id"],
            "base_manifest_revision": candidate["base_manifest_revision"],
            "base_component_version": candidate["base_component_version"],
            "deployment_sha256": candidate["deployment_sha256"],
            "deployment_spec": candidate["deployment_spec"],
        },
        "exact_replay": candidate["replay_contract"],
        "origin_evidence": {
            "factory_candidate_id": candidate["candidate_id"],
            "factory_candidate_sha256": candidate["candidate_sha256"],
            "tournament_sha256": tournament_sha,
        },
        "holdout": {
            "fixed_paired_n": int(promotion_config.get("fixed_paired_n") or 30),
            "confidence_level": float(promotion_config.get("confidence_level") or 0.9),
            "bootstrap_samples": int(promotion_config.get("bootstrap_samples") or 4000),
            "minimum_net_incremental_return_percent": float(promotion_config.get("minimum_net_incremental_return_percent") or 0.0),
            "minimum_net_positive_rate": float(promotion_config.get("minimum_net_positive_rate") or 0.55),
            "minimum_unique_symbols": int(promotion_config.get("minimum_unique_symbols") or 5),
            "minimum_span_days": int(promotion_config.get("minimum_span_days") or 10),
            "maximum_single_positive_contribution_share": float(promotion_config.get("maximum_single_positive_contribution_share") or 0.5),
            "single_formal_look": True,
            "historical_evidence_reuse_forbidden": True,
        },
        "state": "COLLECTING_FRESH_HOLDOUT",
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "production_promotion_enabled": False,
            "future_only_holdout": True,
            "safety_gates_unchanged": True,
            "tournament_is_discovery_only": True,
        },
    }
    body = dict(payload)
    payload["challenger_sha256"] = contracts.payload_sha256(body)
    learner.validate_challenger(payload)
    return payload


def run(*, candidate_root: Path, experience_root: Path, admission_root: Path, outcome_root: Path, promotion_config_path: Path, challenger_root: Path, tournament_root: Path = TOURNAMENT_ROOT) -> dict[str, Any]:
    events, admissions, outcomes = _iter(experience_root), _iter(admission_root), _iter(outcome_root)
    rows: list[dict[str, Any]] = []
    for path in sorted(candidate_root.glob("*.json")) if candidate_root.exists() else []:
        candidate = _read(path)
        factory.validate_candidate(candidate)
        try:
            samples = exact.collect_entry_threshold_samples(candidate=candidate, events=events, admissions=admissions, outcomes=outcomes)
            metrics = candidate_metrics(candidate, samples)
            rows.append({"candidate_id": candidate["candidate_id"], "deployment_id": candidate["deployment_id"], "deployment_sha256": candidate["deployment_sha256"], "research_component": candidate["research_component"], "production_component": candidate["production_component"], "metrics": metrics})
        except (ValueError, TypeError) as exc:
            rows.append({"candidate_id": candidate["candidate_id"], "deployment_id": candidate["deployment_id"], "research_component": candidate.get("research_component"), "status": "UNREPLAYABLE", "reason": str(exc), "metrics": {"robustness_pass": False}})

    winners = [row for row in rows if (row.get("metrics") or {}).get("robustness_pass") is True]
    winners.sort(key=lambda row: (float((row["metrics"] or {}).get("tournament_score") or -1e99), float((row["metrics"] or {}).get("mean_incremental_net_return_percent") or -1e99), str(row["candidate_id"])), reverse=True)
    winner = winners[0] if winners else None
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": contracts.iso_utc(),
        "candidate_results": rows,
        "winner": winner,
        "governance": {"production_decision_influence": False, "fresh_holdout_required_after_win": True},
    }
    body = dict(payload)
    payload["tournament_sha256"] = contracts.payload_sha256(body)
    _atomic(tournament_root / "latest.json", payload)

    challenger_id = None
    created = False
    if winner:
        candidate = _read(candidate_root / f"{winner['candidate_id']}.json")
        promotion_config = _read(promotion_config_path)
        challenger = make_challenger(candidate, payload["tournament_sha256"], promotion_config, start_at=payload["generated_at"])
        challenger_id = challenger["challenger_id"]
        created = learner.persist(challenger_root, challenger)
    return {
        "schema_version": "stock-trading-v2-replay-tournament-run-v1",
        "candidates": len(rows),
        "robust_winners": len(winners),
        "winner_candidate_id": winner.get("candidate_id") if winner else None,
        "challenger_id": challenger_id,
        "challenger_created": created,
        "production_decision_influence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=factory.DEFAULT_ROOT)
    parser.add_argument("--experience-root", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--outcome-root", type=Path, required=True)
    parser.add_argument("--promotion-config", type=Path, required=True)
    parser.add_argument("--challengers", type=Path, default=learner.DEFAULT_ROOT)
    parser.add_argument("--tournament-root", type=Path, default=TOURNAMENT_ROOT)
    args = parser.parse_args()
    result = run(candidate_root=args.candidates, experience_root=args.experience_root, admission_root=args.admission_root, outcome_root=args.outcome_root, promotion_config_path=args.promotion_config, challenger_root=args.challengers, tournament_root=args.tournament_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
