#!/usr/bin/env python3
"""Fresh-holdout Champion/Challenger evaluation for Stock Trading v2.

Each Challenger is evaluated only on candidate decisions that occurred after its
immutable validation_start_at. The gate is fixed-N and single-look. A statistical
PASS is research eligibility only; production promotion remains disabled.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import stock_trading_v2_admission_ledger as admission
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_experience_store as experience
    from scripts import stock_trading_v2_outcome_replay as outcomes
    from scripts import stock_trading_v2_policy_learner as learner
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_admission_ledger as admission
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_experience_store as experience
    import stock_trading_v2_outcome_replay as outcomes
    import stock_trading_v2_policy_learner as learner

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_challenger_evaluations"
SCHEMA_VERSION = "stock-trading-v2-challenger-evaluation-v1"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _iter_json(root: Path) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    return (_read_json(path) for path in sorted(root.rglob("*.json")))


def _parse_dt(value: Any) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone aware")
    return dt


def _failed_gate_names(event: Mapping[str, Any]) -> list[str]:
    state = event.get("candidate_state") or {}
    path = state.get("decision_path") or {}
    gates = path.get("gates") or []
    names: list[str] = []
    for gate in gates:
        if not isinstance(gate, Mapping) or gate.get("passed") is not False:
            continue
        name = str(gate.get("name") or "").strip()
        if name:
            names.append(name)
    if not names:
        first = path.get("first_blocking_gate") or state.get("first_blocking_gate")
        if isinstance(first, Mapping):
            name = str(first.get("name") or "").strip()
            if name:
                names.append(name)
        elif first:
            names.append(str(first))
    return names


def _producer_component_alias(component: str) -> set[str]:
    aliases = {component}
    if component == "entry_score_below_threshold":
        aliases.update({"score", "entry_score", "minimum_composite_score"})
    if component == "opening_confirmation":
        aliases.update({"opening_confirmation", "opening"})
    return aliases


def component_would_change_cash_to_long(
    event: Mapping[str, Any],
    observation: Mapping[str, Any] | None,
    component: str,
) -> bool:
    """Conservative leave-one-component-out eligibility without outcome peeking."""
    if isinstance(observation, Mapping):
        champion = observation.get("champion") or {}
        if champion.get("action") != "CASH":
            return False
        reason = str(champion.get("reason") or "")
        if event.get("selected") is True and reason == component:
            # Production qualifier is ordered; this exact reason means earlier
            # hard gates passed and this component alone stopped admission.
            return True
    if event.get("selected") is True:
        return False
    failed = _failed_gate_names(event)
    aliases = _producer_component_alias(component)
    return len(failed) == 1 and failed[0] in aliases


def _priority(event: Mapping[str, Any]) -> tuple[int, float, float, str] | None:
    state = event.get("candidate_state") or {}
    score = state.get("score_state") or {}
    rank_value = score.get("rank", score.get("quant_rank"))
    score_value = score.get("score", score.get("composite_score", score.get("legacy_composite_score")))
    try:
        rank = float(rank_value) if rank_value is not None else math.inf
    except (TypeError, ValueError):
        rank = math.inf
    try:
        numeric_score = float(score_value) if score_value is not None else -math.inf
    except (TypeError, ValueError):
        numeric_score = -math.inf
    if not math.isfinite(rank) and not math.isfinite(numeric_score):
        return None
    return (0 if math.isfinite(rank) else 1, rank, -numeric_score, str(event.get("symbol") or ""))


def _outcome_return_percent(row: Mapping[str, Any]) -> float | None:
    replay = row.get("replay") or {}
    if replay.get("status") == "NOT_ACTIVATED":
        return 0.0
    if replay.get("status") != "SETTLED":
        return None
    try:
        return float(replay.get("net_return_percent"))
    except (TypeError, ValueError):
        return None


def collect_samples(
    challenger: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    admission_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    learner.validate_challenger(challenger)
    component = str(challenger["component"])
    horizon = int(challenger["horizon_sessions"])
    start = _parse_dt(challenger["validation_start_at"])

    event_map: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for raw in events:
        event = dict(raw)
        contracts.validate_experience_event(event)
        try:
            decision_dt = _parse_dt(event["decision_at"])
        except Exception:
            continue
        if decision_dt <= start:
            continue
        event_id = str(event["event_id"])
        event_map[event_id] = event
        group = str((event.get("source") or {}).get("payload_sha256") or event_id)
        groups[group].append(event_id)

    admissions: dict[str, dict[str, Any]] = {}
    for raw in admission_rows:
        row = dict(raw)
        admission.validate_observation(row)
        admissions[str(row["source_event_id"])] = row

    horizon_outcomes: dict[str, dict[str, Any]] = {}
    for raw in outcome_rows:
        row = dict(raw)
        outcomes.validate_outcome(row)
        if int(row["horizon_sessions"]) == horizon:
            horizon_outcomes[str(row["source_event_id"])] = row

    samples: list[dict[str, Any]] = []
    for group, ids in groups.items():
        # Gate-admission challengers are measured only when Champion was CASH.
        champion_long = [event_id for event_id in ids if (admissions.get(event_id, {}).get("champion") or {}).get("action") == "LONG"]
        if champion_long:
            continue
        eligible = [
            event_map[event_id]
            for event_id in ids
            if component_would_change_cash_to_long(event_map[event_id], admissions.get(event_id), component)
            and event_id in horizon_outcomes
            and _outcome_return_percent(horizon_outcomes[event_id]) is not None
        ]
        if not eligible:
            continue
        if len(eligible) > 1:
            priorities = [(event, _priority(event)) for event in eligible]
            if any(priority is None for _, priority in priorities):
                # Never use realised outcome to choose among ambiguous candidates.
                continue
            eligible.sort(key=lambda event: _priority(event))
        chosen = eligible[0]
        event_id = str(chosen["event_id"])
        net_percent = _outcome_return_percent(horizon_outcomes[event_id])
        if net_percent is None:
            continue
        samples.append(
            {
                "source_group": group,
                "decision_at": chosen["decision_at"],
                "session_date": chosen["session_date"],
                "symbol": chosen["symbol"],
                "source_event_id": event_id,
                "champion_action": "CASH",
                "challenger_action": "LONG",
                "champion_net_return_percent": 0.0,
                "challenger_net_return_percent": round(net_percent, 8),
                "incremental_net_return_percent": round(net_percent, 8),
            }
        )
    samples.sort(key=lambda row: (str(row["decision_at"]), str(row["source_group"])))
    return samples


def _bootstrap_lower_bound(values: Sequence[float], *, confidence: float, draws: int, seed_text: str) -> float | None:
    if not values or draws <= 0:
        return None
    rng = random.Random(int(contracts.payload_sha256(seed_text)[:16], 16))
    n = len(values)
    means = []
    for _ in range(draws):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.mean(sample))
    means.sort()
    alpha = max(0.0, min(1.0, 1.0 - confidence))
    index = min(len(means) - 1, max(0, int(alpha * len(means))))
    return float(means[index])


def evaluate_samples(challenger: Mapping[str, Any], samples: Sequence[Mapping[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    learner.validate_challenger(challenger)
    holdout = challenger.get("holdout") or {}
    required_n = int(holdout.get("fixed_paired_n") or 30)
    ordered = sorted((dict(row) for row in samples), key=lambda row: (str(row.get("decision_at")), str(row.get("source_group"))))
    formal = ordered[:required_n]
    state = "COLLECTING_FRESH_HOLDOUT"
    metrics: dict[str, Any] = {"paired_n": len(formal), "required_n": required_n}

    if len(formal) >= required_n:
        values = [float(row["incremental_net_return_percent"]) for row in formal]
        positive = [max(0.0, value) for value in values]
        positive_total = sum(positive)
        unique_symbols = len({str(row["symbol"]) for row in formal})
        dates = sorted(datetime.fromisoformat(str(row["decision_at"]).replace("Z", "+00:00")).date() for row in formal)
        span_days = (dates[-1] - dates[0]).days if dates else 0
        positive_rate = sum(value > 0 for value in values) / len(values)
        mean_incremental = statistics.mean(values)
        lower = _bootstrap_lower_bound(
            values,
            confidence=float(holdout.get("confidence_level") or 0.9),
            draws=int(holdout.get("bootstrap_samples") or 4000),
            seed_text=str(challenger["challenger_id"]),
        )
        concentration = max(positive) / positive_total if positive_total > 0 else 1.0
        checks = {
            "mean_incremental": mean_incremental >= float(holdout.get("minimum_net_incremental_return_percent") or 0.0),
            "positive_rate": positive_rate >= float(holdout.get("minimum_net_positive_rate") or 0.55),
            "unique_symbols": unique_symbols >= int(holdout.get("minimum_unique_symbols") or 5),
            "span_days": span_days >= int(holdout.get("minimum_span_days") or 10),
            "concentration": concentration <= float(holdout.get("maximum_single_positive_contribution_share") or 0.5),
            "bootstrap_lower_bound_positive": lower is not None and lower > 0.0,
        }
        passed = all(checks.values())
        state = "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT" if passed else "FORMAL_HOLDOUT_FAIL"
        metrics.update(
            {
                "mean_incremental_net_return_percent": round(mean_incremental, 8),
                "median_incremental_net_return_percent": round(statistics.median(values), 8),
                "positive_rate": round(positive_rate, 8),
                "unique_symbols": unique_symbols,
                "span_days": span_days,
                "maximum_single_positive_contribution_share": round(concentration, 8),
                "bootstrap_lower_bound_percent": round(float(lower), 8) if lower is not None else None,
                "checks": checks,
                "formal_pass": passed,
            }
        )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "challenger_id": challenger["challenger_id"],
        "component": challenger["component"],
        "horizon_sessions": challenger["horizon_sessions"],
        "validation_start_at": challenger["validation_start_at"],
        "generated_at": generated_at or contracts.iso_utc(),
        "state": state,
        "metrics": metrics,
        "formal_samples": formal,
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "production_promotion_enabled": False,
            "single_formal_look": True,
        },
    }
    body = dict(payload)
    payload["evaluation_sha256"] = contracts.payload_sha256(body)
    validate_evaluation(payload)
    return payload


def validate_evaluation(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("challenger evaluation schema mismatch")
    if payload.get("state") not in {"COLLECTING_FRESH_HOLDOUT", "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT", "FORMAL_HOLDOUT_FAIL"}:
        raise contracts.ContractError("challenger evaluation state invalid")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False or governance.get("production_promotion_enabled") is not False:
        raise contracts.ContractError("challenger evaluation escaped research governance")
    body = dict(payload)
    stored = str(body.pop("evaluation_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("challenger evaluation hash mismatch")


def run_all(
    *,
    challenger_root: Path = learner.DEFAULT_ROOT,
    experience_root: Path = experience.DEFAULT_STORE_ROOT,
    admission_root: Path = admission.DEFAULT_ROOT,
    outcome_root: Path = outcomes.DEFAULT_ROOT,
    output_root: Path = DEFAULT_ROOT,
) -> dict[str, Any]:
    events = list(_iter_json(experience_root))
    admissions = list(_iter_json(admission_root))
    outcome_rows = list(_iter_json(outcome_root))
    output_root.mkdir(parents=True, exist_ok=True)
    states: dict[str, int] = defaultdict(int)
    count = 0
    for challenger_path in sorted(challenger_root.glob("*.json")) if challenger_root.exists() else []:
        challenger = _read_json(challenger_path)
        learner.validate_challenger(challenger)
        samples = collect_samples(challenger, events, admissions, outcome_rows)
        report = evaluate_samples(challenger, samples)
        path = output_root / f"{challenger['challenger_id']}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        states[str(report["state"])] += 1
        count += 1
    return {"schema_version": "stock-trading-v2-challenger-eval-run-v1", "challengers": count, "states": dict(states), "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--challengers", type=Path, default=learner.DEFAULT_ROOT)
    parser.add_argument("--experience-root", type=Path, default=experience.DEFAULT_STORE_ROOT)
    parser.add_argument("--admission-root", type=Path, default=admission.DEFAULT_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=outcomes.DEFAULT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    result = run_all(
        challenger_root=args.challengers,
        experience_root=args.experience_root,
        admission_root=args.admission_root,
        outcome_root=args.outcome_root,
        output_root=args.output_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
