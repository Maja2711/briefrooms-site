#!/usr/bin/env python3
"""Read-only WES-SPX / BRACE-SPX bridge.

The bridge freezes point-in-time evidence around the WES S&P 500 decision.
It never changes WES candidates, thresholds, TP/SL, exposure, BRACE-SPX
research state, or broker controls.

Stages:
- pre-wes: after governed v5 execution/admission, before WES postflight.
  Freeze the v5 baseline candidate / risk plan and the contemporaneous
  BRACE-SPX Generation 6 shadow state.
- post-wes: after WES postflight. Freeze the actual WES plan while preserving
  the already-frozen BRACE-SPX state and v5 baseline.

The resulting ledger is intended for future agreement/conflict alpha and
counterfactual evaluation. No bridge record has active decision influence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
WES_REPORT = ROOT / "data" / "investments" / "wes_report.json"
DEFAULT_BRACE_PUBLIC = ROOT / "data" / "public" / "brace_spx_generation6_public.json"
LEDGER = ROOT / "data" / "investments" / "wes_spx_brace_bridge.json"
ALPHA_REPORT = ROOT / "data" / "investments" / "wes_spx_brace_alpha_report.json"

SCHEMA_VERSION = "wes-spx-brace-readonly-v1"
REPORT_VERSION = "wes-spx-brace-alpha-v1"
SPX_ID = "sp500_futures"
MAX_BRACE_AGE_HOURS = 36.0
STRONG_RELATIONSHIP_CONFIDENCE = 0.65


def _read(path: Path, default: Any) -> Any:
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


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current_week_id(now: Optional[datetime] = None) -> str:
    local = (now or _now()).astimezone(ZoneInfo("Europe/Warsaw"))
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _find_spx(week: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    for row in week.get("instruments", []) or []:
        if isinstance(row, dict) and str(row.get("instrument_id")) == SPX_ID:
            return row
    return None


def _report_spx_action(report: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    for row in report.get("actions", []) or []:
        if isinstance(row, dict) and str(row.get("instrument_id")) == SPX_ID:
            return row
    return None


def _copy_risk_plan(item: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    plan = item.get("risk_plan")
    return deepcopy(plan) if isinstance(plan, dict) else None


def _decision_from_item_or_report(
    week: Mapping[str, Any],
    item: Mapping[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    continuous = (
        item.get("continuous_entry_decision")
        if isinstance(item.get("continuous_entry_decision"), dict)
        else {}
    )
    action = _report_spx_action(report) or {}
    candidate = action.get("candidate") if isinstance(action.get("candidate"), dict) else action
    direction = str(
        continuous.get("direction")
        or candidate.get("direction")
        or item.get("direction")
        or "neutral"
    ).lower()
    strategy = continuous.get("strategy_id") or candidate.get("strategy_id") or None
    raw_score = _finite(
        continuous.get("raw_score")
        if continuous.get("raw_score") is not None
        else candidate.get("raw_score")
    )
    entry_price = _finite(item.get("entry_price"))
    entry_at = item.get("entry_captured_at")
    report_at = report.get("checked_at") or report.get("generated_at")
    decision_at = (
        entry_at
        or report_at
        or week.get("forecast_locked_at")
        or week.get("forecast_created_at")
    )
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    entry_class = str(
        plan.get("wes_entry_class")
        or candidate.get("entry_class")
        or ("continuous_entry" if entry_at else "no_trade_monitoring")
    )
    action_name = str(action.get("action") or "")
    if entry_at:
        decision_type = "entered_position"
    elif action_name == "authorize_trigger":
        decision_type = "authorized_trigger"
    elif action_name == "monitor_no_trade":
        decision_type = "no_trade_monitoring"
    else:
        decision_type = "observation"

    identity_payload = {
        "week_id": week.get("week_id"),
        "instrument_id": SPX_ID,
        "decision_type": decision_type,
        "entry_captured_at": entry_at,
        "report_at": None if entry_at else report_at,
        "direction": direction,
        "strategy_id": strategy,
    }
    decision_id = "wes-spx-" + canonical_sha256(identity_payload)[:20]
    return {
        "decision_id": decision_id,
        "decision_type": decision_type,
        "decision_at": decision_at,
        "direction": direction,
        "strategy_id": strategy,
        "raw_score": round(raw_score, 6) if raw_score is not None else None,
        "entry_class": entry_class,
        "entry_price": entry_price,
        "entry_captured_at": entry_at,
        "wes_report_action": action_name or None,
    }


def _safe_mean(values: Iterable[float]) -> Optional[float]:
    rows = list(values)
    return fmean(rows) if rows else None


def brace_specialist_state(shadow: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize G6 shadow as a non-trading specialist stance.

    During warm-up there is deliberately no opinion. Once G6 itself emits all
    eight predeclared candidate snapshots, the bridge summarizes their target
    exposure as risk_on / neutral / defensive. It never selects a champion.
    """
    source = {
        "generation_id": shadow.get("generation_id"),
        "candidate_signature": shadow.get("candidate_signature"),
        "updated_at": shadow.get("updated_at"),
        "latest_market_date": shadow.get("latest_market_date"),
        "status": shadow.get("status"),
        "observations_collected": shadow.get("observations_collected"),
        "warmup_required": shadow.get("warmup_required"),
        "holdout_accessed": bool(shadow.get("holdout_accessed", False)),
        "live_orders": bool(shadow.get("live_orders", False)),
        "autonomous_trading": bool(shadow.get("autonomous_trading", False)),
    }
    governance_ok = (
        source["generation_id"] == "spx-orthogonal-core-v6"
        and source["holdout_accessed"] is False
        and source["live_orders"] is False
        and source["autonomous_trading"] is False
    )
    if not governance_ok:
        return {
            "available": False,
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "brace_spx_governance_guard_failed",
            "source": source,
            "family_scores": None,
            "candidate_consensus": None,
        }

    if str(shadow.get("status")) != "shadow_active_no_orders":
        return {
            "available": False,
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "brace_spx_warmup_no_opinion",
            "source": source,
            "family_scores": None,
            "candidate_consensus": None,
        }

    snapshots = [x for x in (shadow.get("candidate_snapshots") or []) if isinstance(x, dict)]
    exposures = [
        value
        for value in (_finite(row.get("target_exposure_next_session")) for row in snapshots)
        if value is not None
    ]
    if len(exposures) != 8:
        return {
            "available": False,
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "brace_spx_candidate_snapshot_incomplete",
            "source": source,
            "family_scores": deepcopy(shadow.get("family_scores")),
            "candidate_consensus": None,
        }

    mean_exposure = float(_safe_mean(exposures) or 0.0)
    risk_on_votes = sum(value >= 0.60 for value in exposures)
    defensive_votes = sum(value <= 0.40 for value in exposures)
    neutral_votes = len(exposures) - risk_on_votes - defensive_votes

    if mean_exposure >= 0.60:
        stance = "risk_on"
        directional_votes = risk_on_votes
    elif mean_exposure <= 0.40:
        stance = "defensive"
        directional_votes = defensive_votes
    else:
        stance = "neutral"
        directional_votes = neutral_votes

    agreement = directional_votes / len(exposures)
    distance = min(1.0, abs(mean_exposure - 0.50) / 0.50)
    confidence = agreement if stance == "neutral" else agreement * distance
    return {
        "available": True,
        "stance": stance,
        "confidence": round(confidence, 6),
        "reason": "g6_parallel_candidate_consensus_read_only",
        "source": source,
        "family_scores": deepcopy(shadow.get("family_scores")),
        "latest_regime": shadow.get("latest_regime"),
        "candidate_consensus": {
            "candidate_count": len(exposures),
            "mean_target_exposure_next_session": round(mean_exposure, 6),
            "risk_on_votes": risk_on_votes,
            "neutral_votes": neutral_votes,
            "defensive_votes": defensive_votes,
            "agreement_ratio": round(agreement, 6),
            "single_champion_selected": bool(shadow.get("single_champion_selected", False)),
        },
    }


def point_in_time_status(
    decision_at: Any,
    brace_state: Mapping[str, Any],
    *,
    max_age_hours: float = MAX_BRACE_AGE_HOURS,
) -> dict[str, Any]:
    decision_dt = _dt(decision_at)
    brace_dt = _dt((brace_state.get("source") or {}).get("updated_at"))
    if decision_dt is None or brace_dt is None:
        return {"eligible": False, "reason": "missing_decision_or_brace_timestamp", "age_hours": None}
    if brace_dt > decision_dt:
        return {
            "eligible": False,
            "reason": "brace_state_created_after_wes_decision",
            "age_hours": round((brace_dt - decision_dt).total_seconds() / 3600.0, 3),
        }
    age = (decision_dt - brace_dt).total_seconds() / 3600.0
    if age > max_age_hours:
        return {
            "eligible": False,
            "reason": "brace_state_too_old_at_wes_decision",
            "age_hours": round(age, 3),
        }
    if not brace_state.get("available"):
        return {
            "eligible": False,
            "reason": str(brace_state.get("reason") or "brace_state_unavailable"),
            "age_hours": round(age, 3),
        }
    return {"eligible": True, "reason": "point_in_time_valid", "age_hours": round(age, 3)}


def relationship(
    wes_direction: str,
    brace_state: Mapping[str, Any],
    point_in_time: Mapping[str, Any],
) -> dict[str, Any]:
    if not point_in_time.get("eligible"):
        return {
            "class": "UNAVAILABLE",
            "strength": 0.0,
            "alpha_eligible": False,
            "reason": point_in_time.get("reason"),
        }
    stance = str(brace_state.get("stance") or "unavailable")
    direction = str(wes_direction or "neutral").lower()
    confidence = float(brace_state.get("confidence") or 0.0)
    if direction not in {"long", "short"} or stance == "neutral":
        return {
            "class": "NEUTRAL",
            "strength": round(confidence, 6),
            "alpha_eligible": True,
            "reason": "no_directional_agreement_or_conflict",
        }

    agrees = (direction == "long" and stance == "risk_on") or (
        direction == "short" and stance == "defensive"
    )
    conflicts = (direction == "long" and stance == "defensive") or (
        direction == "short" and stance == "risk_on"
    )
    strong = confidence >= STRONG_RELATIONSHIP_CONFIDENCE
    if agrees:
        cls = "STRONG_AGREEMENT" if strong else "WEAK_AGREEMENT"
    elif conflicts:
        cls = "STRONG_CONFLICT" if strong else "WEAK_CONFLICT"
    else:
        cls = "NEUTRAL"
    return {
        "class": cls,
        "strength": round(confidence, 6),
        "alpha_eligible": True,
        "reason": "read_only_relationship_classification",
    }


def _frozen_counterfactual(decision: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "counterfactual_only": True,
        "source": "governed_v5_state_before_wes_postflight",
        "direction": decision.get("direction"),
        "strategy_id": decision.get("strategy_id"),
        "raw_score": decision.get("raw_score"),
        "entry_price": decision.get("entry_price"),
        "entry_captured_at": decision.get("entry_captured_at"),
        "risk_plan": _copy_risk_plan(item),
        "outcome_status": "frozen_pending_counterfactual_evaluator",
        "net_result_percent": None,
    }


def _actual_wes(decision: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "active_decision": True,
        "brace_spx_influence": False,
        "direction": decision.get("direction"),
        "strategy_id": decision.get("strategy_id"),
        "raw_score": decision.get("raw_score"),
        "entry_class": decision.get("entry_class"),
        "entry_price": decision.get("entry_price"),
        "entry_captured_at": decision.get("entry_captured_at"),
        "risk_plan": _copy_risk_plan(item),
    }


def _find_closed_leg(item: Mapping[str, Any], entry_captured_at: Any) -> Optional[Mapping[str, Any]]:
    if not entry_captured_at:
        return None
    for leg in item.get("position_legs", []) or []:
        if not isinstance(leg, Mapping):
            continue
        if str(leg.get("entry_captured_at") or "") == str(entry_captured_at) and leg.get("exit_captured_at"):
            return leg
    if str(item.get("entry_captured_at") or "") == str(entry_captured_at) and item.get("exit_captured_at"):
        return item
    return None


def _settle_actual(record: dict[str, Any], item: Mapping[str, Any]) -> None:
    actual = record.get("wes_actual") or {}
    leg = _find_closed_leg(item, actual.get("entry_captured_at"))
    if not leg:
        record["outcome"] = {
            "status": "pending",
            "wes_net_result_percent": None,
            "v5_counterfactual_net_result_percent": (record.get("v5_counterfactual") or {}).get("net_result_percent"),
        }
        return
    net = _finite(leg.get("net_result_percent"))
    if net is None:
        net = _finite(leg.get("result_percent"))
    record["outcome"] = {
        "status": "wes_observed_v5_counterfactual_pending",
        "closed_at": leg.get("exit_captured_at"),
        "exit_reason": leg.get("exit_reason"),
        "wes_net_result_percent": round(net, 8) if net is not None else None,
        "v5_counterfactual_net_result_percent": (record.get("v5_counterfactual") or {}).get("net_result_percent"),
        "incremental_wes_vs_v5_percent": None,
    }


def _new_ledger() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "active_decision_influence": False,
        "governance": {
            "read_only_bridge": True,
            "wes_decisions_modified": False,
            "brace_spx_research_modified": False,
            "bounded_influence_enabled": False,
            "sealed_holdout_must_remain_unaccessed": True,
            "point_in_time_required_for_alpha": True,
        },
        "records": [],
    }


def capture(
    *,
    stage: str,
    week: Mapping[str, Any],
    wes_report: Mapping[str, Any],
    brace_shadow: Mapping[str, Any],
    ledger: Mapping[str, Any],
    captured_at: Optional[datetime] = None,
) -> dict[str, Any]:
    if stage not in {"pre-wes", "post-wes"}:
        raise ValueError("stage must be pre-wes or post-wes")
    out = deepcopy(ledger) if ledger else _new_ledger()
    out.setdefault("schema_version", SCHEMA_VERSION)
    out["active_decision_influence"] = False
    out.setdefault("governance", _new_ledger()["governance"])
    records = [deepcopy(x) for x in (out.get("records") or []) if isinstance(x, dict)]
    item = _find_spx(week)
    if item is None:
        out["records"] = records
        return out

    decision = _decision_from_item_or_report(week, item, wes_report)
    decision_id = str(decision["decision_id"])
    existing = next((row for row in records if row.get("decision_id") == decision_id), None)
    now = captured_at or _now()

    if existing is None:
        brace_state = brace_specialist_state(brace_shadow)
        pit = point_in_time_status(decision.get("decision_at"), brace_state)
        rel = relationship(str(decision.get("direction")), brace_state, pit)
        if decision.get("decision_type") not in {"entered_position", "authorized_trigger"}:
            rel = {**rel, "alpha_eligible": False, "reason": "non_actionable_monitoring_observation"}
        existing = {
            "decision_id": decision_id,
            "week_id": week.get("week_id"),
            "instrument_id": SPX_ID,
            "decision_type": decision.get("decision_type"),
            "decision_at": decision.get("decision_at"),
            "first_captured_at": now.isoformat(timespec="seconds"),
            "last_updated_at": now.isoformat(timespec="seconds"),
            "active_decision_influence": False,
            "point_in_time": pit,
            "brace_spx": brace_state,
            "relationship": rel,
            "counterfactual_overlay": {
                "decision_without_brace_spx": decision.get("direction"),
                "decision_with_brace_spx": "not_computed_until_calibration",
                "hypothetical_role": (
                    "support"
                    if "AGREEMENT" in rel["class"]
                    else "caution"
                    if "CONFLICT" in rel["class"]
                    else "no_opinion"
                ),
                "bounded_modifier_applied": False,
            },
            "v5_counterfactual": None,
            "wes_actual": None,
            "outcome": {"status": "pending"},
        }
        records.append(existing)
    else:
        existing["last_updated_at"] = now.isoformat(timespec="seconds")

    if stage == "pre-wes" and existing.get("v5_counterfactual") is None:
        existing["v5_counterfactual"] = _frozen_counterfactual(decision, item)
        existing["pre_wes_frozen_at"] = now.isoformat(timespec="seconds")

    if stage == "post-wes":
        existing["wes_actual"] = _actual_wes(decision, item)
        existing["post_wes_frozen_at"] = now.isoformat(timespec="seconds")
        _settle_actual(existing, item)

    out["records"] = sorted(
        records,
        key=lambda row: (
            str(row.get("week_id") or ""),
            str(row.get("decision_at") or ""),
            str(row.get("decision_id") or ""),
        ),
    )
    out["updated_at"] = now.isoformat(timespec="seconds")
    out["content_sha256"] = canonical_sha256(
        {"schema_version": out["schema_version"], "governance": out["governance"], "records": out["records"]}
    )
    return out


def _bucket(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        float(value)
        for value in (
            _finite((row.get("outcome") or {}).get("wes_net_result_percent")) for row in rows
        )
        if value is not None
    ]
    return {
        "records": len(rows),
        "settled_wes_outcomes": len(values),
        "mean_wes_net_percent": round(fmean(values), 8) if values else None,
        "win_rate": round(sum(value > 0 for value in values) / len(values), 6) if values else None,
    }


def build_alpha_report(ledger: Mapping[str, Any]) -> dict[str, Any]:
    records = [x for x in (ledger.get("records") or []) if isinstance(x, Mapping)]
    eligible = [row for row in records if (row.get("relationship") or {}).get("alpha_eligible") is True]
    classes = [
        "STRONG_AGREEMENT",
        "WEAK_AGREEMENT",
        "NEUTRAL",
        "WEAK_CONFLICT",
        "STRONG_CONFLICT",
        "UNAVAILABLE",
    ]
    by_relationship = {
        cls: _bucket([row for row in records if (row.get("relationship") or {}).get("class") == cls])
        for cls in classes
    }
    counterfactual_ready = [
        row
        for row in eligible
        if _finite((row.get("v5_counterfactual") or {}).get("net_result_percent")) is not None
        and _finite((row.get("outcome") or {}).get("wes_net_result_percent")) is not None
    ]
    return {
        "schema_version": REPORT_VERSION,
        "source_ledger_schema_version": ledger.get("schema_version"),
        "active_decision_influence": False,
        "bounded_influence_enabled": False,
        "records": len(records),
        "point_in_time_alpha_eligible_records": len(eligible),
        "by_relationship": by_relationship,
        "counterfactual": {
            "v5_baselines_frozen": sum(bool(row.get("v5_counterfactual")) for row in records),
            "wes_actual_plans_frozen": sum(bool(row.get("wes_actual")) for row in records),
            "resolved_wes_vs_v5_pairs": len(counterfactual_ready),
            "status": "ready_for_incremental_alpha" if counterfactual_ready else "collecting_frozen_baselines_and_outcomes",
        },
        "interpretation": {
            "agreement_conflict_alpha": "Use only point-in-time eligible BRACE-SPX states; warm-up and retrospective states are excluded.",
            "v5_counterfactual": "The pre-WES v5 plan is frozen now so a later evaluator can resolve it without hindsight.",
            "bounded_influence": "Disabled. The bridge cannot change WES runtime decisions.",
        },
    }


def _load_brace_shadow(path: Path) -> dict[str, Any]:
    if path.exists():
        return _read(path, {})
    return _read(DEFAULT_BRACE_PUBLIC, {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["pre-wes", "post-wes"], required=True)
    parser.add_argument("--week-id", default=None)
    parser.add_argument("--week-path", type=Path, default=None)
    parser.add_argument("--wes-report", type=Path, default=WES_REPORT)
    parser.add_argument("--brace-shadow", type=Path, default=Path("/tmp/brace_spx_generation6_shadow.json"))
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--alpha-report", type=Path, default=ALPHA_REPORT)
    args = parser.parse_args()

    week_path = args.week_path or WEEKLY_DIR / f"{args.week_id or current_week_id()}.json"
    if not week_path.exists():
        print(f"WES-SPX/BRACE-SPX bridge skipped: no week file {week_path}")
        return

    week = _read(week_path, {})
    report = _read(args.wes_report, {})
    brace = _load_brace_shadow(args.brace_shadow)
    ledger = _read(args.ledger, _new_ledger())
    updated = capture(stage=args.stage, week=week, wes_report=report, brace_shadow=brace, ledger=ledger)
    _write(args.ledger, updated)
    _write(args.alpha_report, build_alpha_report(updated))
    latest = updated.get("records", [])[-1] if updated.get("records") else {}
    print(
        "WES-SPX/BRACE-SPX bridge",
        f"stage={args.stage}",
        f"records={len(updated.get('records', []))}",
        f"relationship={(latest.get('relationship') or {}).get('class')}",
        "active_decision_influence=false",
    )


if __name__ == "__main__":
    main()
