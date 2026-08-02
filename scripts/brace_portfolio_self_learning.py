#!/usr/bin/env python3
"""Governed weekly self-learning loop for BRACE Portfolio Engine.

The loop learns only inside the BRACE shadow methodology. It evaluates mature
shadow decisions, proposes small bounded changes to three decision gates,
requires repeated confirmation, and writes an immutable challenger manifest.
It never changes the production baseline, enables a real broker, or bypasses
promotion controls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "portfolio10k"
DEFAULT_CONFIG = DATA_ROOT / "config.json"
DEFAULT_ADAPTIVE_POLICY = DATA_ROOT / "adaptive_policy.json"
DEFAULT_LEARNING_STATE = DATA_ROOT / "learning_state.json"
DEFAULT_SHADOW_LOG = DATA_ROOT / "shadow_log.json"
DEFAULT_VALIDATION = DATA_ROOT / "research_validation.json"
DEFAULT_REGISTRY = DATA_ROOT / "methodology_registry.json"
DEFAULT_MARKET_CACHE = ROOT / ".cache" / "brace_portfolio_market.json"

SCHEMA_VERSION = "brace-portfolio-self-learning-v1"
POLICY_SCHEMA_VERSION = "brace-adaptive-policy-v1"
HORIZON_WEIGHTS = {7: 0.35, 30: 1.0, 90: 1.50}
ACTION_DIRECTION = {"ADD": 1, "REPLACE": 1, "REDUCE": -1, "EXIT": -1}
EXCESS_RETURN_DEADBAND = 0.005
MIN_EFFECTIVE_SAMPLES = 12.0
REQUIRED_CONFIRMATIONS = 2

LEARNABLE_BOUNDS = {
    "minimum_confidence": (0.60, 0.75),
    "minimum_score_improvement": (6.5, 10.5),
    "minimum_expected_alpha": (0.0175, 0.0400),
}
TIGHTEN_STEPS = {
    "minimum_confidence": 0.02,
    "minimum_score_improvement": 0.50,
    "minimum_expected_alpha": 0.0025,
}
RELAX_STEPS = {
    "minimum_confidence": -0.01,
    "minimum_score_improvement": -0.25,
    "minimum_expected_alpha": -0.0010,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def canonical_sha256(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _history_points(rows: Sequence[Mapping[str, Any]]) -> list[tuple[date, float]]:
    points: list[tuple[date, float]] = []
    for row in rows:
        try:
            observed = _as_date(str(row.get("date"))[:10])
            price = _finite(row.get("close_pln") if row.get("close_pln") is not None else row.get("close"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            points.append((observed, price))
    return sorted(set(points), key=lambda item: item[0])


def _price_on_or_after(points: Sequence[tuple[date, float]], target: date) -> tuple[date, float] | None:
    return next((item for item in points if item[0] >= target), None)


def _price_on_or_before(points: Sequence[tuple[date, float]], target: date) -> tuple[date, float] | None:
    eligible = [item for item in points if item[0] <= target]
    return eligible[-1] if eligible else None


def _path_excursions(
    points: Sequence[tuple[date, float]],
    start: date,
    end: date,
    start_price: float,
) -> tuple[float | None, float | None]:
    returns = [price / start_price - 1.0 for observed, price in points if start <= observed <= end]
    if not returns:
        return None, None
    return round(max(returns), 8), round(min(returns), 8)


def collect_due_outcomes(
    shadow_log: Mapping[str, Any],
    market: Mapping[str, Any],
    existing_events: Sequence[Mapping[str, Any]],
    as_of: date,
) -> list[dict[str, Any]]:
    """Create append-only horizon outcomes from frozen shadow decisions."""
    existing_ids = {str(item.get("outcome_event_id")) for item in existing_events}
    instruments = market.get("instruments") or {}
    histories = {
        str(instrument_id): _history_points((row or {}).get("history") or [])
        for instrument_id, row in instruments.items()
    }
    benchmark_points = histories.get("fwia") or []
    latest_benchmark_date = benchmark_points[-1][0] if benchmark_points else None
    created: list[dict[str, Any]] = []

    for run in shadow_log.get("runs", []) or []:
        run_id = str(run.get("shadow_run_id") or "")
        generated_text = str(run.get("generated_at") or "")
        if not run_id or not generated_text:
            continue
        signal_date = _as_date(generated_text)
        benchmark_start = _price_on_or_before(benchmark_points, signal_date)
        if benchmark_start is None:
            continue
        for index, decision in enumerate(run.get("decisions", []) or []):
            instrument = str(decision.get("instrument") or "")
            action = str(decision.get("brace_decision") or "").upper()
            points = histories.get(instrument) or []
            signal_price = _finite(decision.get("signal_price"))
            if not instrument or not points or signal_price <= 0:
                continue
            decision_id = str(decision.get("decision_id") or f"{run_id}:{instrument}:{index}")
            for horizon, horizon_weight in HORIZON_WEIGHTS.items():
                target = signal_date + timedelta(days=horizon)
                event_id = f"{decision_id}:{horizon}d"
                if event_id in existing_ids or as_of < target:
                    continue
                end_point = _price_on_or_after(points, target)
                benchmark_end = _price_on_or_after(benchmark_points, target)
                if end_point is None or benchmark_end is None:
                    continue
                if latest_benchmark_date is not None and benchmark_end[0] > latest_benchmark_date:
                    continue
                instrument_return = end_point[1] / signal_price - 1.0
                benchmark_return = benchmark_end[1] / benchmark_start[1] - 1.0
                excess = instrument_return - benchmark_return
                direction = ACTION_DIRECTION.get(action)
                eligible = direction is not None
                signed_excess = excess * direction if direction else 0.0
                correct = None
                if eligible:
                    correct = signed_excess > EXCESS_RETURN_DEADBAND
                mfe, mae = _path_excursions(points, signal_date, end_point[0], signal_price)
                event = {
                    "outcome_event_id": event_id,
                    "decision_id": decision_id,
                    "shadow_run_id": run_id,
                    "instrument": instrument,
                    "action": action,
                    "signal_date": signal_date.isoformat(),
                    "evaluation_date": end_point[0].isoformat(),
                    "horizon_days": horizon,
                    "horizon_weight": horizon_weight,
                    "signal_price": round(signal_price, 8),
                    "evaluation_price": round(end_point[1], 8),
                    "benchmark_signal_price": round(benchmark_start[1], 8),
                    "benchmark_evaluation_price": round(benchmark_end[1], 8),
                    "instrument_return": round(instrument_return, 8),
                    "benchmark_return": round(benchmark_return, 8),
                    "excess_return": round(excess, 8),
                    "signed_excess_return": round(signed_excess, 8),
                    "direction_correct": correct,
                    "eligible_for_learning": eligible,
                    "maximum_favorable_excursion": mfe,
                    "maximum_adverse_excursion": mae,
                    "immutable": True,
                }
                event["event_sha256"] = canonical_sha256(event)
                created.append(event)
                existing_ids.add(event_id)
    return created


def learning_statistics(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(events)
    eligible = [item for item in rows if item.get("eligible_for_learning")]
    effective = sum(_finite(item.get("horizon_weight"), 1.0) for item in eligible)
    correct_weight = sum(
        _finite(item.get("horizon_weight"), 1.0)
        for item in eligible
        if item.get("direction_correct") is True
    )
    signed_excess = sum(
        _finite(item.get("signed_excess_return")) * _finite(item.get("horizon_weight"), 1.0)
        for item in eligible
    )
    by_action: dict[str, dict[str, Any]] = {}
    for action in sorted(ACTION_DIRECTION):
        action_rows = [item for item in eligible if item.get("action") == action]
        weight = sum(_finite(item.get("horizon_weight"), 1.0) for item in action_rows)
        by_action[action] = {
            "events": len(action_rows),
            "effective_samples": round(weight, 6),
            "directional_accuracy": round(
                sum(_finite(item.get("horizon_weight"), 1.0) for item in action_rows if item.get("direction_correct") is True)
                / weight,
                6,
            ) if weight else None,
            "mean_signed_excess_return": round(
                sum(_finite(item.get("signed_excess_return")) * _finite(item.get("horizon_weight"), 1.0) for item in action_rows)
                / weight,
                8,
            ) if weight else None,
        }
    return {
        "outcome_events": len(rows),
        "eligible_events": len(eligible),
        "effective_samples": round(effective, 6),
        "directional_accuracy": round(correct_weight / effective, 6) if effective else None,
        "mean_signed_excess_return": round(signed_excess / effective, 8) if effective else None,
        "by_action": by_action,
    }


def _bounded(name: str, value: float) -> float:
    low, high = LEARNABLE_BOUNDS[name]
    clipped = max(low, min(high, value))
    digits = 4 if name == "minimum_expected_alpha" else 3
    return round(clipped, digits)


def effective_policy(base_policy: Mapping[str, Any], active_overrides: Mapping[str, Any]) -> dict[str, float]:
    return {
        name: _bounded(name, _finite(active_overrides.get(name), _finite(base_policy.get(name))))
        for name in LEARNABLE_BOUNDS
    }


def propose_candidate(
    base_policy: Mapping[str, Any],
    active_overrides: Mapping[str, Any],
    statistics: Mapping[str, Any],
) -> tuple[dict[str, float], str]:
    current = effective_policy(base_policy, active_overrides)
    samples = _finite(statistics.get("effective_samples"))
    accuracy = statistics.get("directional_accuracy")
    signed_excess = statistics.get("mean_signed_excess_return")
    if samples < MIN_EFFECTIVE_SAMPLES or accuracy is None or signed_excess is None:
        return {}, "WARMUP_INSUFFICIENT_MATURE_ACTIONABLE_OUTCOMES"

    accuracy = _finite(accuracy)
    signed_excess = _finite(signed_excess)
    if accuracy < 0.55 or signed_excess < 0.0:
        steps = TIGHTEN_STEPS
        reason = "TIGHTEN_WEAK_DIRECTIONAL_OR_EXCESS_RESULTS"
    elif accuracy >= 0.68 and signed_excess >= 0.01:
        steps = RELAX_STEPS
        reason = "CAUTIOUSLY_RELAX_STRONG_STABLE_RESULTS"
    else:
        return {}, "HOLD_PARAMETERS_RESULTS_IN_NEUTRAL_BAND"

    candidate = {
        name: _bounded(name, current[name] + steps[name])
        for name in LEARNABLE_BOUNDS
    }
    if candidate == current:
        return {}, "HOLD_PARAMETERS_AT_SAFETY_BOUND"
    return candidate, reason


def research_gate_passed(validation: Mapping[str, Any]) -> tuple[bool, dict[str, bool]]:
    checks = {
        "no_lookahead_audit": validation.get("no_lookahead_audit") is True,
        "costs_and_fx_included": validation.get("costs_and_fx_included") is True,
        "minimum_observations": int(validation.get("observations") or 0) >= 252,
        "not_single_instrument_dependent": validation.get("not_single_instrument_dependent") is True,
        "no_leverage": validation.get("no_leverage") is True,
        "no_short_sales": validation.get("no_short_sales") is True,
        "no_cfds": validation.get("no_cfds") is True,
        "reproducible_run": validation.get("reproducible_run") is True,
        "full_manifest": validation.get("full_manifest") is True,
    }
    return all(checks.values()), checks


def advance_adaptive_policy(
    current: Mapping[str, Any],
    base_config: Mapping[str, Any],
    statistics: Mapping[str, Any],
    validation: Mapping[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    base_policy = base_config.get("policy") or base_config
    active = dict(current.get("active_overrides") or {})
    candidate, reason = propose_candidate(base_policy, active, statistics)
    signature = canonical_sha256(candidate) if candidate else None
    previous_signature = current.get("candidate_signature")
    confirmations = int(current.get("consecutive_confirmations") or 0)
    if signature:
        confirmations = confirmations + 1 if signature == previous_signature else 1
    else:
        confirmations = 0
    gate_passed, gate_checks = research_gate_passed(validation)
    sample_gate = _finite(statistics.get("effective_samples")) >= MIN_EFFECTIVE_SAMPLES
    activate = bool(
        candidate
        and sample_gate
        and gate_passed
        and confirmations >= REQUIRED_CONFIRMATIONS
    )
    if activate:
        active = dict(candidate)
        status = "ACTIVE_SHADOW_PARAMETERS"
    elif candidate:
        status = "CANDIDATE_PENDING_CONFIRMATION" if gate_passed else "CANDIDATE_BLOCKED_BY_RESEARCH_GATE"
    else:
        status = "OBSERVING" if active else "WARMUP"

    output = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "status": status,
        "apply_to_shadow_decisions": bool(active),
        "never_apply_to_real_broker": True,
        "base_config_sha256": canonical_sha256(base_config),
        "active_overrides": active,
        "candidate_overrides": candidate,
        "candidate_signature": signature,
        "consecutive_confirmations": confirmations,
        "required_confirmations": REQUIRED_CONFIRMATIONS,
        "minimum_effective_samples": MIN_EFFECTIVE_SAMPLES,
        "learning_reason": reason,
        "statistics": dict(statistics),
        "research_gate": {"passed": gate_passed, "checks": gate_checks},
        "bounds": {name: list(bounds) for name, bounds in LEARNABLE_BOUNDS.items()},
        "policy": {
            "scope": "BRACE challenger shadow decisions only",
            "changes_apply_next_week": True,
            "maximum_one_bounded_step_per_week": True,
            "production_baseline_immutable": True,
            "automatic_real_trading_prohibited": True,
            "controller_promotion_gates_still_required": True,
        },
    }
    output["content_sha256"] = canonical_sha256(output)
    return output


def _new_learning_state(generated_at: datetime) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": "brace-portfolio-v3.1-adaptive-shadow",
        "mode": "governed_weekly_self_learning",
        "outcomes": [],
        "statistics": {},
        "audit": [],
    }


def _script_sha() -> str:
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if len(github_sha) == 40:
        return github_sha
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def update_registry_with_candidate(
    registry: Mapping[str, Any],
    adaptive: Mapping[str, Any],
    learning: Mapping[str, Any],
    validation: Mapping[str, Any],
    market: Mapping[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    candidate = adaptive.get("candidate_overrides") or {}
    if not candidate:
        return dict(registry)
    from brace_portfolio_learning import append_challenger, create_challenger_manifest

    events = [item for item in learning.get("outcomes", []) if item.get("eligible_for_learning")]
    dates = sorted(str(item.get("evaluation_date")) for item in events if item.get("evaluation_date"))
    signature = str(adaptive.get("candidate_signature") or canonical_sha256(candidate))
    manifest = create_challenger_manifest(
        methodology_id="brace-portfolio-engine-adaptive-shadow",
        version=f"3.1-{generated_at.date().isoformat()}-{signature[:8]}",
        parameters={
            "overrides": dict(candidate),
            "bounds": adaptive.get("bounds"),
            "scope": "shadow_only",
        },
        code_sha=_script_sha(),
        data_sha=str(market.get("content_sha256") or canonical_sha256(market)),
        training_window={
            "from": dates[0] if dates else None,
            "to": dates[-1] if dates else None,
            "effective_samples": (adaptive.get("statistics") or {}).get("effective_samples"),
        },
        testing_window=dict(validation.get("validation_window") or {}),
        validation_results={
            "status": adaptive.get("status"),
            "research_gate": adaptive.get("research_gate"),
            "directional_accuracy": (adaptive.get("statistics") or {}).get("directional_accuracy"),
            "mean_signed_excess_return": (adaptive.get("statistics") or {}).get("mean_signed_excess_return"),
            "shadow_only": True,
            "automatic_live_promotion": False,
            "production_baseline_unchanged": True,
        },
        created_at=generated_at,
    )
    updated = append_challenger(registry, manifest)
    updated["generated_at"] = generated_at.isoformat(timespec="seconds")
    return updated


def run(
    *,
    config_path: Path = DEFAULT_CONFIG,
    adaptive_path: Path = DEFAULT_ADAPTIVE_POLICY,
    learning_path: Path = DEFAULT_LEARNING_STATE,
    shadow_path: Path = DEFAULT_SHADOW_LOG,
    validation_path: Path = DEFAULT_VALIDATION,
    registry_path: Path = DEFAULT_REGISTRY,
    market_cache_path: Path = DEFAULT_MARKET_CACHE,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or _now()
    config = _read(config_path)
    market = _read(market_cache_path)
    if not isinstance(config, dict) or not config.get("policy"):
        raise ValueError("BRACE base configuration is missing")
    if not isinstance(market, dict) or not market.get("instruments"):
        raise ValueError("Market cache is missing; run the network refresh first")

    shadow = _read(shadow_path, {"runs": []}) or {"runs": []}
    validation = _read(validation_path, {}) or {}
    registry = _read(registry_path, {}) or {}
    learning = _read(learning_path, _new_learning_state(generated_at)) or _new_learning_state(generated_at)
    learning.setdefault("outcomes", [])
    learning.setdefault("audit", [])

    created = collect_due_outcomes(
        shadow,
        market,
        learning.get("outcomes", []),
        generated_at.date(),
    )
    learning["outcomes"].extend(created)
    stats = learning_statistics(learning["outcomes"])
    learning["statistics"] = stats
    learning["generated_at"] = generated_at.isoformat(timespec="seconds")
    learning["audit"].append({
        "at": generated_at.isoformat(timespec="seconds"),
        "event": "weekly_self_learning_review",
        "new_outcomes": len(created),
        "effective_samples": stats.get("effective_samples"),
    })
    learning["audit"] = learning["audit"][-260:]
    learning["content_sha256"] = canonical_sha256({key: value for key, value in learning.items() if key != "content_sha256"})

    current_adaptive = _read(adaptive_path, {}) or {}
    adaptive = advance_adaptive_policy(current_adaptive, config, stats, validation, generated_at)
    updated_registry = update_registry_with_candidate(
        registry, adaptive, learning, validation, market, generated_at
    ) if registry else registry

    _write(learning_path, learning)
    _write(adaptive_path, adaptive)
    if updated_registry:
        _write(registry_path, updated_registry)

    result = {
        "new_outcomes": len(created),
        "effective_samples": stats.get("effective_samples"),
        "status": adaptive.get("status"),
        "active_overrides": adaptive.get("active_overrides"),
        "candidate_overrides": adaptive.get("candidate_overrides"),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--adaptive-policy", type=Path, default=DEFAULT_ADAPTIVE_POLICY)
    parser.add_argument("--learning-state", type=Path, default=DEFAULT_LEARNING_STATE)
    parser.add_argument("--shadow-log", type=Path, default=DEFAULT_SHADOW_LOG)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--market-cache", type=Path, default=DEFAULT_MARKET_CACHE)
    args = parser.parse_args()
    run(
        config_path=args.config,
        adaptive_path=args.adaptive_policy,
        learning_path=args.learning_state,
        shadow_path=args.shadow_log,
        validation_path=args.validation,
        registry_path=args.registry,
        market_cache_path=args.market_cache,
    )


if __name__ == "__main__":
    main()
