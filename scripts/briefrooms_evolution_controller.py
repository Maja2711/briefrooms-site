#!/usr/bin/env python3
"""BriefRooms Evolution Controller.

One lifecycle/governance controller over the existing distributed learning
fabric. It does not replace source-engine research or engine-owned production
writers. It normalizes candidates, applies shared prospective gates, records
version lineage, routes approved changes to the correct writer, monitors
post-promotion performance, and issues rollback/retirement actions.

Direct materialization is deliberately narrow:
- Belief probability-calibration overlays.
- Belief v3 activation registry.

Stock Trading component mutations remain delegated to the existing main-owned
Stock Trading Component Promotion writer. Trade execution is never authorized.
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

from hypothesis_challenger_engine import (
    SCHEMA as HYPOTHESIS_CHALLENGER_SCHEMA,
    _gate as hypothesis_challenger_gate,
    _settle as hypothesis_challenger_settle,
    utc as hypothesis_challenger_utc,
)
from briefrooms_evolution_contracts import (
    EvolutionCandidate,
    ProductionVersion,
    PromotionGate,
    RollbackEvent,
    canonical_json,
    sha256,
    stable_id,
    utc_now_z,
)
from belief_closed_loop import (
    MAX_ACCURACY_DEGRADATION,
    MAX_ECE_DEGRADATION,
    MIN_PROSPECTIVE_N,
    MIN_ROLLBACK_N,
    PROMOTION_BRIER_REL_IMPROVEMENT,
    ROLLBACK_BRIER_REL_DEGRADATION,
    ROLLBACK_ECE_DEGRADATION,
    metrics as probability_metrics,
    relative_improvement,
    transform_probability,
)
from belief_v3_candidate_library import V3_CANDIDATE_LIBRARY, V3_GOVERNANCE

V3_CANDIDATE_IDS = {str(x["belief_id"]) for x in V3_CANDIDATE_LIBRARY}

SCHEMA_VERSION = "briefrooms-evolution-controller-v1"
PUBLIC_SCHEMA_VERSION = "briefrooms-evolution-controller-public-v1"
V3_REGISTRY_SCHEMA = "belief-v3-production-registry-v1"

DEFAULT_STATE = Path("data/investments/evolution_controller_state.json")
DEFAULT_PUBLIC = Path("data/investments/evolution_controller_public.json")
DEFAULT_AUDIT = Path("data/investments/evolution_controller_audit.jsonl")
DEFAULT_BELIEF_POLICY = Path("data/investments/belief_core_production_overrides.json")
DEFAULT_V3_REGISTRY = Path("data/investments/belief_v3_production_registry.json")

V3_CONTROL = {
    "spx.rates.supportive": "spx.trend.bullish",
    "spx.cross_asset_risk.supportive": "spx.trend.bullish",
    "eurusd.risk_regime.supportive": "eurusd.trend.bullish",
    "btc.cross_asset_risk.supportive": "btc.trend.bullish",
}

AUTHORITY = {
    "trade_execution": False,
    "position_sizing": False,
    "risk_limit_weakening": False,
    "historical_rewrite": False,
    "source_evidence_mutation": False,
    "belief_evidence_mutation": False,
    "production_orchestration": True,
    "bounded_belief_overlay_materialization": True,
    "bounded_v3_registry_materialization": True,
    "stock_trading_materialization": False,
    "stock_trading_route": "Stock Trading Component Promotion",
}


def _read_json(path: Path | None, default: Any = None) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(dict(payload)) + "\n")


def _parse(value: str) -> datetime:
    text = str(value or "").strip()
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [x for x in value.values() if isinstance(x, Mapping)]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, Mapping)]
    return []


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "authority": dict(AUTHORITY),
        "candidates": {},
        "promotion_gates": {},
        "production_versions": {},
        "rollback_events": [],
        "hypotheses": {},
        "delegated_actions": [],
        "retirement_actions": [],
        "source_status": {},
        "summary": {},
    }


def _load_state(path: Path) -> dict[str, Any]:
    state = _read_json(path, _default_state())
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        return _default_state()
    state["authority"] = dict(AUTHORITY)
    for key, default in (
        ("candidates", {}), ("promotion_gates", {}), ("production_versions", {}),
        ("rollback_events", []), ("hypotheses", {}), ("delegated_actions", []),
        ("retirement_actions", []), ("source_status", {}),
    ):
        if not isinstance(state.get(key), type(default)):
            state[key] = default
    return state


def _belief_policy(path: Path) -> dict[str, Any]:
    payload = _read_json(path, {})
    if not isinstance(payload, dict) or payload.get("schema_version") != "belief-core-production-overrides-v1":
        payload = {
            "schema_version": "belief-core-production-overrides-v1",
            "updated_at": None,
            "authority": {
                "scope": "probability_calibration_overlay_only",
                "may_change_evidence": False,
                "may_change_sources": False,
                "may_execute_trades": False,
                "may_change_sizing": False,
                "automatic_promotion_after_prospective_gate": True,
                "automatic_rollback": True,
            },
            "overrides": {},
            "history": [],
        }
    return payload


def _v3_registry(path: Path) -> dict[str, Any]:
    payload = _read_json(path, {})
    if not isinstance(payload, dict) or payload.get("schema_version") != V3_REGISTRY_SCHEMA:
        payload = {
            "schema_version": V3_REGISTRY_SCHEMA,
            "updated_at": None,
            "authority": {
                "trade_execution": False,
                "decision_engine_writeback": False,
                "consumer_export_requires_explicit_bridge": True,
                "automatic_activation_after_evolution_gate": True,
                "automatic_retirement": True,
            },
            "active": {},
            "history": [],
        }
    return payload


def _register_candidate(state: dict[str, Any], candidate: EvolutionCandidate) -> dict[str, Any]:
    payload = candidate.to_dict()
    existing = state["candidates"].get(candidate.candidate_id)
    if isinstance(existing, Mapping):
        # Lifecycle status and metrics may advance; immutable identity fields may not.
        for key in (
            "candidate_id", "candidate_type", "source_module", "target_module",
            "component_id", "evaluator_profile", "promotion_route",
            "challenger_version", "source_ref", "source_sha256",
        ):
            if str(existing.get(key)) != str(payload.get(key)):
                raise RuntimeError(f"candidate identity drift: {candidate.candidate_id}:{key}")
    state["candidates"][candidate.candidate_id] = payload
    return payload


def _gate(state: dict[str, Any], gate: PromotionGate) -> dict[str, Any]:
    payload = gate.to_dict()
    state["promotion_gates"][gate.candidate_id] = payload
    return payload


def _production_version(state: dict[str, Any], version: ProductionVersion) -> dict[str, Any]:
    payload = version.to_dict()
    rows = state["production_versions"].setdefault(version.component_id, [])
    if not any(x.get("version_id") == version.version_id for x in rows if isinstance(x, Mapping)):
        for row in rows:
            if isinstance(row, dict) and row.get("status") == "ACTIVE":
                row["status"] = "SUPERSEDED"
        rows.append(payload)
    return payload


def _rollback(state: dict[str, Any], event: RollbackEvent) -> dict[str, Any]:
    payload = event.to_dict()
    if not any(x.get("rollback_id") == event.rollback_id for x in state["rollback_events"] if isinstance(x, Mapping)):
        state["rollback_events"].append(payload)
    for row in state["production_versions"].get(event.component_id, []):
        if isinstance(row, dict) and row.get("version_id") == event.from_version_id:
            row["status"] = "ROLLED_BACK"
    return payload


def _belief_rows(belief_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    forecasts = {
        str(f.get("forecast_id") or ""): f
        for f in _records(belief_state.get("forecasts"))
        if f.get("forecast_id")
    }
    rows: list[dict[str, Any]] = []
    for v in _records(belief_state.get("verifications")):
        fid = str(v.get("forecast_id") or "")
        f = forecasts.get(fid)
        if not f or bool(v.get("legacy")) or v.get("calibration_eligible") is False:
            continue
        p = _safe_float(v.get("predicted_probability"))
        if p is None:
            p = _safe_float(f.get("predicted_probability"))
        if p is None:
            continue
        meta = f.get("metadata") if isinstance(f.get("metadata"), Mapping) else {}
        raw = _safe_float(meta.get("raw_probability"))
        if raw is None:
            raw = p
        horizon = _safe_float(f.get("horizon_hours")) or 0.0
        if horizon <= 6:
            hb = "0-6H"
        elif horizon <= 24:
            hb = "6-24H"
        elif horizon <= 120:
            hb = "1-5D"
        else:
            hb = "5D+"
        bid = str(f.get("belief_id") or v.get("belief_id") or "")
        instrument = "SPX" if bid.startswith("spx.") else "BTC" if bid.startswith("btc.") else "EURUSD" if bid.startswith("eurusd.") else str(f.get("entity") or "OTHER")
        rows.append({
            "forecast_id": fid,
            "belief_id": bid,
            "entity": f.get("entity"),
            "instrument": instrument,
            "forecast_at": str(f.get("forecast_at") or v.get("forecast_at") or ""),
            "target_at": str(f.get("target_at") or v.get("target_at") or ""),
            "horizon_bucket": hb,
            "raw_probability": float(raw),
            "production_probability": float(p),
            "outcome": 1 if bool(v.get("outcome")) else 0,
        })
    rows.sort(key=lambda x: x["forecast_at"])
    return rows


def _rows_for_scope(rows: Sequence[Mapping[str, Any]], scope: str) -> list[dict[str, Any]]:
    if scope == "__GLOBAL__":
        return [dict(x) for x in rows if str(x.get("belief_id") or "") not in V3_CANDIDATE_IDS]
    return [dict(x) for x in rows if str(x.get("belief_id") or "") == scope]


def _segment_safety(
    rows: Sequence[Mapping[str, Any]],
    transform: Mapping[str, Any],
    *,
    boundary: str,
    minimum_segment_n: int = 10,
) -> tuple[list[dict[str, Any]], list[str]]:
    future = [x for x in rows if x.get("forecast_at") and _parse(str(x["forecast_at"])) > _parse(boundary)]
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in future:
        groups[f"instrument:{row.get('instrument')}"] .append(row)
        groups[f"horizon:{row.get('horizon_bucket')}"] .append(row)
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    for name, sample in sorted(groups.items()):
        if len(sample) < minimum_segment_n:
            continue
        control = probability_metrics([(float(x["raw_probability"]), int(x["outcome"])) for x in sample])
        challenger = probability_metrics([
            (transform_probability(float(x["raw_probability"]), transform), int(x["outcome"]))
            for x in sample
        ])
        imp = relative_improvement(challenger.get("brier"), control.get("brier"))
        ece_delta = None
        if challenger.get("ece") is not None and control.get("ece") is not None:
            ece_delta = float(challenger["ece"]) - float(control["ece"])
        passed = (
            imp is not None and imp >= -0.10
            and (ece_delta is None or ece_delta <= 0.05)
        )
        checks.append({
            "segment": name,
            "n": len(sample),
            "control_brier": control.get("brier"),
            "challenger_brier": challenger.get("brier"),
            "brier_relative_improvement": None if imp is None else round(imp, 6),
            "ece_delta": None if ece_delta is None else round(ece_delta, 6),
            "pass": passed,
        })
        if not passed:
            blockers.append(f"protected_slice_degradation:{name}")
    return checks, blockers


def _ingest_belief_calibration(
    state: dict[str, Any],
    belief_state: Mapping[str, Any],
    closed_loop: Mapping[str, Any],
    policy: dict[str, Any],
    now: str,
    audit: Path,
) -> None:
    rows = _belief_rows(belief_state)
    challengers = closed_loop.get("challengers") if isinstance(closed_loop.get("challengers"), Mapping) else {}
    scan_status = {}
    for scope, raw in challengers.items():
        if not isinstance(raw, Mapping):
            continue
        scan_status[str(scope)] = {
            "status": raw.get("status"),
            "last_discovery_n": raw.get("last_discovery_n"),
            "rediscovery_after_n": raw.get("rediscovery_after_n"),
            "prospective_n": (raw.get("prospective") or {}).get("n") if isinstance(raw.get("prospective"), Mapping) else None,
            "trigger_reasons": list(raw.get("trigger_reasons") or []),
        }
    state["source_status"]["belief_calibration"] = {
        "available": True,
        "resolved_rows": len(rows),
        "source_generated_at": closed_loop.get("generated_at"),
        "scan_status": scan_status,
    }
    for scope, raw in sorted(challengers.items()):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("transform"), Mapping):
            continue
        status = str(raw.get("status") or "")
        if status not in {"prospective_shadow", "ready_for_evolution_controller", "rollback_recommended", "promoted_by_evolution_controller"}:
            continue
        frozen_at = str(raw.get("frozen_at") or raw.get("created_at") or now)
        identity = {"scope": scope, "frozen_at": frozen_at, "transform": raw["transform"]}
        cid = stable_id("evo-belief", identity)
        candidate_status = "PROMOTION_ELIGIBLE" if status == "ready_for_evolution_controller" else "OOS_RUNNING"
        candidate = EvolutionCandidate(
            candidate_id=cid,
            candidate_type="probability_calibration",
            source_module="EP-07",
            target_module="EP-05",
            component_id=f"belief_probability:{scope}",
            created_at=str(raw.get("created_at") or frozen_at),
            activation_boundary=frozen_at,
            status=candidate_status,
            evaluator_profile="belief_calibration_v1",
            promotion_route="belief_core_probability_overlay",
            baseline_version="belief-core-v2-raw",
            challenger_version=f"cal:{sha256(raw['transform'])[:12]}",
            proposed_change={"scope": scope, "transform": dict(raw["transform"])},
            source_ref=f"belief-closed-loop://{scope}/{frozen_at}",
            source_sha256=sha256(raw),
            metrics={"discovery": dict(raw.get("discovery") or {}), "prospective": dict(raw.get("prospective") or {})},
            automatic_promotion_allowed=True,
            trade_execution_authority=False,
            metadata={"trigger_reasons": list(raw.get("trigger_reasons") or [])},
        )
        _register_candidate(state, candidate)

        scope_rows = _rows_for_scope(rows, str(scope))
        ev = raw.get("prospective") if isinstance(raw.get("prospective"), Mapping) else {}
        n = int(ev.get("n") or 0)
        control = ev.get("control") if isinstance(ev.get("control"), Mapping) else {}
        challenger = ev.get("challenger") if isinstance(ev.get("challenger"), Mapping) else {}
        improvement = _safe_float(ev.get("brier_relative_improvement"))
        stability = ev.get("stability") if isinstance(ev.get("stability"), Mapping) else {}
        checks, blockers = _segment_safety(scope_rows, raw["transform"], boundary=frozen_at)
        if n < MIN_PROSPECTIVE_N:
            gate_status = "COLLECTING"
            blockers = ["minimum_prospective_sample"] + blockers
        else:
            if improvement is None or improvement < PROMOTION_BRIER_REL_IMPROVEMENT:
                blockers.append("brier_improvement_below_gate")
            if _safe_float(challenger.get("log_loss")) is None or _safe_float(control.get("log_loss")) is None or float(challenger["log_loss"]) >= float(control["log_loss"]):
                blockers.append("log_loss_not_better")
            if _safe_float(challenger.get("ece")) is None or _safe_float(control.get("ece")) is None or float(challenger["ece"]) > float(control["ece"]) + MAX_ECE_DEGRADATION:
                blockers.append("ece_degradation")
            if _safe_float(challenger.get("accuracy")) is None or _safe_float(control.get("accuracy")) is None or float(challenger["accuracy"]) < float(control["accuracy"]) - MAX_ACCURACY_DEGRADATION:
                blockers.append("accuracy_degradation")
            if int(stability.get("improved") or 0) < 3:
                blockers.append("stability_less_than_3_of_4")
            gate_status = "PASS" if not blockers else "FAIL"

        gate = PromotionGate(
            gate_id=stable_id("evo-gate", {"candidate": cid, "n": n, "source": sha256(ev)}),
            candidate_id=cid,
            evaluated_at=now,
            status=gate_status,
            evaluator_profile="belief_calibration_v1",
            prospective_only=True,
            minimum_sample=MIN_PROSPECTIVE_N,
            observed_sample=n,
            criteria={
                "brier_relative_improvement_min": PROMOTION_BRIER_REL_IMPROVEMENT,
                "log_loss_must_improve": True,
                "max_ece_degradation": MAX_ECE_DEGRADATION,
                "max_accuracy_degradation": MAX_ACCURACY_DEGRADATION,
                "stability": "3_of_4",
                "protected_slice_max_brier_degradation": 0.10,
            },
            metrics={"control": dict(control), "challenger": dict(challenger), "brier_relative_improvement": improvement, "stability": dict(stability)},
            segment_checks=tuple(checks),
            blockers=tuple(sorted(set(blockers))),
            source_sha256=sha256(ev),
        )
        _gate(state, gate)

        if gate_status == "PASS" and candidate.automatic_promotion_allowed:
            current = (policy.get("overrides") or {}).get(scope)
            if not isinstance(current, Mapping) or not current.get("active") or current.get("candidate_id") != cid:
                version_id = stable_id("prod-belief", {"candidate": cid, "gate": gate.gate_id})
                policy.setdefault("overrides", {})[scope] = {
                    "active": True,
                    "belief_id": scope,
                    "version": version_id,
                    "candidate_id": cid,
                    "transform": dict(raw["transform"]),
                    "promoted_at": now,
                    "prospective_gate": gate.to_dict(),
                    "raw_control_preserved": True,
                    "evolution_controller": SCHEMA_VERSION,
                    "rollback": {
                        "minimum_post_promotion_n": MIN_ROLLBACK_N,
                        "brier_relative_degradation": ROLLBACK_BRIER_REL_DEGRADATION,
                        "ece_degradation": ROLLBACK_ECE_DEGRADATION,
                    },
                }
                event = {"at": now, "event": "EVOLUTION_PROMOTION", "component_id": candidate.component_id, "candidate_id": cid, "version_id": version_id}
                policy.setdefault("history", []).append(event)
                _append_jsonl(audit, event)
                _production_version(state, ProductionVersion(
                    version_id=version_id,
                    component_id=candidate.component_id,
                    candidate_id=cid,
                    activated_at=now,
                    parent_version_id=None,
                    owner_module="EP-05",
                    materialization_route="belief_core_probability_overlay",
                    payload_sha256=sha256(policy["overrides"][scope]),
                    status="ACTIVE",
                    trade_execution_authority=False,
                    metadata={"scope": scope},
                ))
                state["candidates"][cid]["status"] = "PROMOTED"

    # Central rollback monitor for active belief overlays.
    for scope, override in list((policy.get("overrides") or {}).items()):
        if not isinstance(override, dict) or not override.get("active"):
            continue
        promoted_at = str(override.get("promoted_at") or "")
        transform = override.get("transform") if isinstance(override.get("transform"), Mapping) else {}
        sample = [
            r for r in _rows_for_scope(rows, str(scope))
            if promoted_at and r.get("forecast_at") and _parse(str(r["forecast_at"])) > _parse(promoted_at)
        ]
        if len(sample) < MIN_ROLLBACK_N:
            continue
        raw_m = probability_metrics([(float(r["raw_probability"]), int(r["outcome"])) for r in sample])
        prod_m = probability_metrics([(float(r["production_probability"]), int(r["outcome"])) for r in sample])
        brier_deg = None
        if raw_m.get("brier") not in (None, 0):
            brier_deg = (float(prod_m["brier"]) - float(raw_m["brier"])) / float(raw_m["brier"])
        ece_deg = None
        if prod_m.get("ece") is not None and raw_m.get("ece") is not None:
            ece_deg = float(prod_m["ece"]) - float(raw_m["ece"])
        if (brier_deg is not None and brier_deg >= ROLLBACK_BRIER_REL_DEGRADATION) or (ece_deg is not None and ece_deg >= ROLLBACK_ECE_DEGRADATION):
            override["active"] = False
            override["rolled_back_at"] = now
            from_version = str(override.get("version") or "")
            candidate_id = str(override.get("candidate_id") or "")
            event = RollbackEvent(
                rollback_id=stable_id("rollback", {"version": from_version, "at": now}),
                component_id=f"belief_probability:{scope}",
                from_version_id=from_version,
                to_version_id=None,
                candidate_id=candidate_id or "unknown",
                detected_at=now,
                effective_at=now,
                reason="POST_PROMOTION_DEGRADATION",
                metrics={"n": len(sample), "raw": raw_m, "production": prod_m, "brier_relative_degradation": brier_deg, "ece_degradation": ece_deg},
                owner_module="EP-05",
                materialization_route="belief_core_probability_overlay",
                automatic=True,
            )
            _rollback(state, event)
            policy.setdefault("history", []).append({"at": now, "event": "EVOLUTION_ROLLBACK", **event.to_dict()})
            state["retirement_actions"].append({"at": now, "component_id": event.component_id, "action": "ROLLBACK", "reason": event.reason})
            _append_jsonl(audit, {"event": "EVOLUTION_ROLLBACK", **event.to_dict()})


def _matched_v3_rows(belief_state: Mapping[str, Any], candidate_id: str, control_id: str) -> list[dict[str, Any]]:
    forecasts = _records(belief_state.get("forecasts"))
    verifications = {
        str(v.get("forecast_id") or ""): v
        for v in _records(belief_state.get("verifications"))
        if v.get("forecast_id") and v.get("calibration_eligible") is not False and not bool(v.get("legacy"))
    }
    controls: dict[str, Mapping[str, Any]] = {}
    candidates: list[Mapping[str, Any]] = []
    for f in forecasts:
        bid = str(f.get("belief_id") or "")
        if bid == control_id and round(float(f.get("horizon_hours") or 0)) == 24:
            controls[str(f.get("target_at") or "")] = f
        elif bid == candidate_id and round(float(f.get("horizon_hours") or 0)) == 24:
            candidates.append(f)
    out = []
    for f in candidates:
        target = str(f.get("target_at") or "")
        control = controls.get(target)
        cv = verifications.get(str(f.get("forecast_id") or ""))
        bv = verifications.get(str((control or {}).get("forecast_id") or ""))
        if not control or not cv or not bv:
            continue
        # Both contracts must resolve the same target-asset event.
        cmeta = f.get("metadata") if isinstance(f.get("metadata"), Mapping) else {}
        bmeta = control.get("metadata") if isinstance(control.get("metadata"), Mapping) else {}
        cspec = cmeta.get("outcome_spec") if isinstance(cmeta.get("outcome_spec"), Mapping) else {}
        bspec = bmeta.get("outcome_spec") if isinstance(bmeta.get("outcome_spec"), Mapping) else {}
        if str(cspec.get("kind")) != "price_above" or str(bspec.get("kind")) != "price_above" or str(cspec.get("symbol")) != str(bspec.get("symbol")):
            continue
        if bool(cv.get("outcome")) != bool(bv.get("outcome")):
            continue
        cp = _safe_float(f.get("predicted_probability"))
        bp = _safe_float(control.get("predicted_probability"))
        if cp is None or bp is None:
            continue
        out.append({
            "forecast_at": str(f.get("forecast_at") or ""),
            "target_at": target,
            "candidate_p": cp,
            "control_p": bp,
            "outcome": 1 if bool(cv.get("outcome")) else 0,
        })
    out.sort(key=lambda x: x["forecast_at"])
    return out


def _pair_metrics(rows: Sequence[Mapping[str, Any]], *, after: str | None = None) -> dict[str, Any]:
    sample = [r for r in rows if not after or _parse(str(r["forecast_at"])) > _parse(after)]
    control = probability_metrics([(float(r["control_p"]), int(r["outcome"])) for r in sample])
    challenger = probability_metrics([(float(r["candidate_p"]), int(r["outcome"])) for r in sample])
    improvement = relative_improvement(challenger.get("brier"), control.get("brier"))
    return {
        "n": len(sample),
        "control": control,
        "challenger": challenger,
        "brier_relative_improvement": None if improvement is None else round(improvement, 6),
    }


def _ingest_v3(
    state: dict[str, Any],
    belief_state: Mapping[str, Any],
    v3_registry: dict[str, Any],
    now: str,
    audit: Path,
) -> None:
    state["source_status"]["belief_v3"] = {"available": True, "library_version": V3_GOVERNANCE["library_version"]}
    prior = state["candidates"]
    for item in V3_CANDIDATE_LIBRARY:
        bid = str(item["belief_id"])
        control_id = V3_CONTROL.get(bid)
        if not control_id:
            continue
        rows = _matched_v3_rows(belief_state, bid, control_id)
        discovery = _pair_metrics(rows)
        existing = next(
            (
                x for x in prior.values()
                if isinstance(x, Mapping)
                and x.get("candidate_type") == "belief_v3"
                and x.get("component_id") == f"belief_v3:{bid}"
                and x.get("status") not in {"REJECTED", "RETIRED", "ROLLED_BACK"}
            ),
            None,
        )
        if existing is None:
            if discovery["n"] < int(V3_GOVERNANCE["minimum_sample_for_review"]):
                continue
            d_control = discovery["control"]
            d_ch = discovery["challenger"]
            imp = _safe_float(discovery["brier_relative_improvement"])
            if (
                imp is None or imp < 0.03
                or _safe_float(d_ch.get("log_loss")) is None
                or _safe_float(d_control.get("log_loss")) is None
                or float(d_ch["log_loss"]) >= float(d_control["log_loss"])
                or _safe_float(d_ch.get("ece")) is None
                or _safe_float(d_control.get("ece")) is None
                or float(d_ch["ece"]) > float(d_control["ece"]) + 0.01
            ):
                continue
            boundary = now
            cid = stable_id("evo-v3", {"belief_id": bid, "boundary": boundary, "library": V3_GOVERNANCE["library_version"]})
            candidate = EvolutionCandidate(
                candidate_id=cid,
                candidate_type="belief_v3",
                source_module="EP-05",
                target_module="EP-05",
                component_id=f"belief_v3:{bid}",
                created_at=now,
                activation_boundary=boundary,
                status="OOS_RUNNING",
                evaluator_profile="belief_v3_incremental_v1",
                promotion_route="belief_v3_registry",
                baseline_version=control_id,
                challenger_version=V3_GOVERNANCE["library_version"],
                proposed_change={"activate_belief_id": bid, "control_belief_id": control_id},
                source_ref=f"belief-v3://{bid}",
                source_sha256=sha256(item),
                metrics={"discovery": discovery},
                automatic_promotion_allowed=True,
                trade_execution_authority=False,
                metadata={"required_evidence": list(item.get("required_evidence") or [])},
            )
            _register_candidate(state, candidate)
            _append_jsonl(audit, {"at": now, "event": "V3_OOS_FROZEN", "candidate_id": cid, "belief_id": bid, "discovery": discovery})
            existing = state["candidates"][cid]

        if not isinstance(existing, Mapping):
            continue
        cid = str(existing["candidate_id"])
        boundary = str(existing["activation_boundary"])
        oos = _pair_metrics(rows, after=boundary)
        blockers = []
        if oos["n"] < 50:
            gate_status = "COLLECTING"
            blockers.append("minimum_oos_sample")
        else:
            imp = _safe_float(oos["brier_relative_improvement"])
            if imp is None or imp < 0.05:
                blockers.append("incremental_brier_below_5pct")
            c = oos["control"]; n = oos["challenger"]
            if _safe_float(n.get("log_loss")) is None or _safe_float(c.get("log_loss")) is None or float(n["log_loss"]) >= float(c["log_loss"]):
                blockers.append("log_loss_not_better")
            if _safe_float(n.get("ece")) is None or _safe_float(c.get("ece")) is None or float(n["ece"]) > float(c["ece"]) + 0.01:
                blockers.append("ece_degradation")
            gate_status = "PASS" if not blockers else "FAIL"
        gate = PromotionGate(
            gate_id=stable_id("evo-gate", {"candidate": cid, "n": oos["n"], "source": sha256(oos)}),
            candidate_id=cid,
            evaluated_at=now,
            status=gate_status,
            evaluator_profile="belief_v3_incremental_v1",
            prospective_only=True,
            minimum_sample=50,
            observed_sample=int(oos["n"]),
            criteria={
                "matched_control_required": control_id,
                "incremental_brier_relative_improvement_min": 0.05,
                "log_loss_must_improve": True,
                "max_ece_degradation": 0.01,
            },
            metrics=oos,
            blockers=tuple(blockers),
            source_sha256=sha256(oos),
        )
        _gate(state, gate)
        state["candidates"][cid]["metrics"] = {"discovery": discovery, "oos": oos}
        if gate_status == "PASS":
            active = (v3_registry.get("active") or {}).get(bid)
            if not isinstance(active, Mapping) or active.get("candidate_id") != cid:
                version_id = stable_id("prod-v3", {"candidate": cid, "gate": gate.gate_id})
                row = {
                    "belief_id": bid,
                    "candidate_id": cid,
                    "version_id": version_id,
                    "activated_at": now,
                    "control_belief_id": control_id,
                    "gate": gate.to_dict(),
                    "consumer_export_enabled": True,
                    "decision_engine_writeback": False,
                    "trade_execution": False,
                }
                v3_registry.setdefault("active", {})[bid] = row
                v3_registry.setdefault("history", []).append({"at": now, "event": "ACTIVATED", **row})
                _production_version(state, ProductionVersion(
                    version_id=version_id,
                    component_id=f"belief_v3:{bid}",
                    candidate_id=cid,
                    activated_at=now,
                    parent_version_id=None,
                    owner_module="EP-05",
                    materialization_route="belief_v3_registry",
                    payload_sha256=sha256(row),
                    status="ACTIVE",
                    trade_execution_authority=False,
                    metadata={"belief_id": bid, "control_belief_id": control_id},
                ))
                state["candidates"][cid]["status"] = "PROMOTED"
                _append_jsonl(audit, {"at": now, "event": "V3_PROMOTED", "candidate_id": cid, "belief_id": bid, "version_id": version_id})

    # Retirement: promoted v3 must continue to beat its matched control.
    for bid, active in list((v3_registry.get("active") or {}).items()):
        if not isinstance(active, dict):
            continue
        rows = _matched_v3_rows(belief_state, bid, str(active.get("control_belief_id") or V3_CONTROL.get(bid) or ""))
        post = _pair_metrics(rows, after=str(active.get("activated_at") or now))
        if post["n"] < 30:
            continue
        imp = _safe_float(post["brier_relative_improvement"])
        c = post["control"]; n = post["challenger"]
        ece_deg = None
        if _safe_float(n.get("ece")) is not None and _safe_float(c.get("ece")) is not None:
            ece_deg = float(n["ece"]) - float(c["ece"])
        if (imp is not None and imp <= -0.05) or (ece_deg is not None and ece_deg >= 0.05):
            version_id = str(active.get("version_id") or "")
            cid = str(active.get("candidate_id") or "unknown")
            retired = dict(active)
            retired["retired_at"] = now
            retired["retirement_reason"] = "post_promotion_incremental_degradation"
            v3_registry["active"].pop(bid, None)
            v3_registry.setdefault("history", []).append({"at": now, "event": "RETIRED", **retired, "post_metrics": post})
            event = RollbackEvent(
                rollback_id=stable_id("rollback", {"v3": bid, "version": version_id, "at": now}),
                component_id=f"belief_v3:{bid}",
                from_version_id=version_id,
                to_version_id=None,
                candidate_id=cid,
                detected_at=now,
                effective_at=now,
                reason="POST_PROMOTION_DEGRADATION",
                metrics=post,
                owner_module="EP-05",
                materialization_route="belief_v3_registry",
                automatic=True,
            )
            _rollback(state, event)
            state["retirement_actions"].append({"at": now, "component_id": event.component_id, "action": "RETIRE", "reason": event.reason})
            if cid in state["candidates"]:
                state["candidates"][cid]["status"] = "RETIRED"
            _append_jsonl(audit, {"at": now, "event": "V3_RETIRED", **event.to_dict()})


def _hypothesis(state: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    hid = str(payload.get("hypothesis_id") or stable_id("evo-hyp", payload))
    row = dict(payload)
    row["hypothesis_id"] = hid
    row.setdefault("created_at", utc_now_z())
    row.setdefault("status", "AUTO_CREATED")
    row.setdefault("production_authority", False)
    state["hypotheses"][hid] = row
    return row


def _ingest_hypothesis_challengers(
    state: dict[str, Any], raw: Mapping[str, Any] | None,
    belief_state: Mapping[str, Any], now: str, audit: Path,
) -> None:
    """Independent P2 OOS gate handoff. Never materializes any production change."""
    if not isinstance(raw, Mapping) or raw.get("schema_version") != HYPOTHESIS_CHALLENGER_SCHEMA:
        state["source_status"]["hypothesis_challengers"] = {"available": False}
        return
    authority = raw.get("authority") or {}
    if (authority.get("automatic_production_promotion") is not False or
        authority.get("frozen_forecast_mutation") is not False or
        authority.get("trade_execution") is not False):
        raise RuntimeError("P2 authority boundary violation")
    candidates = raw.get("candidates") or {}
    if not isinstance(candidates, Mapping):
        raise ValueError("invalid HUE P2 challenger registry")
    counters = {"available": True, "candidates": 0, "gate_pass": 0, "gate_hold": 0}
    for cid, frozen in sorted(candidates.items()):
        if not isinstance(frozen, Mapping) or str(frozen.get("candidate_id")) != str(cid):
            raise ValueError("P2 candidate identity mismatch")
        proposal = frozen.get("proposed_change") or {}
        if proposal.get("type") != "probability_calibration_only":
            raise ValueError("P2 unsupported methodology; cannot delegate")
        transform = proposal.get("transform") or {}
        if transform.get("type") != "logit_affine_v1":
            raise ValueError("P2 unsupported probability transform")
        if frozen.get("production_write_authority") is not False or frozen.get("auto_promotion") is not False:
            raise RuntimeError("P2 candidate cannot authorize production")
        validated = json.loads(canonical_json(frozen))
        _, conflicts = hypothesis_challenger_settle(validated, belief_state, hypothesis_challenger_utc(now))
        actual = hypothesis_challenger_gate(validated, conflicts, hypothesis_challenger_utc(now))
        declared = frozen.get("gate") or {}
        matching = (declared.get("status") == actual.get("status") and
                    int(declared.get("observed_sample") or 0) == int(actual.get("observed_sample") or 0))
        if not matching:
            gate_status = "HOLD"
            blockers = ["p2_oos_gate_reconciliation_mismatch"]
        else:
            gate_status = actual["status"]
            blockers = list(actual.get("blockers") or [])
        status = {
            "PASS": "PROMOTION_ELIGIBLE",
            "FAIL": "REJECTED",
            "HOLD": "PARKED",
            "COLLECTING": "OOS_RUNNING",
        }[gate_status]
        counters["candidates"] += 1
        counters["gate_pass"] += int(gate_status == "PASS")
        counters["gate_hold"] += int(gate_status == "HOLD")
        candidate = EvolutionCandidate(
            candidate_id=str(cid),
            candidate_type="hypothesis_probability_methodology",
            source_module="L3-P2",
            target_module="EP-05",
            component_id="belief_hypothesis:" + str(frozen.get("scope") or cid),
            created_at=str(frozen["created_at"]),
            activation_boundary=str(frozen["activation_boundary"]),
            status=status,
            evaluator_profile="hypothesis_shadow_oos_gate_v1",
            promotion_route="manual_hypothesis_probability_review",
            baseline_version="unchanged_belief_core",
            challenger_version=str(cid),
            proposed_change=dict(proposal),
            source_ref="hypothesis-challenger://" + str(cid),
            source_sha256=sha256(proposal),
            metrics={"discovery": frozen.get("discovery") or {},
                     "shadow_oos": actual.get("metrics") or {},
                     "distinct_target_dates": actual.get("distinct_target_dates"),
                     "outcome_conflicts": len(conflicts)},
            automatic_promotion_allowed=False,
            trade_execution_authority=False,
            metadata={"hypothesis_id": frozen.get("hypothesis_id"),
                      "hypothesis_version": frozen.get("hypothesis_version"),
                      "horizon_bucket": frozen.get("horizon_bucket"),
                      "production_write_authority": False},
        )
        previous = state["candidates"].get(str(cid)) or {}
        _register_candidate(state, candidate)
        p_gate = PromotionGate(
            gate_id=str(actual["gate_id"]),
            candidate_id=str(cid),
            evaluated_at=now,
            status=gate_status,
            evaluator_profile="hypothesis_shadow_oos_gate_v1",
            prospective_only=True,
            minimum_sample=50,
            observed_sample=int(actual.get("observed_sample") or 0),
            criteria=actual["criteria"],
            metrics=actual.get("metrics") or {},
            segment_checks=tuple(
                {"block": i+1, "brier_relative_improvement": value}
                for i, value in enumerate((actual.get("metrics") or {}).get("chronological_block_relative_improvements") or [])
            ),
            blockers=tuple(blockers),
            source_sha256=sha256({"candidate": cid, "gate": actual}),
        )
        _gate(state, p_gate)
        if previous.get("status") != status:
            _append_jsonl(audit, {"at": now, "event": "P2_GATE_LIFECYCLE",
                                  "candidate_id": cid, "from": previous.get("status"), "to": status,
                                  "status": gate_status})
        if gate_status == "PASS":
            if not any(a.get("candidate_id") == cid and a.get("action") == "REQUEST_OWNER_REVIEW_AND_CONTROLLED_PROMOTION"
                       for a in state["delegated_actions"] if isinstance(a, Mapping)):
                state["delegated_actions"].append({
                    "at": now, "candidate_id": cid,
                    "route": "Manual Reviewed Belief Probability Overlay",
                    "action": "REQUEST_OWNER_REVIEW_AND_CONTROLLED_PROMOTION",
                    "materialization_authority": False,
                    "reason": "P2 prospective OOS gate PASS; implementation requires separate owner approval",
                })
    state["source_status"]["hypothesis_challengers"] = counters


def _ingest_patterns(state: dict[str, Any], lab_public: Mapping[str, Any], now: str) -> None:
    patterns = lab_public.get("evidence_patterns") if isinstance(lab_public.get("evidence_patterns"), list) else []
    state["source_status"]["evidence_patterns"] = {"available": True, "patterns": len(patterns)}
    seen = set()
    for pattern in patterns:
        if not isinstance(pattern, Mapping):
            continue
        pid = str(pattern.get("pattern_id") or "")
        if not pid:
            continue
        seen.add(pid)
        status = str(pattern.get("status") or "")
        if status not in {"REPLICATED", "OOS PASS"}:
            # If a previously registered pattern becomes explicitly unstable, retire it.
            for cid, row in list(state["candidates"].items()):
                if isinstance(row, dict) and row.get("source_ref") == f"evidence-pattern://{pid}" and status == "UNSTABLE":
                    row["status"] = "RETIRED"
            continue
        holdout = pattern.get("holdout") if isinstance(pattern.get("holdout"), Mapping) else {}
        if int(holdout.get("n") or 0) < 5 or float(holdout.get("lift") or 0.0) < 0.05:
            continue
        cid = stable_id("evo-pattern", {"pattern_id": pid, "pattern_key": pattern.get("pattern_key")})
        candidate = EvolutionCandidate(
            candidate_id=cid,
            candidate_type="evidence_pattern_hypothesis",
            source_module="EP-09",
            target_module="LE-04",
            component_id=f"evidence_pattern:{pid}",
            created_at=now,
            activation_boundary=now,
            status="DISCOVERED",
            evaluator_profile="pattern_to_hypothesis_v1",
            promotion_route="hypothesis_registry",
            baseline_version=None,
            challenger_version=str(pattern.get("pattern_key") or pid),
            proposed_change={
                "belief_id": pattern.get("belief_id"),
                "atoms": list(pattern.get("atoms") or []),
                "expected_outcome": pattern.get("expected_outcome"),
                "horizon_bucket": pattern.get("horizon_bucket"),
            },
            source_ref=f"evidence-pattern://{pid}",
            source_sha256=sha256(pattern),
            metrics={"discovery": dict(pattern.get("discovery") or {}), "holdout": dict(holdout)},
            automatic_promotion_allowed=False,
            trade_execution_authority=False,
            metadata={"causal_status": "ASSOCIATION_ONLY"},
        )
        _register_candidate(state, candidate)
        _hypothesis(state, {
            "hypothesis_id": stable_id("hyp-pattern", {"pattern": pid, "key": pattern.get("pattern_key")}),
            "created_at": now,
            "source_module": "EP-09",
            "source_ref": f"evidence-pattern://{pid}",
            "kind": "evidence_pattern_modifier",
            "claim": f"Pattern {pid} may add incremental predictive information for {pattern.get('belief_id')}",
            "evidence": {
                "discovery": dict(pattern.get("discovery") or {}),
                "holdout": dict(holdout),
                "atoms": list(pattern.get("atoms") or []),
                "causal_status": "ASSOCIATION_ONLY",
            },
            "next_step": "compile_prospective_belief_modifier_challenger",
            "production_authority": False,
        })


def _read_experiences(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ingest_experience_store(state: dict[str, Any], experience_path: Path | None, now: str) -> None:
    rows = [x for x in _read_experiences(experience_path) if x.get("status") == "SETTLED" and isinstance(x.get("outcome"), Mapping)]
    state["source_status"]["experience_store"] = {"available": bool(experience_path and experience_path.exists()), "settled": len(rows)}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        engine = str(row.get("engine") or "unknown")
        action = str(row.get("action") or "UNKNOWN")
        decision = row.get("decision") if isinstance(row.get("decision"), Mapping) else {}
        regime = str(decision.get("regime") or decision.get("market_regime") or "unknown")
        groups[(engine, action, regime)].append(row)
    for (engine, action, regime), sample in sorted(groups.items()):
        if len(sample) < 20:
            continue
        returns = [
            x for x in (_safe_float((r.get("outcome") or {}).get("net_return_fraction")) for r in sample)
            if x is not None
        ]
        if len(returns) < 20:
            continue
        mean_return = statistics.mean(returns)
        negative_rate = sum(x < 0 for x in returns) / len(returns)
        stop_rate = sum(str((r.get("outcome") or {}).get("exit_reason") or "").upper() in {"STOP", "SL", "STOP_LOSS"} for r in sample) / len(sample)
        if mean_return >= 0 and negative_rate < 0.60 and stop_rate < 0.45:
            continue
        evidence = {
            "n": len(returns),
            "mean_net_return_fraction": round(mean_return, 8),
            "negative_rate": round(negative_rate, 6),
            "stop_rate": round(stop_rate, 6),
            "engine": engine,
            "action": action,
            "regime": regime,
        }
        _hypothesis(state, {
            "hypothesis_id": stable_id("hyp-exp", {"engine": engine, "action": action, "regime": regime, "evidence": evidence}),
            "created_at": now,
            "source_module": "LE-01",
            "source_ref": f"experience-store://{engine}/{action}/{regime}",
            "kind": "recurring_decision_failure",
            "claim": f"{engine} {action} decisions in regime {regime} show recurring adverse outcomes and warrant a bounded challenger",
            "evidence": evidence,
            "next_step": "compile_engine_owned_challenger",
            "production_authority": False,
        })


def _ingest_trading_regret(state: dict[str, Any], regret: Mapping[str, Any] | None, now: str) -> None:
    if not isinstance(regret, Mapping):
        state["source_status"]["trading_regret"] = {"available": False}
        return
    hypotheses = regret.get("challenger_hypotheses") if isinstance(regret.get("challenger_hypotheses"), list) else []
    state["source_status"]["trading_regret"] = {"available": True, "hypotheses": len(hypotheses)}
    for row in hypotheses:
        if not isinstance(row, Mapping):
            continue
        component = str(row.get("component") or "")
        horizon = int(row.get("horizon_sessions") or 0)
        h = _hypothesis(state, {
            "hypothesis_id": stable_id("hyp-regret", {"component": component, "horizon": horizon, "source": row.get("evidence")}),
            "created_at": now,
            "source_module": "LE-03",
            "source_ref": f"stock-regret://{component}/{horizon}",
            "kind": "opportunity_regret_component",
            "claim": f"Stock Trading component {component} may be rejecting economically superior legal opportunities",
            "evidence": dict(row.get("evidence") or {}),
            "suggested_experiment": dict(row.get("suggested_experiment") or {}),
            "next_step": "stock_trading_component_challenger",
            "production_authority": False,
        })
        if str(row.get("status") or "") == "ELIGIBLE_FOR_CHALLENGER_HOLDOUT":
            cid = stable_id("evo-stock", {"hypothesis": h["hypothesis_id"], "component": component, "horizon": horizon})
            candidate = EvolutionCandidate(
                candidate_id=cid,
                candidate_type="trading_component_replacement",
                source_module="LE-03",
                target_module="TR-04",
                component_id=f"stock_trading:{component}",
                created_at=now,
                activation_boundary=now,
                status="READY_FOR_OOS",
                evaluator_profile="stock_trading_component_gate_v1",
                promotion_route="stock_trading_component_promotion",
                baseline_version="current_champion_component",
                challenger_version=f"regret-hypothesis:{h['hypothesis_id']}",
                proposed_change=dict(row.get("suggested_experiment") or {}),
                source_ref=h["source_ref"],
                source_sha256=sha256(row),
                metrics={"regret_evidence": dict(row.get("evidence") or {})},
                automatic_promotion_allowed=False,
                trade_execution_authority=False,
                metadata={"owner_writer": "Stock Trading Component Promotion"},
            )
            _register_candidate(state, candidate)
            if not any(x.get("candidate_id") == cid for x in state["delegated_actions"] if isinstance(x, Mapping)):
                state["delegated_actions"].append({
                    "at": now,
                    "candidate_id": cid,
                    "component_id": candidate.component_id,
                    "route": "Stock Trading Component Promotion",
                    "action": "REQUEST_OWNER_CHALLENGER_AND_HOLDOUT",
                    "reason": "regret_hypothesis_eligible",
                })


def _summarize(state: dict[str, Any]) -> None:
    candidates = list(state["candidates"].values())
    gates = list(state["promotion_gates"].values())
    active_versions = [
        row
        for rows in state["production_versions"].values()
        for row in rows if isinstance(row, Mapping) and row.get("status") == "ACTIVE"
    ]
    state["summary"] = {
        "candidates_total": len(candidates),
        "oos_running": sum(x.get("status") in {"OOS_RUNNING", "READY_FOR_OOS"} for x in candidates if isinstance(x, Mapping)),
        "promotion_eligible": sum(x.get("status") == "PROMOTION_ELIGIBLE" for x in candidates if isinstance(x, Mapping)),
        "promoted": sum(x.get("status") == "PROMOTED" for x in candidates if isinstance(x, Mapping)),
        "retired_or_rolled_back": sum(x.get("status") in {"RETIRED", "ROLLED_BACK"} for x in candidates if isinstance(x, Mapping)),
        "gate_pass": sum(x.get("status") == "PASS" for x in gates if isinstance(x, Mapping)),
        "active_production_versions": len(active_versions),
        "hypotheses": len(state["hypotheses"]),
        "delegated_actions": len(state["delegated_actions"]),
        "rollback_events": len(state["rollback_events"]),
        "retirement_actions": len(state["retirement_actions"]),
    }


def _public(state: Mapping[str, Any]) -> dict[str, Any]:
    candidates = []
    for row in state.get("candidates", {}).values():
        if not isinstance(row, Mapping):
            continue
        candidates.append({
            "candidate_id": row.get("candidate_id"),
            "candidate_type": row.get("candidate_type"),
            "source_module": row.get("source_module"),
            "target_module": row.get("target_module"),
            "component_id": row.get("component_id"),
            "status": row.get("status"),
            "evaluator_profile": row.get("evaluator_profile"),
            "promotion_route": row.get("promotion_route"),
            "created_at": row.get("created_at"),
            "activation_boundary": row.get("activation_boundary"),
            "metrics": row.get("metrics"),
            "automatic_promotion_allowed": row.get("automatic_promotion_allowed"),
        })
    gates = [
        {
            "candidate_id": x.get("candidate_id"),
            "status": x.get("status"),
            "observed_sample": x.get("observed_sample"),
            "minimum_sample": x.get("minimum_sample"),
            "blockers": x.get("blockers"),
            "segment_checks": x.get("segment_checks"),
        }
        for x in state.get("promotion_gates", {}).values()
        if isinstance(x, Mapping)
    ]
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "generated_at": state.get("updated_at"),
        "authority": dict(AUTHORITY),
        "summary": dict(state.get("summary") or {}),
        "candidates": sorted(candidates, key=lambda x: (str(x.get("status")), str(x.get("component_id")))),
        "promotion_gates": gates,
        "production_versions": state.get("production_versions", {}),
        "rollback_events": list(state.get("rollback_events") or [])[-30:],
        "hypotheses": list(state.get("hypotheses", {}).values())[-50:],
        "delegated_actions": list(state.get("delegated_actions") or [])[-30:],
        "retirement_actions": list(state.get("retirement_actions") or [])[-30:],
        "source_status": dict(state.get("source_status") or {}),
    }


def run(
    *,
    state_path: Path,
    public_path: Path,
    audit_path: Path,
    belief_state_path: Path | None = None,
    belief_closed_loop_path: Path | None = None,
    hypothesis_challengers_path: Path | None = None,
    decision_lab_public_path: Path | None = None,
    experience_store_path: Path | None = None,
    trading_regret_path: Path | None = None,
    belief_policy_path: Path = DEFAULT_BELIEF_POLICY,
    v3_registry_path: Path = DEFAULT_V3_REGISTRY,
    now: str | None = None,
) -> dict[str, Any]:
    now = now or utc_now_z()
    state = _load_state(state_path)
    policy = _belief_policy(belief_policy_path)
    v3_registry = _v3_registry(v3_registry_path)
    belief_state = _read_json(belief_state_path, {}) if belief_state_path else {}
    closed_loop = _read_json(belief_closed_loop_path, {}) if belief_closed_loop_path else {}
    hypothesis_challengers = _read_json(hypothesis_challengers_path, {}) if hypothesis_challengers_path else {}
    lab_public = _read_json(decision_lab_public_path, {}) if decision_lab_public_path else {}
    regret = _read_json(trading_regret_path, None) if trading_regret_path else None

    if isinstance(belief_state, Mapping) and belief_state:
        if isinstance(closed_loop, Mapping) and closed_loop:
            _ingest_belief_calibration(state, belief_state, closed_loop, policy, now, audit_path)
        else:
            state["source_status"]["belief_calibration"] = {"available": False, "reason": "closed_loop_state_missing"}
        _ingest_v3(state, belief_state, v3_registry, now, audit_path)
    else:
        state["source_status"]["belief_calibration"] = {"available": False, "reason": "belief_state_missing"}
        state["source_status"]["belief_v3"] = {"available": False, "reason": "belief_state_missing"}

    _ingest_hypothesis_challengers(state, hypothesis_challengers, belief_state, now, audit_path)

    if isinstance(lab_public, Mapping) and lab_public:
        _ingest_patterns(state, lab_public, now)
    else:
        state["source_status"]["evidence_patterns"] = {"available": False}

    _ingest_experience_store(state, experience_store_path, now)
    _ingest_trading_regret(state, regret, now)

    policy["updated_at"] = now
    v3_registry["updated_at"] = now
    state["updated_at"] = now
    _summarize(state)

    _atomic_json(belief_policy_path, policy)
    _atomic_json(v3_registry_path, v3_registry)
    _atomic_json(state_path, state)
    public = _public(state)
    _atomic_json(public_path, public)
    return public


def main() -> int:
    ap = argparse.ArgumentParser(description="Run BriefRooms Evolution Controller")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    ap.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    ap.add_argument("--belief-state", type=Path)
    ap.add_argument("--belief-closed-loop", type=Path)
    ap.add_argument("--hypothesis-challengers", type=Path)
    ap.add_argument("--decision-lab-public", type=Path, default=Path("data/investments/decision_lab_public.json"))
    ap.add_argument("--experience-store", type=Path)
    ap.add_argument("--trading-regret", type=Path)
    ap.add_argument("--belief-policy", type=Path, default=DEFAULT_BELIEF_POLICY)
    ap.add_argument("--v3-registry", type=Path, default=DEFAULT_V3_REGISTRY)
    ap.add_argument("--now")
    args = ap.parse_args()
    payload = run(
        state_path=args.state,
        public_path=args.public,
        audit_path=args.audit,
        belief_state_path=args.belief_state,
        belief_closed_loop_path=args.belief_closed_loop,
        hypothesis_challengers_path=args.hypothesis_challengers,
        decision_lab_public_path=args.decision_lab_public,
        experience_store_path=args.experience_store,
        trading_regret_path=args.trading_regret,
        belief_policy_path=args.belief_policy,
        v3_registry_path=args.v3_registry,
        now=args.now,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
