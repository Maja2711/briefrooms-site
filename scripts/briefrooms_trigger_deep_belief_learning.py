#!/usr/bin/env python3
"""Prospective learning for Trigger-directed deep research.

Joins immutable targeted deep-research records to immutable Trigger outcomes by
exact trigger_observation_id. No ticker/time heuristic join is allowed.

The report measures:
- how often expensive research found material evidence,
- how much it changed the research score,
- whether the research update supported or opposed the Trigger direction,
- whether that directional update agreed with the later prospective outcome.

This remains research-only. It cannot promote routing weights or influence
production decisions automatically.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import briefrooms_market_relationship_outcomes as outcomes
    from scripts import briefrooms_trigger_deep_belief as targeted
    from scripts import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:  # pragma: no cover
    import briefrooms_market_relationship_outcomes as outcomes
    import briefrooms_trigger_deep_belief as targeted
    import stock_trading_v2_contracts as contracts

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / "data/investments/market_relationship_deep_belief_history"
OUTCOME_ROOT = ROOT / "data/investments/market_relationship_trigger_outcomes"
REPORT_PATH = ROOT / "data/investments/market_relationship_deep_belief_learning.json"

REPORT_SCHEMA = "briefrooms-trigger-deep-belief-learning-v1"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_historical_research_snapshot(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != targeted.SCHEMA_VERSION:
        raise contracts.ContractError("historical targeted research schema mismatch")
    if payload.get("market") != "US":
        raise contracts.ContractError("historical targeted research market mismatch")
    if payload.get("research_backend") != targeted.BACKEND:
        raise contracts.ContractError("historical targeted research backend mismatch")
    if payload.get("full_belief_core_invocation") is not False:
        raise contracts.ContractError("historical targeted research cannot claim full Belief Core")
    rows = payload.get("targets")
    if not isinstance(rows, list) or int(payload.get("target_count", -1)) != len(rows):
        raise contracts.ContractError("historical targeted research target count mismatch")
    if len(rows) > 2:
        raise contracts.ContractError("historical targeted research exceeded max-two budget")
    for row in rows:
        if not str((row or {}).get("trigger_observation_id") or "").startswith("rel-"):
            raise contracts.ContractError("historical targeted research observation lineage missing")
        if ((row or {}).get("admission") or {}).get("production_decision_influence") is not False:
            raise contracts.ContractError("historical targeted research escaped shadow governance")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("historical targeted research production influence invalid")
    if governance.get("automatic_policy_writeback") is not False:
        raise contracts.ContractError("historical targeted research auto writeback invalid")
    body = dict(payload)
    stored = str(body.pop("snapshot_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("historical targeted research hash mismatch")


def iter_research_snapshots(root: Path = RESEARCH_ROOT) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, Mapping):
            continue
        validate_historical_research_snapshot(payload)
        rows.append(dict(payload))
    return rows


def flatten_research_targets(
    snapshots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate exact research observations by observation ID + methodology."""
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        trigger_version = str(snapshot.get("trigger_config_version") or "unknown")
        evidence_version = str(snapshot.get("evidence_config_version") or "unknown")
        for target_row in snapshot.get("targets") or []:
            if not isinstance(target_row, Mapping):
                continue
            observation_id = str(target_row.get("trigger_observation_id") or "")
            key = (observation_id, trigger_version, evidence_version)
            if not observation_id or key in seen:
                continue
            seen.add(key)
            row = dict(target_row)
            row["research_snapshot_id"] = snapshot.get("snapshot_id")
            row["research_snapshot_sha256"] = snapshot.get("snapshot_sha256")
            row["researched_at"] = snapshot.get("generated_at")
            row["trigger_config_version"] = trigger_version
            row["evidence_config_version"] = evidence_version
            rows.append(row)
    return rows


def _outcome_index(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, int], Mapping[str, Any]]:
    result: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in rows:
        replay = row.get("replay") or {}
        if replay.get("status") != "SETTLED":
            continue
        observation_id = str(row.get("observation_id") or "")
        horizon = int(row.get("horizon_sessions") or 0)
        if observation_id and horizon:
            result[(observation_id, horizon)] = row
    return result


def link_research_to_outcomes(
    research_rows: Sequence[Mapping[str, Any]],
    outcome_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    index = _outcome_index(outcome_rows)
    linked: list[dict[str, Any]] = []
    for research in research_rows:
        observation_id = str(research.get("trigger_observation_id") or "")
        base_score = _finite(research.get("opportunity_score"))
        deep_score = _finite(research.get("deep_opportunity_score"))
        score_delta = (
            deep_score - base_score
            if base_score is not None and deep_score is not None
            else None
        )
        trigger_direction = str(research.get("trigger_direction") or "").upper()
        sign = 1.0 if trigger_direction == "UP" else -1.0 if trigger_direction == "DOWN" else 0.0
        directional_update = score_delta * sign if score_delta is not None and sign else None

        for horizon in outcomes.HORIZONS:
            outcome = index.get((observation_id, horizon))
            if outcome is None:
                continue
            replay = outcome.get("replay") or {}
            continuation = bool(replay.get("continuation"))
            update_class = (
                "SUPPORTS_TRIGGER"
                if directional_update is not None and directional_update > 0.0
                else "OPPOSES_TRIGGER"
                if directional_update is not None and directional_update < 0.0
                else "NEUTRAL_UPDATE"
            )
            agreement = (
                continuation
                if update_class == "SUPPORTS_TRIGGER"
                else (not continuation)
                if update_class == "OPPOSES_TRIGGER"
                else None
            )
            metrics = research.get("evidence_metrics") or {}
            linked.append({
                "trigger_observation_id": observation_id,
                "symbol": research.get("symbol"),
                "horizon_sessions": horizon,
                "research_snapshot_id": research.get("research_snapshot_id"),
                "researched_at": research.get("researched_at"),
                "trigger_type": research.get("trigger_type"),
                "trigger_direction": trigger_direction,
                "evidence_status": research.get("evidence_status"),
                "primary_evidence_items": int(metrics.get("primary_count") or 0),
                "secondary_evidence_items": int(metrics.get("secondary_count") or 0),
                "material_event_present": bool(metrics.get("material_event_present")),
                "score_delta": round(score_delta, 8) if score_delta is not None else None,
                "absolute_score_delta": round(abs(score_delta), 8) if score_delta is not None else None,
                "directional_research_update": (
                    round(directional_update, 8)
                    if directional_update is not None
                    else None
                ),
                "research_update_class": update_class,
                "directional_update_outcome_agreement": agreement,
                "directional_return": replay.get("directional_return"),
                "continuation": continuation,
                "mfe_directional": replay.get("mfe_directional"),
                "mae_directional": replay.get("mae_directional"),
                "terminal_session": replay.get("terminal_session"),
            })
    return linked


def _aggregate_group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    returns = [
        float(row["directional_return"])
        for row in rows
        if _finite(row.get("directional_return")) is not None
    ]
    abs_updates = [
        float(row["absolute_score_delta"])
        for row in rows
        if _finite(row.get("absolute_score_delta")) is not None
    ]
    directional_updates = [
        float(row["directional_research_update"])
        for row in rows
        if _finite(row.get("directional_research_update")) is not None
    ]
    agreements = [
        bool(row["directional_update_outcome_agreement"])
        for row in rows
        if row.get("directional_update_outcome_agreement") is not None
    ]
    symbols = {str(row.get("symbol") or "") for row in rows if row.get("symbol")}
    return {
        "observations": len(rows),
        "unique_symbols": len(symbols),
        "material_event_rate": round(
            sum(bool(row.get("material_event_present")) for row in rows) / len(rows),
            8,
        ) if rows else None,
        "mean_absolute_score_delta": round(statistics.mean(abs_updates), 8) if abs_updates else None,
        "median_absolute_score_delta": round(statistics.median(abs_updates), 8) if abs_updates else None,
        "mean_directional_research_update": (
            round(statistics.mean(directional_updates), 8)
            if directional_updates
            else None
        ),
        "continuation_rate": round(
            sum(bool(row.get("continuation")) for row in rows) / len(rows),
            8,
        ) if rows else None,
        "mean_directional_return": round(statistics.mean(returns), 8) if returns else None,
        "directional_update_outcome_agreement_rate": (
            round(sum(agreements) / len(agreements), 8)
            if agreements
            else None
        ),
        "data_error_rate": round(
            sum(row.get("evidence_status") == "DATA_ERROR" for row in rows) / len(rows),
            8,
        ) if rows else None,
    }


def build_report(
    research_rows: Sequence[Mapping[str, Any]],
    outcome_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    linked = link_research_to_outcomes(research_rows, outcome_rows)
    groups: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in linked:
        groups[(int(row.get("horizon_sessions") or 0), str(row.get("trigger_type") or "UNKNOWN"))].append(row)

    group_rows: list[dict[str, Any]] = []
    for (horizon, trigger_type), rows in sorted(groups.items()):
        group_rows.append({
            "horizon_sessions": horizon,
            "trigger_type": trigger_type,
            **_aggregate_group(rows),
        })

    by_horizon: list[dict[str, Any]] = []
    for horizon in outcomes.HORIZONS:
        rows = [row for row in linked if int(row.get("horizon_sessions") or 0) == horizon]
        by_horizon.append({
            "horizon_sessions": horizon,
            **_aggregate_group(rows),
        })

    linked_observation_ids = {
        str(row.get("trigger_observation_id") or "")
        for row in linked
    }
    all_observation_ids = {
        str(row.get("trigger_observation_id") or "")
        for row in research_rows
        if row.get("trigger_observation_id")
    }

    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _iso_now(),
        "mode": "shadow_prospective_deep_research_learning",
        "research_targets": len(research_rows),
        "research_targets_with_any_settled_outcome": len(linked_observation_ids),
        "pending_research_targets": len(all_observation_ids - linked_observation_ids),
        "linked_outcome_rows": len(linked),
        "by_horizon": by_horizon,
        "groups": group_rows,
        "linked_rows": linked,
        "learning_policy": {
            "exact_observation_id_join_required": True,
            "ticker_time_heuristic_join_forbidden": True,
            "automatic_routing_promotion": False,
            "automatic_policy_writeback": False,
            "minimum_observations_before_hypothesis_review": 30,
            "minimum_unique_symbols_before_hypothesis_review": 8,
            "separate_holdout_required_for_any_routing_challenger": True,
        },
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "automatic_promotion": False,
            "correlation_is_not_causation": True,
            "prospective_only": True,
        },
    }
    payload["report_sha256"] = contracts.payload_sha256(payload)
    validate_report(payload)
    return payload


def validate_report(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != REPORT_SCHEMA:
        raise contracts.ContractError("targeted deep research learning schema mismatch")
    policy = payload.get("learning_policy") or {}
    if policy.get("exact_observation_id_join_required") is not True:
        raise contracts.ContractError("targeted deep research learning lost exact join invariant")
    if policy.get("ticker_time_heuristic_join_forbidden") is not True:
        raise contracts.ContractError("targeted deep research learning enabled heuristic join")
    governance = payload.get("governance") or {}
    for key in ("production_decision_influence", "automatic_policy_writeback", "automatic_promotion"):
        if governance.get(key) is not False:
            raise contracts.ContractError(f"targeted deep research learning governance violation: {key}")
    body = dict(payload)
    stored = str(body.pop("report_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("targeted deep research learning report hash mismatch")


def run(
    *,
    research_root: Path = RESEARCH_ROOT,
    outcome_root: Path = OUTCOME_ROOT,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    snapshots = list(iter_research_snapshots(research_root))
    research_rows = flatten_research_targets(snapshots)
    outcome_rows = list(outcomes.iter_outcomes(outcome_root))
    report = build_report(research_rows, outcome_rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, default=RESEARCH_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=OUTCOME_ROOT)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        payload = _read_json(args.report)
        if not isinstance(payload, Mapping):
            raise SystemExit("targeted deep research learning report unavailable")
        validate_report(payload)
        print(
            "TRIGGER_DEEP_BELIEF_LEARNING_OK",
            payload.get("research_targets"),
            payload.get("linked_outcome_rows"),
        )
        return 0

    payload = run(
        research_root=args.research_root,
        outcome_root=args.outcome_root,
        report_path=args.report,
    )
    print(json.dumps({
        "research_targets": payload.get("research_targets"),
        "linked_outcome_rows": payload.get("linked_outcome_rows"),
        "pending_research_targets": payload.get("pending_research_targets"),
        "production_decision_influence": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
