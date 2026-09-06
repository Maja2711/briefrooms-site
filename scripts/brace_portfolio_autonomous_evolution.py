#!/usr/bin/env python3
"""Governed autonomous evolution of BRACE portfolio construction.

The loop may autonomously change portfolio-construction parameters after repeated
prospective evidence. It can never modify the immutable Safety Kernel or grant
real-broker execution authority.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, Mapping

from brace_portfolio_config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH,
    IMMUTABLE_SAFETY_FIELDS,
    PORTFOLIO_EVOLUTION_FIELDS,
    EngineConfig,
    load_config,
)
from brace_portfolio_counterfactual_lab import (
    canonical_sha256,
    freeze_counterfactual_snapshot,
    settle_counterfactual_snapshot,
)
from brace_portfolio_learning_attribution import attribute_portfolio_outcome

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "portfolio10k"
ANALYSIS_PATH = DATA_ROOT / "analysis.json"
EVOLUTION_ROOT = DATA_ROOT / "portfolio_evolution"
SNAPSHOTS_PATH = EVOLUTION_ROOT / "counterfactual_snapshots.jsonl"
OUTCOMES_PATH = EVOLUTION_ROOT / "outcomes.jsonl"
ATTRIBUTIONS_PATH = EVOLUTION_ROOT / "attributions.jsonl"
EVENTS_PATH = EVOLUTION_ROOT / "events.jsonl"
STATE_PATH = EVOLUTION_ROOT / "state.json"
DEFAULT_MARKET_CACHE_PATH = ROOT / ".cache" / "brace_portfolio_market.json"
HORIZONS = (7, 30, 90)
MINIMUM_ECONOMIC_SAMPLES = 8
REQUIRED_CONFIRMATIONS = 2
ROLLBACK_MINIMUM_POST_ACTIVATION_SAMPLES = 4
MATERIAL_REGRET = 0.002
MUTATION_STEPS = {
    "portfolio_risk_aversion": 0.05,
    "portfolio_drawdown_penalty": 0.05,
    "portfolio_turnover_penalty": 0.05,
    "portfolio_diversification_penalty": 0.05,
    "portfolio_cash_floor": 0.05,
    "portfolio_rebalance_threshold": 0.005,
    "portfolio_max_active_positions": 1.0,
}


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_jsonl(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _base_config_hash() -> str:
    return canonical_sha256(json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))


def _now(value: str | None = None) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _policy_with_hash(payload: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(payload)
    result.pop("content_sha256", None)
    result["content_sha256"] = canonical_sha256(result)
    return result


def _default_policy(now: datetime) -> Dict[str, Any]:
    payload = {
        "schema_version": "brace-portfolio-evolution-policy-v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "status": "WARMUP",
        "apply_to_engine_portfolio_policy": True,
        "never_apply_to_real_broker": True,
        "base_config_sha256": _base_config_hash(),
        "active_overrides": {},
        "candidate_overrides": {},
        "candidate_signature": None,
        "consecutive_confirmations": 0,
        "required_confirmations": REQUIRED_CONFIRMATIONS,
        "minimum_economic_samples": MINIMUM_ECONOMIC_SAMPLES,
        "learning_reason": "WARMUP_INSUFFICIENT_PROSPECTIVE_PORTFOLIO_OUTCOMES",
        "bounds": {key: list(value) for key, value in PORTFOLIO_EVOLUTION_FIELDS.items()},
        "immutable_safety_fields": sorted(IMMUTABLE_SAFETY_FIELDS),
        "safety": {
            "paper_shadow_authority_only": True,
            "real_broker_integration": False,
            "trade_execution_authority": False,
            "safety_kernel_mutable_by_learner": False,
        },
        "statistics": {},
    }
    return _policy_with_hash(payload)


def _validate_policy(policy: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != "brace-portfolio-evolution-policy-v1":
        raise ValueError("Unexpected portfolio evolution policy schema")
    if policy.get("never_apply_to_real_broker") is not True:
        raise ValueError("Portfolio evolution policy must prohibit real-broker use")
    if (policy.get("safety") or {}).get("trade_execution_authority") is not False:
        raise ValueError("Portfolio evolution must not gain trade execution authority")
    overrides = policy.get("active_overrides") or {}
    candidates = policy.get("candidate_overrides") or {}
    for values in (overrides, candidates):
        unknown = set(values) - set(PORTFOLIO_EVOLUTION_FIELDS)
        if unknown:
            raise ValueError(f"Non-whitelisted portfolio mutation: {sorted(unknown)}")
        if set(values) & IMMUTABLE_SAFETY_FIELDS:
            raise ValueError("Portfolio learner attempted to mutate Safety Kernel")
        for key, raw in values.items():
            low, high = PORTFOLIO_EVOLUTION_FIELDS[key]
            value = float(raw)
            if not low <= value <= high:
                raise ValueError(f"Portfolio mutation {key} outside governed bounds")
    expected = str(policy.get("content_sha256") or "")
    if expected:
        payload = dict(policy)
        payload.pop("content_sha256", None)
        if expected != canonical_sha256(payload):
            raise ValueError("Portfolio evolution policy hash mismatch")


def _capture_current_snapshot(config: EngineConfig, now: datetime) -> Dict[str, Any] | None:
    analysis = read_json(ANALYSIS_PATH)
    if not (analysis.get("optimization") or {}).get("comparisons"):
        return None
    snapshot = freeze_counterfactual_snapshot(analysis, config, generated_at=None)
    existing = {row.get("snapshot_id") for row in read_jsonl(SNAPSHOTS_PATH)}
    if snapshot["snapshot_id"] not in existing:
        append_jsonl(SNAPSHOTS_PATH, snapshot)
        append_jsonl(
            EVENTS_PATH,
            {
                "event": "COUNTERFACTUAL_SNAPSHOT_FROZEN",
                "at": now.isoformat(timespec="seconds"),
                "snapshot_id": snapshot["snapshot_id"],
                "prospective_only": True,
            },
        )
        return snapshot
    return None


def _settle_mature_snapshots(market: Mapping[str, Any], now: datetime) -> int:
    snapshots = read_jsonl(SNAPSHOTS_PATH)
    existing_outcomes = {row.get("outcome_id") for row in read_jsonl(OUTCOMES_PATH)}
    written = 0
    for snapshot in snapshots:
        for horizon in HORIZONS:
            outcome = settle_counterfactual_snapshot(snapshot, market, horizon, as_of=now)
            if not outcome or outcome.get("outcome_id") in existing_outcomes:
                continue
            attribution = attribute_portfolio_outcome(outcome)
            append_jsonl(OUTCOMES_PATH, outcome)
            append_jsonl(ATTRIBUTIONS_PATH, attribution)
            append_jsonl(
                EVENTS_PATH,
                {
                    "event": "COUNTERFACTUAL_OUTCOME_SETTLED",
                    "at": now.isoformat(timespec="seconds"),
                    "outcome_id": outcome["outcome_id"],
                    "snapshot_id": outcome["snapshot_id"],
                    "horizon_days": horizon,
                    "winner": outcome["winner"],
                    "regret": outcome["regret"],
                },
            )
            existing_outcomes.add(outcome["outcome_id"])
            written += 1
    return written


def _latest_outcome_per_snapshot(outcomes: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in outcomes:
        snapshot_id = str(row.get("snapshot_id") or "")
        if not snapshot_id:
            continue
        current = latest.get(snapshot_id)
        if current is None or int(row.get("horizon_days") or 0) > int(current.get("horizon_days") or 0):
            latest[snapshot_id] = dict(row)
    return sorted(latest.values(), key=lambda row: (str(row.get("generated_at") or ""), str(row.get("snapshot_id") or "")))


def _statistics(latest: list[Mapping[str, Any]]) -> Dict[str, Any]:
    if not latest:
        return {
            "economic_samples": 0,
            "mean_regret": None,
            "selected_vs_current_mean": None,
            "selected_beats_current_rate": None,
            "winner_frequencies": {},
        }
    vs_current = [
        float(row["selected_vs_current"])
        for row in latest
        if row.get("selected_vs_current") is not None
    ]
    frequencies: Dict[str, int] = defaultdict(int)
    for row in latest:
        frequencies[str(row.get("winner") or "UNKNOWN")] += 1
    return {
        "economic_samples": len(latest),
        "mean_regret": round(mean(float(row.get("regret") or 0.0) for row in latest), 10),
        "selected_vs_current_mean": round(mean(vs_current), 10) if vs_current else None,
        "selected_beats_current_rate": (
            round(sum(1 for value in vs_current if value > 0) / len(vs_current), 6)
            if vs_current
            else None
        ),
        "winner_frequencies": dict(sorted(frequencies.items())),
        "longest_horizon_days": max(int(row.get("horizon_days") or 0) for row in latest),
        "sample_semantics": "one economic portfolio decision contributes one latest matured horizon",
    }


def _attribution_votes(latest: list[Mapping[str, Any]]) -> Dict[tuple[str, str], float]:
    latest_ids = {str(row.get("outcome_id") or "") for row in latest}
    votes: Dict[tuple[str, str], float] = defaultdict(float)
    for attribution in read_jsonl(ATTRIBUTIONS_PATH):
        if str(attribution.get("outcome_id") or "") not in latest_ids:
            continue
        if not attribution.get("material_regret"):
            continue
        primary = attribution.get("primary_attribution") or {}
        parameter = str(primary.get("parameter") or "")
        direction = str(primary.get("direction") or "")
        if parameter in PORTFOLIO_EVOLUTION_FIELDS and direction in {"UP", "DOWN"}:
            votes[(parameter, direction)] += max(0.05, float(primary.get("strength") or 0.0))
    return dict(votes)


def _bounded_value(parameter: str, value: float) -> float | int:
    low, high = PORTFOLIO_EVOLUTION_FIELDS[parameter]
    bounded = min(high, max(low, value))
    if parameter == "portfolio_max_active_positions":
        return int(round(bounded))
    return round(bounded, 6)


def propose_mutation(
    config: EngineConfig,
    latest: list[Mapping[str, Any]],
) -> Dict[str, Any] | None:
    if len(latest) < MINIMUM_ECONOMIC_SAMPLES:
        return None
    stats = _statistics(latest)
    if float(stats.get("mean_regret") or 0.0) < MATERIAL_REGRET:
        return None
    votes = _attribution_votes(latest)
    if not votes:
        return None
    (parameter, direction), score = max(
        votes.items(), key=lambda item: (item[1], item[0][0], item[0][1])
    )
    current = float(getattr(config, parameter))
    step = MUTATION_STEPS[parameter] * (1.0 if direction == "UP" else -1.0)
    candidate = _bounded_value(parameter, current + step)
    if float(candidate) == current:
        return None
    if parameter == "portfolio_max_active_positions":
        candidate = min(int(candidate), config.max_positions)
    return {
        "parameter": parameter,
        "direction": direction,
        "from": current if parameter != "portfolio_max_active_positions" else int(round(current)),
        "to": candidate,
        "vote_strength": round(score, 6),
        "evidence_samples": len(latest),
        "mean_regret": stats["mean_regret"],
    }


def _post_activation_degradation(
    latest: list[Mapping[str, Any]], state: Mapping[str, Any]
) -> tuple[bool, Dict[str, Any]]:
    activated_at = str(state.get("activated_at") or "")
    if not activated_at:
        return False, {}
    post = [row for row in latest if str(row.get("generated_at") or "") >= activated_at]
    values = [
        float(row["selected_vs_current"])
        for row in post
        if row.get("selected_vs_current") is not None
    ]
    if len(values) < ROLLBACK_MINIMUM_POST_ACTIVATION_SAMPLES:
        return False, {"post_activation_samples": len(values)}
    trailing = values[-ROLLBACK_MINIMUM_POST_ACTIVATION_SAMPLES:]
    average = mean(trailing)
    return average < -0.01, {
        "post_activation_samples": len(values),
        "trailing_selected_vs_current_mean": round(average, 10),
    }


def _evolve_policy(
    config: EngineConfig,
    latest: list[Mapping[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    policy = (
        read_json(DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH)
        if DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH.exists()
        else _default_policy(now)
    )
    _validate_policy(policy)
    state = read_json(STATE_PATH)
    stats = _statistics(latest)
    policy["statistics"] = stats
    policy["generated_at"] = now.isoformat(timespec="seconds")
    policy["base_config_sha256"] = _base_config_hash()

    degraded, degradation = _post_activation_degradation(latest, state)
    if degraded and policy.get("active_overrides"):
        previous = dict(state.get("previous_active_overrides") or {})
        rolled_back = dict(policy.get("active_overrides") or {})
        policy["active_overrides"] = previous
        policy["candidate_overrides"] = {}
        policy["candidate_signature"] = None
        policy["consecutive_confirmations"] = 0
        policy["status"] = "AUTONOMOUS_ROLLBACK"
        policy["learning_reason"] = "POST_ACTIVATION_UNDERPERFORMANCE_VS_CURRENT_PORTFOLIO"
        state.update(
            {
                "last_rollback_at": now.isoformat(timespec="seconds"),
                "rolled_back_overrides": rolled_back,
                "candidate_signature": None,
                "consecutive_confirmations": 0,
            }
        )
        append_jsonl(
            EVENTS_PATH,
            {
                "event": "AUTONOMOUS_PORTFOLIO_POLICY_ROLLBACK",
                "at": now.isoformat(timespec="seconds"),
                "rolled_back_overrides": rolled_back,
                "restored_overrides": previous,
                "evidence": degradation,
            },
        )
        write_json_atomic(STATE_PATH, state)
        policy = _policy_with_hash(policy)
        _validate_policy(policy)
        return policy

    last_evidence_samples = int(state.get("last_evidence_samples", -1))
    if DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH.exists() and last_evidence_samples == len(latest):
        policy["learning_reason"] = "NO_NEW_MATURE_PROSPECTIVE_PORTFOLIO_OUTCOMES"
        state["updated_at"] = now.isoformat(timespec="seconds")
        state["statistics"] = stats
        state["degradation_check"] = degradation
        write_json_atomic(STATE_PATH, state)
        policy = _policy_with_hash(policy)
        _validate_policy(policy)
        return policy

    mutation = propose_mutation(config, latest)
    if mutation is None:
        policy["candidate_overrides"] = {}
        policy["candidate_signature"] = None
        policy["consecutive_confirmations"] = 0
        policy["status"] = (
            "ACTIVE_PORTFOLIO_POLICY" if policy.get("active_overrides") else "WARMUP"
        )
        policy["learning_reason"] = (
            "NO_MATERIAL_BOUNDED_MUTATION_SUPPORTED_BY_PROSPECTIVE_EVIDENCE"
            if len(latest) >= MINIMUM_ECONOMIC_SAMPLES
            else "WARMUP_INSUFFICIENT_PROSPECTIVE_PORTFOLIO_OUTCOMES"
        )
        state["candidate_signature"] = None
        state["consecutive_confirmations"] = 0
        state["last_evidence_samples"] = len(latest)
    else:
        candidate_overrides = {mutation["parameter"]: mutation["to"]}
        signature = canonical_sha256(candidate_overrides)
        previous_signature = str(state.get("candidate_signature") or "")
        previous_samples = int(state.get("last_evidence_samples") or 0)
        confirmations = int(state.get("consecutive_confirmations") or 0)
        if signature == previous_signature and len(latest) > previous_samples:
            confirmations += 1
        elif signature != previous_signature:
            confirmations = 1
        policy["candidate_overrides"] = candidate_overrides
        policy["candidate_signature"] = signature
        policy["consecutive_confirmations"] = confirmations
        policy["status"] = "PORTFOLIO_CHALLENGER"
        policy["learning_reason"] = "REPEATED_PROSPECTIVE_COUNTERFACTUAL_REGRET"
        state.update(
            {
                "candidate_signature": signature,
                "consecutive_confirmations": confirmations,
                "last_evidence_samples": len(latest),
                "last_mutation": mutation,
            }
        )
        append_jsonl(
            EVENTS_PATH,
            {
                "event": "PORTFOLIO_MUTATION_CANDIDATE",
                "at": now.isoformat(timespec="seconds"),
                "mutation": mutation,
                "candidate_signature": signature,
                "confirmations": confirmations,
            },
        )
        if confirmations >= REQUIRED_CONFIRMATIONS:
            previous_active = dict(policy.get("active_overrides") or {})
            active = dict(previous_active)
            active.update(candidate_overrides)
            policy["active_overrides"] = active
            policy["candidate_overrides"] = {}
            policy["candidate_signature"] = None
            policy["consecutive_confirmations"] = 0
            policy["status"] = "ACTIVE_PORTFOLIO_POLICY"
            policy["learning_reason"] = "AUTONOMOUS_PROMOTION_AFTER_REPEATED_PROSPECTIVE_EVIDENCE"
            state.update(
                {
                    "previous_active_overrides": previous_active,
                    "activated_at": now.isoformat(timespec="seconds"),
                    "activated_overrides": candidate_overrides,
                    "candidate_signature": None,
                    "consecutive_confirmations": 0,
                }
            )
            append_jsonl(
                EVENTS_PATH,
                {
                    "event": "AUTONOMOUS_PORTFOLIO_POLICY_PROMOTION",
                    "at": now.isoformat(timespec="seconds"),
                    "active_overrides": active,
                    "evidence_samples": len(latest),
                    "real_broker_integration": False,
                },
            )

    state["updated_at"] = now.isoformat(timespec="seconds")
    state["statistics"] = stats
    state["degradation_check"] = degradation
    write_json_atomic(STATE_PATH, state)
    policy = _policy_with_hash(policy)
    _validate_policy(policy)
    return policy


def run(*, market_cache_path: Path, now: datetime) -> Dict[str, Any]:
    EVOLUTION_ROOT.mkdir(parents=True, exist_ok=True)
    config, _ = load_config()
    _capture_current_snapshot(config, now)
    market = read_json(market_cache_path)
    settled = _settle_mature_snapshots(market, now) if market.get("instruments") else 0
    latest = _latest_outcome_per_snapshot(read_jsonl(OUTCOMES_PATH))
    policy = _evolve_policy(config, latest, now)
    write_json_atomic(DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH, policy)
    return {
        "status": policy["status"],
        "settled_outcomes": settled,
        "economic_samples": len(latest),
        "active_overrides": policy.get("active_overrides") or {},
        "candidate_overrides": policy.get("candidate_overrides") or {},
        "real_broker_integration": False,
    }


def verify() -> Dict[str, Any]:
    policy = read_json(DEFAULT_PORTFOLIO_EVOLUTION_POLICY_PATH)
    if not policy:
        return {"status": "NOT_CONFIGURED", "verified": True}
    _validate_policy(policy)
    for snapshot in read_jsonl(SNAPSHOTS_PATH):
        if snapshot.get("prospective_only") is not True:
            raise ValueError("Non-prospective snapshot found in portfolio evolution ledger")
        if snapshot.get("real_broker_integration") is not False:
            raise ValueError("Counterfactual ledger attempted real-broker integration")
    return {
        "status": policy.get("status"),
        "verified": True,
        "active_overrides": policy.get("active_overrides") or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-cache", type=Path, default=DEFAULT_MARKET_CACHE_PATH)
    parser.add_argument("--now", default=None)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify() if args.verify else run(market_cache_path=args.market_cache, now=_now(args.now))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
