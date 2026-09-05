#!/usr/bin/env python3
"""PR506 — Rejection Component Value Attribution v1 for GPW Daily Stock.

This is a derived, observational research layer over the prospective rejected-
candidate outcome store. It does not re-run the selector and it does not change
ranking, gates, risk, execution or learning.

Primary attribution is deliberately conservative: one rejected candidate is
assigned to exactly one component — its prospectively frozen
``first_blocking_gate``. This prevents double counting. The layer then observes
the candidate's T+2 outcome:

* negative rejected return -> avoided loss (valuable rejection),
* positive rejected return -> missed upside (missed winner),
* zero -> neutral rejection.

For candidates that were legal alternatives (no failed hard gate), the layer
also compares the rejected T+2 outcome with the actually selected T+2 outcome.
That selected-relative metric is an ex-post diagnostic, not a causal claim that
removing the component would necessarily have selected that candidate.

Hard-gate value is measured against the flat/no-trade reference only. True
marginal causal value still requires a later leave-one-component-out replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from scripts import gpw_daily_pick as gpw
    from scripts import gpw_rejected_candidate_outcomes as outcomes
except ModuleNotFoundError:  # pragma: no cover - direct execution from scripts/
    import gpw_daily_pick as gpw
    import gpw_rejected_candidate_outcomes as outcomes

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = outcomes.STORE_DIR
OUTPUT_PATH = STORE_DIR / "component_value_attribution.json"
SCHEMA_VERSION = "gpw-rejection-component-value-attribution-v1"
RECENT_OBSERVATION_LIMIT = 250
EPSILON = 1e-12


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    temporary.replace(path)


def _horizon(row: Mapping[str, Any], number: int = 2) -> Optional[Mapping[str, Any]]:
    for item in row.get("horizons") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            horizon = int(item.get("horizon_sessions") or 0)
        except (TypeError, ValueError):
            continue
        if horizon == number:
            return item
    return None


def _component(candidate: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    gate = candidate.get("first_blocking_gate")
    if not isinstance(gate, Mapping):
        return None
    name = str(gate.get("name") or "").strip()
    if not name:
        return None
    return {
        "name": name,
        "stage": str(gate.get("stage") or "unknown"),
        "hard": bool(gate.get("hard") is True),
        "reason": str(gate.get("reason") or ""),
    }


def _selected_t2(settlement: Mapping[str, Any]) -> Optional[float]:
    selected = settlement.get("selected")
    if not isinstance(selected, Mapping):
        return None
    horizon = _horizon(selected, 2)
    if not isinstance(horizon, Mapping) or horizon.get("status") != "RESOLVED":
        return None
    return _finite(horizon.get("net_return_percent"))


def _classification(rejected_return: float) -> str:
    if rejected_return < -EPSILON:
        return "VALUABLE_REJECTION"
    if rejected_return > EPSILON:
        return "MISSED_WINNER"
    return "NEUTRAL_REJECTION"


def observation_from_candidate(
    *,
    record: Mapping[str, Any],
    candidate: Mapping[str, Any],
    selected_t2_return: Optional[float],
) -> Optional[dict[str, Any]]:
    """Create one primary-attribution observation or return None if not evaluable.

    Non-evaluable candidates are not evidence for component value. They remain
    visible in the underlying prospective outcome record and in coverage counts.
    """
    if candidate.get("economically_evaluable") is not True:
        return None
    component = _component(candidate)
    if component is None:
        return None
    horizon = _horizon(candidate, 2)
    status = str((horizon or {}).get("status") or candidate.get("status") or "UNKNOWN")
    observation: dict[str, Any] = {
        "observation_id": f"{record.get('decision_date')}:{candidate.get('candidate_id') or candidate.get('symbol')}:{component['name']}",
        "decision_date": record.get("decision_date"),
        "candidate_id": candidate.get("candidate_id"),
        "symbol": candidate.get("symbol"),
        "component": component,
        "primary_attribution": "first_blocking_gate",
        "double_counted": False,
        "hard_blocked": bool(candidate.get("hard_blocked") is True),
        "opportunity_candidate": bool(candidate.get("opportunity_candidate") is True),
        "t2_status": status,
        "source_snapshot_sha256": record.get("source_snapshot_sha256"),
        "causal_claim": False,
    }
    if not isinstance(horizon, Mapping) or horizon.get("status") != "RESOLVED":
        observation["value_status"] = "NOT_RESOLVED"
        observation["reason"] = (horizon or {}).get("reason") or candidate.get("reason") or status
        return observation
    rejected_return = _finite(horizon.get("net_return_percent"))
    if rejected_return is None:
        observation.update({"value_status": "DATA_GAP", "reason": "resolved_t2_missing_net_return"})
        return observation

    avoided = max(-rejected_return, 0.0)
    missed = max(rejected_return, 0.0)
    signed = avoided - missed  # equivalent to -rejected_return; positive means rejection helped vs flat.
    observation.update(
        {
            "value_status": "RESOLVED",
            "classification": _classification(rejected_return),
            "rejected_t2_return_percent": round(rejected_return, 6),
            "rejected_t2_r_multiple": _finite(horizon.get("r_multiple")),
            "avoided_loss_percent": round(avoided, 6),
            "missed_upside_percent": round(missed, 6),
            "signed_rejection_value_vs_flat_percent": round(signed, 6),
            "reference_for_gate_value": "FLAT_ZERO_RETURN",
        }
    )

    # Only legal alternatives can be compared with the actually selected trade
    # as an ex-post opportunity-cost diagnostic. Hard-gate rejects stay outside
    # this production opportunity set even when their market outcome is known.
    if candidate.get("opportunity_candidate") is True and selected_t2_return is not None:
        delta = rejected_return - selected_t2_return
        observation.update(
            {
                "selected_t2_return_percent": round(selected_t2_return, 6),
                "candidate_minus_selected_percent": round(delta, 6),
                "selected_relative_opportunity_cost_percent": round(max(delta, 0.0), 6),
                "selected_relative_selection_advantage_percent": round(max(-delta, 0.0), 6),
                "selected_relative_comparable": True,
            }
        )
    else:
        observation["selected_relative_comparable"] = False
    return observation


def _evidence_status(resolved_count: int) -> str:
    if resolved_count < 20:
        return "OBSERVING"
    if resolved_count < 50:
        return "EARLY_SIGNAL"
    return "EVALUABLE"


def _sum(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    return sum(float(value) for row in rows if (value := _finite(row.get(field))) is not None)


def _mean(rows: Sequence[Mapping[str, Any]], field: str) -> Optional[float]:
    values = [value for row in rows if (value := _finite(row.get(field))) is not None]
    return statistics.fmean(values) if values else None


def aggregate_component(name: str, observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [row for row in observations if ((row.get("component") or {}).get("name") if isinstance(row.get("component"), Mapping) else None) == name]
    resolved = [row for row in rows if row.get("value_status") == "RESOLVED"]
    valuable = [row for row in resolved if row.get("classification") == "VALUABLE_REJECTION"]
    missed = [row for row in resolved if row.get("classification") == "MISSED_WINNER"]
    neutral = [row for row in resolved if row.get("classification") == "NEUTRAL_REJECTION"]
    comparable = [row for row in resolved if row.get("selected_relative_comparable") is True]
    avoided_sum = _sum(resolved, "avoided_loss_percent")
    missed_sum = _sum(resolved, "missed_upside_percent")
    selected_opp_sum = _sum(comparable, "selected_relative_opportunity_cost_percent")
    selected_adv_sum = _sum(comparable, "selected_relative_selection_advantage_percent")
    stages = sorted({str((row.get("component") or {}).get("stage") or "unknown") for row in rows})
    hard_flags = {bool((row.get("component") or {}).get("hard") is True) for row in rows}
    return {
        "component": name,
        "stages": stages,
        "gate_type": "HARD" if hard_flags == {True} else ("SOFT" if hard_flags == {False} else "MIXED"),
        "evaluable_rejection_count": len(rows),
        "resolved_t2_count": len(resolved),
        "pending_or_data_gap_count": len(rows) - len(resolved),
        "hard_blocked_count": sum(1 for row in rows if row.get("hard_blocked") is True),
        "legal_opportunity_candidate_count": sum(1 for row in rows if row.get("opportunity_candidate") is True),
        "valuable_rejection_count": len(valuable),
        "missed_winner_count": len(missed),
        "neutral_rejection_count": len(neutral),
        "valuable_rejection_rate": round(len(valuable) / len(resolved), 6) if resolved else None,
        "missed_winner_rate": round(len(missed) / len(resolved), 6) if resolved else None,
        "avoided_loss_percent_sum": round(avoided_sum, 6),
        "missed_upside_percent_sum": round(missed_sum, 6),
        "net_rejection_value_vs_flat_percent": round(avoided_sum - missed_sum, 6),
        "mean_rejected_t2_return_percent": round(value, 6) if (value := _mean(resolved, "rejected_t2_return_percent")) is not None else None,
        "mean_signed_rejection_value_vs_flat_percent": round(value, 6) if (value := _mean(resolved, "signed_rejection_value_vs_flat_percent")) is not None else None,
        "selected_relative_comparable_count": len(comparable),
        "selected_relative_opportunity_cost_percent_sum": round(selected_opp_sum, 6),
        "selected_relative_selection_advantage_percent_sum": round(selected_adv_sum, 6),
        "selected_relative_net_rejection_value_percent": round(selected_adv_sum - selected_opp_sum, 6),
        "mean_candidate_minus_selected_percent": round(value, 6) if (value := _mean(comparable, "candidate_minus_selected_percent")) is not None else None,
        "evidence_status": _evidence_status(len(resolved)),
    }


def build_attribution(records: Sequence[Mapping[str, Any]], *, generated_at: Optional[datetime] = None) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    excluded_not_evaluable = 0
    missing_first_blocker = 0
    for record in sorted(records, key=lambda item: str(item.get("decision_date") or "")):
        outcomes.verify_record(record)
        settlement = record.get("settlement") if isinstance(record.get("settlement"), Mapping) else {}
        rejected = settlement.get("rejected_candidates") if isinstance(settlement.get("rejected_candidates"), Sequence) else []
        selected_t2 = _selected_t2(settlement)
        record_observations = 0
        for candidate in rejected:
            if not isinstance(candidate, Mapping):
                continue
            if candidate.get("economically_evaluable") is not True:
                excluded_not_evaluable += 1
                continue
            if _component(candidate) is None:
                missing_first_blocker += 1
                continue
            observation = observation_from_candidate(record=record, candidate=candidate, selected_t2_return=selected_t2)
            if observation is not None:
                observations.append(observation)
                record_observations += 1
        lineage.append(
            {
                "decision_date": record.get("decision_date"),
                "source_snapshot_sha256": record.get("source_snapshot_sha256"),
                "settlement_status": settlement.get("status"),
                "evaluable_attribution_observations": record_observations,
            }
        )

    component_names = sorted({str((row.get("component") or {}).get("name")) for row in observations if isinstance(row.get("component"), Mapping)})
    components = [aggregate_component(name, observations) for name in component_names]
    resolved = [row for row in observations if row.get("value_status") == "RESOLVED"]
    comparable = [row for row in resolved if row.get("selected_relative_comparable") is True]
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market": "gpw",
        "method": {
            "primary_attribution": "first_blocking_gate_only",
            "one_candidate_one_primary_component": True,
            "double_counting_allowed": False,
            "t2_value_reference_for_all_evaluable_rejects": "flat_zero_return",
            "selected_relative_opportunity_cost_only_for_legal_alternatives": True,
            "hard_gate_rejects_excluded_from_selected_relative_opportunity_set": True,
            "causal_leave_one_component_out_replay": False,
            "shapley_attribution": False,
        },
        "overall": {
            "source_record_count": len(records),
            "evaluable_attribution_observation_count": len(observations),
            "resolved_t2_observation_count": len(resolved),
            "pending_or_data_gap_observation_count": len(observations) - len(resolved),
            "excluded_not_economically_evaluable_count": excluded_not_evaluable,
            "excluded_missing_first_blocking_gate_count": missing_first_blocker,
            "valuable_rejection_count": sum(1 for row in resolved if row.get("classification") == "VALUABLE_REJECTION"),
            "missed_winner_count": sum(1 for row in resolved if row.get("classification") == "MISSED_WINNER"),
            "avoided_loss_percent_sum": round(_sum(resolved, "avoided_loss_percent"), 6),
            "missed_upside_percent_sum": round(_sum(resolved, "missed_upside_percent"), 6),
            "net_rejection_value_vs_flat_percent": round(_sum(resolved, "avoided_loss_percent") - _sum(resolved, "missed_upside_percent"), 6),
            "selected_relative_comparable_count": len(comparable),
            "selected_relative_opportunity_cost_percent_sum": round(_sum(comparable, "selected_relative_opportunity_cost_percent"), 6),
            "selected_relative_selection_advantage_percent_sum": round(_sum(comparable, "selected_relative_selection_advantage_percent"), 6),
        },
        "components": components,
        "lineage": lineage,
        "recent_observations": deepcopy(observations[-RECENT_OBSERVATION_LIMIT:]),
        "governance": {
            "observational_only": True,
            "decision_influence": False,
            "ranking_writeback": False,
            "gate_writeback": False,
            "production_trade_writeback": False,
            "automatic_learning_writeback": False,
            "automatic_promotion": False,
            "automatic_component_removal": False,
            "causal_component_effect_claim": False,
            "historical_backfill": False,
        },
    }
    artifact["artifact_sha256"] = _sha(artifact)
    verify_attribution(artifact)
    return artifact


def verify_attribution(artifact: Mapping[str, Any]) -> None:
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("component attribution schema mismatch")
    body = dict(artifact)
    stored = str(body.pop("artifact_sha256", ""))
    if not stored or stored != _sha(body):
        raise ValueError("component attribution hash mismatch")
    method = artifact.get("method") if isinstance(artifact.get("method"), Mapping) else {}
    if method.get("primary_attribution") != "first_blocking_gate_only" or method.get("one_candidate_one_primary_component") is not True:
        raise ValueError("primary attribution contract violated")
    if method.get("double_counting_allowed") is not False:
        raise ValueError("double counting must remain disabled")
    governance = artifact.get("governance") if isinstance(artifact.get("governance"), Mapping) else {}
    forbidden = (
        "decision_influence",
        "ranking_writeback",
        "gate_writeback",
        "production_trade_writeback",
        "automatic_learning_writeback",
        "automatic_promotion",
        "automatic_component_removal",
        "causal_component_effect_claim",
        "historical_backfill",
    )
    if any(governance.get(field) is not False for field in forbidden):
        raise ValueError("zero-authority component attribution contract violated")
    seen: set[str] = set()
    for row in artifact.get("recent_observations") or []:
        if not isinstance(row, Mapping):
            raise ValueError("invalid recent attribution observation")
        oid = str(row.get("observation_id") or "")
        if not oid or oid in seen:
            raise ValueError("duplicate or empty attribution observation id")
        seen.add(oid)
        if row.get("double_counted") is not False or row.get("primary_attribution") != "first_blocking_gate":
            raise ValueError("observation attribution contract violated")
    for component in artifact.get("components") or []:
        if not isinstance(component, Mapping):
            raise ValueError("invalid component aggregate")
        avoided = float(component.get("avoided_loss_percent_sum") or 0.0)
        missed = float(component.get("missed_upside_percent_sum") or 0.0)
        net = float(component.get("net_rejection_value_vs_flat_percent") or 0.0)
        if abs(net - (avoided - missed)) > 1e-5:
            raise ValueError(f"component value arithmetic mismatch: {component.get('component')}")


def load_records(*, store_dir: Path = STORE_DIR) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(store_dir.glob("????-??-??.json")):
        payload = gpw.load_json(path)
        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid rejected-candidate outcome record: {path}")
        outcomes.verify_record(payload)
        records.append(dict(payload))
    return records


def build_store(*, store_dir: Path = STORE_DIR, output_path: Optional[Path] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    records = load_records(store_dir=store_dir)
    artifact = build_attribution(records, generated_at=now)
    target = output_path or (store_dir / "component_value_attribution.json")
    _atomic(target, artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true", help="Rebuild derived PR506 component attribution from prospective outcomes.")
    mode.add_argument("--verify", action="store_true", help="Verify the current derived attribution artifact.")
    args = parser.parse_args()
    if args.build:
        artifact = build_store()
        print(json.dumps({"status": "OK", "components": len(artifact["components"]), "resolved_observations": artifact["overall"]["resolved_t2_observation_count"]}, ensure_ascii=False))
        return 0
    payload = gpw.load_json(OUTPUT_PATH)
    if not isinstance(payload, Mapping):
        raise ValueError("component value attribution artifact is missing")
    verify_attribution(payload)
    print(json.dumps({"status": "OK", "artifact_sha256": payload.get("artifact_sha256"), "components": len(payload.get("components") or [])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
