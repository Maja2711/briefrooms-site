#!/usr/bin/env python3
"""Shared prospective learning loop for the existing EUR/USD A/B/C laboratory.

This module does not create another shadow engine and does not change A/B/C
trading decisions. It converts terminal prospective virtual trade paths from the
existing A/B/C experiment into one canonical LearningEpisode contract, keeps
strictly isolated memory for arms A/B/C, performs deterministic error
attribution and proposes lessons only after repeated evidence.

Policy mutation stays disabled. A single trade can never change an arm policy.
The output is durable private research state consumed by a sanitized public
learning summary and the canonical Experiment Registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

LEARNING_EPISODE_SCHEMA = "briefrooms-learning-episode-v1"
LEARNING_STATE_SCHEMA = "eurusd-abc-learning-state-v1"
LEARNING_REPORT_SCHEMA = "eurusd-abc-learning-report-v1"
STATE_FILENAME = "EURUSD_DAILY_ABC_LEARNING.json"
REPORT_FILENAME = "EURUSD_DAILY_ABC_LEARNING_REPORT.json"
ARMS = ("A", "B", "C")
TERMINAL_ELIGIBLE = {"CLOSED"}
MIN_EPISODES_FOR_LESSON = 8
MIN_LOSSES_FOR_ERROR_LESSON = 4
MIN_DOMINANT_ERROR_RECURRENCE = 0.60
RECENT_WINDOW = 4


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any, digits: int = 6) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def safety_controls() -> dict[str, bool]:
    return {
        "decision_influence": False,
        "automatic_policy_mutation": False,
        "single_trade_policy_mutation": False,
        "cross_arm_writeback": False,
        "belief_writeback": False,
        "production_execution": False,
        "historical_backfill": False,
    }


def _capture_id(capture: Mapping[str, Any]) -> str:
    explicit = str(capture.get("capture_id") or "").strip()
    if explicit:
        return explicit
    seed = "|".join(
        [
            str(capture.get("captured_at") or ""),
            str(capture.get("market_observed_at") or ""),
            str(capture.get("decision_sha256") or ""),
        ]
    )
    return "abc-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _numeric_leaves(value: Any, prefix: str = "", *, limit: int = 40) -> dict[str, float]:
    result: dict[str, float] = {}

    def walk(node: Any, path: str) -> None:
        if len(result) >= limit:
            return
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            result[path or "value"] = round(float(node), 6)
            return
        if isinstance(node, Mapping):
            for key in sorted(node):
                child = f"{path}.{key}" if path else str(key)
                walk(node[key], child)
                if len(result) >= limit:
                    break

    walk(value, prefix)
    return result


def _entry_thesis(capture: Mapping[str, Any], arm_id: str) -> dict[str, Any]:
    arm = ((capture.get("arms") or {}).get(arm_id) or {})
    model_type = str(arm.get("model_type") or arm.get("method") or {
        "A": "TECHNICAL_ONLY",
        "B": "BELIEF_ONLY",
        "C": "HYBRID",
    }[arm_id])
    direction = str(arm.get("direction") or "UNAVAILABLE").upper()
    score = _number(arm.get("score"), 4)
    confidence = _number(arm.get("confidence"), 4)
    diagnostic_source = {}
    for key in ("technical", "belief", "belief_context", "components"):
        if key in arm:
            diagnostic_source[key] = arm.get(key)
    return {
        "schema_version": "eurusd-abc-entry-thesis-v1",
        "arm_id": arm_id,
        "model_type": model_type,
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "component_snapshot": _numeric_leaves(diagnostic_source),
        "market_observed_at": capture.get("market_observed_at"),
        "decision_fingerprint": capture.get("decision_sha256"),
    }


def _risk_bps(plan_arm: Mapping[str, Any]) -> float | None:
    entry = _number(plan_arm.get("entry_price"), 10)
    stop = _number(plan_arm.get("stop_price"), 10)
    if entry is None or stop is None or entry <= 0:
        return None
    value = abs(stop - entry) / entry * 10000.0
    return round(value, 6) if value > 0 else None


def _diagnose(*, outcome_r: float, mfe_r: float, mae_r: float, exit_reason: str) -> dict[str, Any]:
    if outcome_r >= 0:
        primary = "NO_ERROR_OBSERVED"
        evidence = "terminal virtual trade did not lose"
    elif mfe_r < 0.25 and mae_r <= -0.75:
        primary = "DIRECTION_OR_TIMING_FAILURE"
        evidence = "loss with little favorable excursion and large adverse excursion"
    elif mfe_r >= 0.75 and outcome_r < 0:
        primary = "EXIT_CAPTURE_FAILURE"
        evidence = "meaningful favorable excursion was not converted into a non-negative outcome"
    else:
        primary = "FOLLOW_THROUGH_FAILURE"
        evidence = "thesis moved favorably but failed to deliver sufficient follow-through"

    if exit_reason == "STOP_LOSS" and mfe_r < 0.25:
        risk_geometry = "STOP_NOT_OBVIOUSLY_TOO_TIGHT_FROM_PATH"
    elif exit_reason == "STOP_LOSS" and mfe_r >= 0.75:
        risk_geometry = "REVIEW_STOP_OR_PROFIT_PROTECTION"
    else:
        risk_geometry = "NO_STRONG_RISK_GEOMETRY_SIGNAL"

    return {
        "primary_pattern": primary,
        "observed_evidence": evidence,
        "risk_geometry_observation": risk_geometry,
        "causal_claim": False,
        "event_news_attribution": "NOT_AVAILABLE_IN_ABC_TRADE_PATH",
        "data_quality_attribution": "NO_PATH_QUALITY_FAILURE_RECORDED",
    }


def _episode(capture: Mapping[str, Any], arm_id: str) -> dict[str, Any] | None:
    plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), Mapping) else {}
    path = capture.get("trade_path") if isinstance(capture.get("trade_path"), Mapping) else {}
    plan_arm = ((plan.get("arms") or {}).get(arm_id) or {}) if isinstance(plan, Mapping) else {}
    path_arm = ((path.get("arms") or {}).get(arm_id) or {}) if isinstance(path, Mapping) else {}
    status = str(path_arm.get("status") or "").upper()
    direction = str(plan_arm.get("direction") or "").upper()
    if status not in TERMINAL_ELIGIBLE or direction not in {"LONG", "SHORT"}:
        return None
    realized_bps = _number(path_arm.get("realized_bps"), 6)
    mfe_bps = _number(path_arm.get("mfe_bps"), 6)
    mae_bps = _number(path_arm.get("mae_bps"), 6)
    risk_bps = _risk_bps(plan_arm)
    if None in {realized_bps, mfe_bps, mae_bps, risk_bps} or not risk_bps:
        return None
    outcome_r = round(float(realized_bps) / float(risk_bps), 6)
    mfe_r = round(float(mfe_bps) / float(risk_bps), 6)
    mae_r = round(float(mae_bps) / float(risk_bps), 6)
    capture_id = _capture_id(capture)
    episode_id = "abc-learn-" + hashlib.sha256(f"{capture_id}|{arm_id}".encode("utf-8")).hexdigest()[:24]
    exit_reason = str(path_arm.get("exit_reason") or "UNKNOWN")
    return {
        "schema_version": LEARNING_EPISODE_SCHEMA,
        "episode_id": episode_id,
        "engine_id": f"EURUSD_ABC_{arm_id}",
        "arm_id": arm_id,
        "instrument_id": "EUR/USD",
        "capture_id": capture_id,
        "decision_at": capture.get("captured_at") or capture.get("market_observed_at"),
        "resolved_at": path_arm.get("exit_at"),
        "entry_thesis": _entry_thesis(capture, arm_id),
        "market_regime": "UNCLASSIFIED",
        "decision_state": {
            "direction": direction,
            "entry_price": _number(plan_arm.get("entry_price"), 5),
            "stop_price": _number(plan_arm.get("stop_price"), 5),
            "target_price": _number(plan_arm.get("target_price"), 5),
            "risk_bps": risk_bps,
        },
        "outcome": {
            "exit_reason": exit_reason,
            "exit_price": _number(path_arm.get("exit_price"), 5),
            "realized_bps": realized_bps,
            "outcome_r": outcome_r,
            "mfe_bps": mfe_bps,
            "mae_bps": mae_bps,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
        },
        "error_attribution": _diagnose(
            outcome_r=outcome_r, mfe_r=mfe_r, mae_r=mae_r, exit_reason=exit_reason
        ),
        "counterfactuals": {
            "opposite_direction_realized_bps": round(-float(realized_bps), 6),
            "interpretation": "mechanical sign inversion only; not an executable counterfactual trade",
            "costs_included": False,
        },
        "policy_change_proposed": False,
        "policy_change_applied": False,
    }


def initialize_learning_state(experiment_state: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    captures = [row for row in (experiment_state.get("captures") or []) if isinstance(row, Mapping)]
    cutoff = None
    if captures:
        cutoff = max(str(row.get("captured_at") or row.get("market_observed_at") or "") for row in captures)
    activated = now or datetime.now(timezone.utc)
    return {
        "schema_version": LEARNING_STATE_SCHEMA,
        "activated_at": _iso_z(activated),
        "activation_capture_cutoff": cutoff,
        "anti_hindsight": {
            "historical_backfill": False,
            "requires_post_activation_decision": True,
        },
        "authority": safety_controls(),
        "episodes": [],
        "arm_memory": {arm: {"episode_ids": [], "policy_changes_applied": []} for arm in ARMS},
        "updated_at": _iso_z(activated),
    }


def _post_activation(capture: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    decision_at = str(capture.get("captured_at") or capture.get("market_observed_at") or "")
    cutoff = str(state.get("activation_capture_cutoff") or "")
    if not decision_at:
        return False
    if cutoff and _parse(decision_at) <= _parse(cutoff):
        return False
    return _parse(decision_at) >= _parse(state["activated_at"])


def sync_learning(experiment_state: Mapping[str, Any], learning_state: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    state = json.loads(json.dumps(learning_state))
    if state.get("schema_version") != LEARNING_STATE_SCHEMA:
        raise ValueError("unexpected A/B/C learning state schema")
    known = {str(row.get("episode_id")) for row in (state.get("episodes") or []) if isinstance(row, Mapping)}
    appended = 0
    captures = [row for row in (experiment_state.get("captures") or []) if isinstance(row, Mapping)]
    captures.sort(key=lambda row: str(row.get("captured_at") or row.get("market_observed_at") or ""))
    for capture in captures:
        if not _post_activation(capture, state):
            continue
        for arm in ARMS:
            row = _episode(capture, arm)
            if row is None or row["episode_id"] in known:
                continue
            state["episodes"].append(row)
            state["arm_memory"][arm]["episode_ids"].append(row["episode_id"])
            known.add(row["episode_id"])
            appended += 1
    if appended:
        state["updated_at"] = _iso_z(datetime.now(timezone.utc))
    return state, appended


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _proposal_for(pattern: str) -> str:
    return {
        "DIRECTION_OR_TIMING_FAILURE": "test stricter directional confirmation or improved entry timing for this arm",
        "FOLLOW_THROUGH_FAILURE": "test follow-through confirmation before entry; do not change policy automatically",
        "EXIT_CAPTURE_FAILURE": "test profit-protection or exit-management variant on holdout",
    }.get(pattern, "collect more evidence before proposing a policy change")


def arm_metrics(state: Mapping[str, Any], arm_id: str) -> dict[str, Any]:
    episodes = [
        row for row in (state.get("episodes") or [])
        if isinstance(row, Mapping) and row.get("arm_id") == arm_id
    ]
    outcomes = [float((row.get("outcome") or {}).get("outcome_r")) for row in episodes]
    wins = sum(value > 0 for value in outcomes)
    losses = sum(value < 0 for value in outcomes)
    loss_patterns = [
        str((row.get("error_attribution") or {}).get("primary_pattern"))
        for row in episodes if float((row.get("outcome") or {}).get("outcome_r")) < 0
    ]
    counts = Counter(loss_patterns)
    dominant, dominant_count = counts.most_common(1)[0] if counts else (None, 0)
    recurrence = round(dominant_count / losses, 6) if losses else None
    prior = outcomes[:-RECENT_WINDOW] if len(outcomes) > RECENT_WINDOW else []
    recent = outcomes[-RECENT_WINDOW:] if len(outcomes) >= RECENT_WINDOW else []
    delta = None
    if prior and recent:
        delta = round(float(_mean(recent) or 0.0) - float(_mean(prior) or 0.0), 6)
    eligible = (
        len(episodes) >= MIN_EPISODES_FOR_LESSON
        and losses >= MIN_LOSSES_FOR_ERROR_LESSON
        and recurrence is not None
        and recurrence >= MIN_DOMINANT_ERROR_RECURRENCE
        and dominant not in {None, "NO_ERROR_OBSERVED"}
    )
    confidence = None
    if eligible and recurrence is not None:
        sample_factor = min(1.0, len(episodes) / 20.0)
        confidence = round(min(0.95, recurrence * (0.5 + 0.5 * sample_factor)), 4)
    return {
        "episode_count": len(episodes),
        "wins": wins,
        "losses": losses,
        "hit_rate": round(wins / len(outcomes), 6) if outcomes else None,
        "mean_r": _mean(outcomes),
        "mean_mfe_r": _mean([float((row.get("outcome") or {}).get("mfe_r")) for row in episodes]),
        "mean_mae_r": _mean([float((row.get("outcome") or {}).get("mae_r")) for row in episodes]),
        "dominant_error": dominant,
        "error_recurrence_rate": recurrence,
        "recent_vs_prior_mean_r_delta": delta,
        "policy_stability": 1.0,
        "lesson_candidate": {
            "eligible": bool(eligible),
            "error_pattern": dominant if eligible else None,
            "confidence": confidence,
            "proposed_action": _proposal_for(str(dominant)) if eligible else None,
            "policy_change_proposed": bool(eligible),
            "policy_change_applied": False,
        },
    }


def build_report(state: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    metrics = {arm: arm_metrics(state, arm) for arm in ARMS}
    return {
        "schema_version": LEARNING_REPORT_SCHEMA,
        "generated_at": _iso_z(now or datetime.now(timezone.utc)),
        "mode": "research_shadow_learning",
        "shared_contract": LEARNING_EPISODE_SCHEMA,
        "authority": safety_controls(),
        "sample": {
            "episodes": len(state.get("episodes") or []),
            "per_arm": {arm: metrics[arm]["episode_count"] for arm in ARMS},
        },
        "arms": metrics,
        "governance": {
            "minimum_episodes_for_lesson": MIN_EPISODES_FOR_LESSON,
            "minimum_losses_for_error_lesson": MIN_LOSSES_FOR_ERROR_LESSON,
            "minimum_dominant_error_recurrence": MIN_DOMINANT_ERROR_RECURRENCE,
            "single_trade_can_change_policy": False,
            "automatic_policy_mutation": False,
            "cross_arm_learning": False,
            "human_or_promotion_gate_required_before_policy_application": True,
        },
    }


def validate_learning(state: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    if state.get("schema_version") != LEARNING_STATE_SCHEMA:
        raise ValueError("invalid A/B/C learning state schema")
    if report.get("schema_version") != LEARNING_REPORT_SCHEMA:
        raise ValueError("invalid A/B/C learning report schema")
    if any(value is not False for value in safety_controls().values()):
        raise ValueError("learning safety controls must remain zero-authority")
    if state.get("anti_hindsight", {}).get("historical_backfill") is not False:
        raise ValueError("historical backfill must remain disabled")
    seen: set[str] = set()
    per_arm: dict[str, set[str]] = {arm: set() for arm in ARMS}
    for row in state.get("episodes") or []:
        if row.get("schema_version") != LEARNING_EPISODE_SCHEMA:
            raise ValueError("invalid LearningEpisode schema")
        episode_id = str(row.get("episode_id") or "")
        arm = str(row.get("arm_id") or "")
        if not episode_id or episode_id in seen or arm not in ARMS:
            raise ValueError("invalid or duplicate A/B/C learning episode")
        if row.get("policy_change_applied") is not False:
            raise ValueError("automatic policy application is forbidden")
        seen.add(episode_id)
        per_arm[arm].add(episode_id)
    for arm in ARMS:
        memory = set((state.get("arm_memory") or {}).get(arm, {}).get("episode_ids") or [])
        if memory != per_arm[arm]:
            raise ValueError(f"cross-arm or incomplete memory detected for arm {arm}")
        if (report.get("arms") or {}).get(arm, {}).get("lesson_candidate", {}).get("policy_change_applied") is not False:
            raise ValueError("report cannot apply policy changes")


def run_cycle(state_dir: Path, *, now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any], int]:
    experiment = _load(state_dir / "EURUSD_DAILY_ABC_STATE.json")
    if not isinstance(experiment, Mapping):
        raise ValueError("EURUSD_DAILY_ABC_STATE.json is required")
    path = state_dir / STATE_FILENAME
    existing = _load(path)
    if not isinstance(existing, Mapping):
        state = initialize_learning_state(experiment, now=now)
        initialized = 1
    else:
        state = dict(existing)
        initialized = 0
    state, appended = sync_learning(experiment, state)
    report = build_report(state, now=now)
    validate_learning(state, report)
    _atomic(path, state)
    _atomic(state_dir / REPORT_FILENAME, report)
    return state, report, appended + initialized


def main() -> int:
    parser = argparse.ArgumentParser(description="Shared prospective EUR/USD A/B/C learning loop")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    if args.validate:
        state = _load(args.state_dir / STATE_FILENAME)
        report = _load(args.state_dir / REPORT_FILENAME)
        if not isinstance(state, Mapping) or not isinstance(report, Mapping):
            raise SystemExit("A/B/C learning state/report missing")
        validate_learning(state, report)
        print("EURUSD_ABC_LEARNING_OK", len(state.get("episodes") or []))
        return 0
    now = _parse(args.now) if args.now else None
    state, report, changed = run_cycle(args.state_dir, now=now)
    print(f"ABC_LEARNING_STATE_CHANGED={'true' if changed else 'false'}")
    print(f"ABC_LEARNING_EPISODES={len(state.get('episodes') or [])}")
    print("ABC_LEARNING_PER_ARM=" + json.dumps(report["sample"]["per_arm"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
