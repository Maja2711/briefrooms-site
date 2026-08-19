#!/usr/bin/env python3
"""Prospective WES-SPX Engine-Belief calibration and WITH/WITHOUT evaluator.

PR #9 consumes only frozen PR #8 WES-Belief observations. It measures whether
agreement/conflict predicts WES outcomes and WES-vs-V5 incremental alpha. A
predeclared research-only WITH BELIEF variant can attenuate risk on conflicts,
but it never changes production WES direction, entry, TP/SL, score, sizing,
exposure, veto, learning, execution, or policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any, Dict, Mapping, Optional, Sequence

SCHEMA_VERSION = "wes-spx-engine-belief-calibration-v1"
REPORT_VERSION = "wes-spx-engine-belief-calibration-report-v1"
MODE = "research_shadow"
MAX_CONTRACT_CAPTURE_DELAY_MINUTES = 15.0
MAX_CAPTURE_CLOCK_SKEW_MINUTES = 5.0
MAX_HYPOTHETICAL_RISK_ATTENUATION = 0.10
MAX_HYPOTHETICAL_SCORE_MODIFIER_POINTS = 2.0
MIN_DESCRIPTIVE_N = 12
MIN_RELATIONSHIP_ANALYSIS_N = 30


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "direction_change": False,
        "entry_change": False,
        "tp_sl_change": False,
        "score_change": False,
        "sizing_change": False,
        "exposure_change": False,
        "veto": False,
        "candidate_ranking_change": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence": False,
        "historical_backfill": False,
    }


def _assert_safety() -> None:
    enabled = [key for key, value in safety_controls().items() if value is not False]
    if enabled:
        raise RuntimeError("PR #9 safety invariant violated: " + ",".join(enabled))


def evaluation_policy() -> Dict[str, Any]:
    return {
        "with_without_evaluation_enabled": True,
        "hypothetical_overlay_only": True,
        "production_modifier_proposed": False,
        "max_conflict_risk_attenuation": MAX_HYPOTHETICAL_RISK_ATTENUATION,
        "max_score_modifier_points_telemetry_only": MAX_HYPOTHETICAL_SCORE_MODIFIER_POINTS,
        "agreement_can_increase_exposure": False,
        "direction_change_allowed": False,
        "tp_sl_change_allowed": False,
        "veto_allowed": False,
    }


def _initial_state(now: datetime) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "mode": MODE, "activated_at": _iso_z(now), "safety_controls": safety_controls(), "contracts_total": 0, "settled_pairs": 0}


def _relationship_kind(value: str) -> str:
    text = str(value or "UNAVAILABLE")
    if "AGREEMENT" in text:
        return "AGREEMENT"
    if "CONFLICT" in text:
        return "CONFLICT"
    if text == "NEUTRAL":
        return "NEUTRAL"
    return "UNAVAILABLE"


def _contract_capture_status(bridge_record: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    captured_at = _dt(bridge_record.get("captured_at"))
    if captured_at is None:
        return {"eligible": False, "status": "missed_not_reconstructed", "reason": "bridge_capture_timestamp_missing", "delay_minutes": None}
    delay = (now.astimezone(timezone.utc) - captured_at).total_seconds() / 60.0
    if delay < -MAX_CAPTURE_CLOCK_SKEW_MINUTES:
        return {"eligible": False, "status": "missed_not_reconstructed", "reason": "contract_capture_precedes_bridge_beyond_clock_skew", "delay_minutes": round(delay, 3)}
    if delay > MAX_CONTRACT_CAPTURE_DELAY_MINUTES:
        return {"eligible": False, "status": "missed_not_reconstructed", "reason": "calibration_contract_window_missed", "delay_minutes": round(delay, 3)}
    return {"eligible": True, "status": "frozen_point_in_time", "reason": "captured_inside_pr9_contract_window", "delay_minutes": round(delay, 3)}


def _risk_scale_and_score_modifier(relationship: Mapping[str, Any]) -> tuple[float, float]:
    cls = str(relationship.get("class") or "UNAVAILABLE")
    strength = max(0.0, min(1.0, float(_finite(relationship.get("strength")) or 0.0)))
    kind = _relationship_kind(cls)
    if kind == "CONFLICT":
        risk_scale = 1.0 - MAX_HYPOTHETICAL_RISK_ATTENUATION * strength
        score_modifier = -MAX_HYPOTHETICAL_SCORE_MODIFIER_POINTS * strength
    elif kind == "AGREEMENT":
        risk_scale = 1.0
        score_modifier = MAX_HYPOTHETICAL_SCORE_MODIFIER_POINTS * strength
    else:
        risk_scale = 1.0
        score_modifier = 0.0
    return round(risk_scale, 8), round(score_modifier, 8)


def make_contract(bridge_record: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    if bridge_record.get("engine_belief_calibration_eligible") is not True:
        return {"status": "not_eligible", "reason": "pr8_record_not_calibration_eligible", "contract": None}
    capture = _contract_capture_status(bridge_record, now)
    if not capture["eligible"]:
        return {"status": capture["status"], "reason": capture["reason"], "contract": None}
    wes = bridge_record.get("wes") if isinstance(bridge_record.get("wes"), Mapping) else {}
    belief = bridge_record.get("belief_state") if isinstance(bridge_record.get("belief_state"), Mapping) else {}
    rel = bridge_record.get("relationship") if isinstance(bridge_record.get("relationship"), Mapping) else {}
    direction = str(wes.get("direction") or "")
    if direction not in {"long", "short"} or not wes.get("actionable") or not belief.get("available"):
        return {"status": "not_eligible", "reason": "frozen_wes_or_belief_state_incomplete", "contract": None}
    risk_scale, score_modifier = _risk_scale_and_score_modifier(rel)
    raw_score = _finite(wes.get("raw_score"))
    hypothetical_score = raw_score + score_modifier if raw_score is not None else None
    payload = {
        "schema_version": SCHEMA_VERSION,
        "record_id": bridge_record.get("record_id"),
        "decision_id": bridge_record.get("decision_id"),
        "week_id": bridge_record.get("week_id"),
        "instrument_id": "sp500_futures",
        "frozen_at": _iso_z(now),
        "bridge_record_sha256": canonical_sha256(bridge_record),
        "without_belief": {"direction": direction, "strategy_id": wes.get("strategy_id"), "raw_score": wes.get("raw_score"), "entry_class": wes.get("entry_class"), "entry_price": wes.get("entry_price"), "entry_captured_at": wes.get("entry_captured_at"), "risk_plan_sha256": canonical_sha256(wes.get("risk_plan") or {}), "risk_scale": 1.0},
        "with_belief_hypothetical": {"direction": direction, "risk_scale": risk_scale, "score_modifier_points_telemetry_only": score_modifier, "hypothetical_raw_score_telemetry_only": round(hypothetical_score, 8) if hypothetical_score is not None else None, "direction_changed": False, "entry_changed": False, "tp_sl_changed": False, "veto_applied": False, "production_modifier_proposed": False},
        "belief": {"forecast_set_id": belief.get("forecast_set_id"), "forecast_at": belief.get("forecast_at"), "stance": belief.get("stance"), "confidence": belief.get("confidence"), "risk_on_probability_mean": belief.get("risk_on_probability_mean"), "snapshot_sha256": belief.get("snapshot_sha256")},
        "relationship": deepcopy(dict(rel)),
        "policy": evaluation_policy(),
        "decision_influence": False,
    }
    payload["contract_sha256"] = canonical_sha256({k: v for k, v in payload.items() if k != "contract_sha256"})
    return {"status": "frozen", "reason": "prospective_pr8_based_calibration_contract", "contract": payload}


def _source_by_decision(wes_source: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(row.get("decision_id")): row for row in (wes_source.get("records") or []) if isinstance(row, Mapping) and row.get("decision_id")}


def _source_matches_contract(contract: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    actual = source.get("wes_actual") if isinstance(source.get("wes_actual"), Mapping) else {}
    frozen = contract.get("without_belief") if isinstance(contract.get("without_belief"), Mapping) else {}
    checks = [str(source.get("decision_id") or "") == str(contract.get("decision_id") or ""), str(actual.get("direction") or "") == str(frozen.get("direction") or ""), str(actual.get("strategy_id") or "") == str(frozen.get("strategy_id") or ""), str(actual.get("entry_captured_at") or "") == str(frozen.get("entry_captured_at") or "")]
    a = _finite(actual.get("entry_price")); b = _finite(frozen.get("entry_price"))
    if a is not None or b is not None:
        checks.append(a is not None and b is not None and abs(a - b) <= max(1e-9, abs(b) * 1e-9))
    return all(checks)


def settle_contract(contract: Mapping[str, Any], source: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    if not _source_matches_contract(contract, source):
        return {"status": "source_identity_mismatch", "decision_influence": False}
    outcome = source.get("outcome") if isinstance(source.get("outcome"), Mapping) else {}
    wes_net = _finite(outcome.get("wes_net_result_percent"))
    if wes_net is None:
        return {"status": "pending_wes_outcome", "decision_influence": False}
    v5_net = _finite(outcome.get("v5_counterfactual_net_result_percent"))
    incremental = _finite(outcome.get("incremental_wes_vs_v5_percent"))
    risk_scale = float((contract.get("with_belief_hypothetical") or {}).get("risk_scale") or 1.0)
    with_net = wes_net * risk_scale
    delta = with_net - wes_net
    with_vs_v5 = with_net - v5_net if v5_net is not None else None
    return {"status": "resolved", "settled_at": _iso_z(now), "closed_at": outcome.get("closed_at"), "exit_reason": outcome.get("exit_reason"), "without_belief_wes_net_percent": round(wes_net, 8), "with_belief_hypothetical_net_percent": round(with_net, 8), "delta_pnl_percent": round(delta, 8), "risk_scale": round(risk_scale, 8), "v5_counterfactual_net_percent": round(v5_net, 8) if v5_net is not None else None, "wes_vs_v5_incremental_alpha_percent": round(incremental, 8) if incremental is not None else None, "with_belief_vs_v5_incremental_alpha_percent": round(with_vs_v5, 8) if with_vs_v5 is not None else None, "relationship_class": (contract.get("relationship") or {}).get("class"), "relationship_strength": (contract.get("relationship") or {}).get("strength"), "decision_influence": False}


def _compound(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    wealth = 1.0
    for value in values:
        wealth *= 1.0 + value / 100.0
    return round((wealth - 1.0) * 100.0, 8)


def _max_drawdown(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    wealth = 1.0; peak = 1.0; max_dd = 0.0
    for value in values:
        wealth *= 1.0 + value / 100.0
        peak = max(peak, wealth)
        max_dd = min(max_dd, wealth / peak - 1.0)
    return round(max_dd * 100.0, 8)


def _sharpe(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    sigma = stdev(values)
    if sigma <= 1e-12:
        return None
    return round(fmean(values) / sigma * math.sqrt(52.0), 8)


def _stats(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    without = [float(x) for x in (_finite((r.get("settlement") or {}).get("without_belief_wes_net_percent")) for r in rows) if x is not None]
    with_b = [float(x) for x in (_finite((r.get("settlement") or {}).get("with_belief_hypothetical_net_percent")) for r in rows) if x is not None]
    delta = [float(x) for x in (_finite((r.get("settlement") or {}).get("delta_pnl_percent")) for r in rows) if x is not None]
    inc = [float(x) for x in (_finite((r.get("settlement") or {}).get("wes_vs_v5_incremental_alpha_percent")) for r in rows) if x is not None]
    with_inc = [float(x) for x in (_finite((r.get("settlement") or {}).get("with_belief_vs_v5_incremental_alpha_percent")) for r in rows) if x is not None]
    return {"settled_pairs": len(without), "mean_wes_net_percent": round(fmean(without), 8) if without else None, "win_rate": round(sum(v > 0 for v in without) / len(without), 6) if without else None, "mean_with_belief_net_percent": round(fmean(with_b), 8) if with_b else None, "mean_delta_pnl_percent": round(fmean(delta), 8) if delta else None, "median_delta_pnl_percent": round(median(delta), 8) if delta else None, "worst_delta_pnl_percent": round(min(delta), 8) if delta else None, "cumulative_without_belief_percent": _compound(without), "cumulative_with_belief_percent": _compound(with_b), "max_drawdown_without_belief_percent": _max_drawdown(without), "max_drawdown_with_belief_percent": _max_drawdown(with_b), "sharpe_without_belief": _sharpe(without), "sharpe_with_belief": _sharpe(with_b), "resolved_wes_v5_pairs": len(inc), "mean_wes_vs_v5_incremental_alpha_percent": round(fmean(inc), 8) if inc else None, "mean_with_belief_vs_v5_incremental_alpha_percent": round(fmean(with_inc), 8) if with_inc else None}


def build_report(records: Sequence[Mapping[str, Any]], state: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    settled = [r for r in records if (r.get("settlement") or {}).get("status") == "resolved"]
    by_rel: Dict[str, list[Mapping[str, Any]]] = defaultdict(list); by_strategy: Dict[str, list[Mapping[str, Any]]] = defaultdict(list); by_entry: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in settled:
        contract = row.get("contract") or {}; rel = str((contract.get("relationship") or {}).get("class") or "UNAVAILABLE"); by_rel[rel].append(row)
        frozen = contract.get("without_belief") or {}; by_strategy[str(frozen.get("strategy_id") or "unknown")].append(row); by_entry[str(frozen.get("entry_class") or "unknown")].append(row)
    conflict = [r for r in settled if _relationship_kind(str(((r.get("contract") or {}).get("relationship") or {}).get("class"))) == "CONFLICT"]
    agreement = [r for r in settled if _relationship_kind(str(((r.get("contract") or {}).get("relationship") or {}).get("class"))) == "AGREEMENT"]
    conflict_negative = sum(float((r.get("settlement") or {}).get("without_belief_wes_net_percent") or 0.0) < 0 for r in conflict)
    agreement_positive = sum(float((r.get("settlement") or {}).get("without_belief_wes_net_percent") or 0.0) > 0 for r in agreement)
    n = len(settled)
    status = "collecting_prospective_pairs" if n == 0 else "warmup_insufficient_evidence" if n < MIN_DESCRIPTIVE_N else "descriptive_analysis_available_not_policy_authorized" if n < MIN_RELATIONSHIP_ANALYSIS_N else "relationship_analysis_available_not_policy_authorized"
    return {"schema_version": REPORT_VERSION, "report_name": "WES_SPX_ENGINE_BELIEF_CALIBRATION_REPORT", "generated_at": _iso_z(now), "mode": MODE, "safety_controls": safety_controls(), "evaluation_policy": evaluation_policy(), "activation": {"activated_at": state.get("activated_at"), "historical_backfill_allowed": False, "prospective_only": True, "contract_capture_window_minutes": MAX_CONTRACT_CAPTURE_DELAY_MINUTES}, "sample": {"contracts_total": len(records), "settled_pairs": n, "effective_samples": float(n), "minimum_before_descriptive_analysis": MIN_DESCRIPTIVE_N, "minimum_before_relationship_analysis": MIN_RELATIONSHIP_ANALYSIS_N, "status": status}, "overall": _stats(settled), "by_relationship": {k: _stats(v) for k, v in sorted(by_rel.items())}, "by_strategy": {k: _stats(v) for k, v in sorted(by_strategy.items())}, "by_entry_class": {k: _stats(v) for k, v in sorted(by_entry.items())}, "relationship_diagnostics": {"conflict_pairs": len(conflict), "conflict_warning_rate_for_negative_wes_outcome": round(conflict_negative / len(conflict), 6) if conflict else None, "agreement_pairs": len(agreement), "agreement_confirmation_rate_for_positive_wes_outcome": round(agreement_positive / len(agreement), 6) if agreement else None, "wes_v5_incremental_alpha_grouped_by_relationship": True}, "interpretation": {"without_belief": "Observed WES net result from the existing governed WES ledger.", "with_belief": "Research-only risk sensitivity: conflicts may attenuate notional by at most 10%; agreements never increase exposure.", "score_modifier": "A signed max ±2 point score modifier is recorded as telemetry only and never changes WES policy.", "v5": "Existing prospectively frozen V5 counterfactual only; PR #9 does not create or rewrite V5 replay.", "promotion": "No result in this report authorizes bounded influence or policy changes."}, "active_decision_influence": False, "bounded_influence_enabled": False}


def run_calibration(calibration_dir: Path, bridge_dir: Path, wes_source_path: Path, now: datetime) -> Dict[str, Any]:
    _assert_safety(); calibration_dir.mkdir(parents=True, exist_ok=True)
    state_path = calibration_dir / "calibration_state.json"; records_path = calibration_dir / "calibration_records.jsonl"; report_path = calibration_dir / "WES_SPX_ENGINE_BELIEF_CALIBRATION_REPORT.json"
    existed = state_path.exists(); state = _read_json(state_path, _initial_state(now)); activated_at = _dt(state.get("activated_at")) or now
    records = _load_jsonl(records_path); by_bridge_id = {str(r.get("record_id")): r for r in records}; bridge_records = _load_jsonl(bridge_dir / "engine_belief_observations.jsonl")
    source = _read_json(wes_source_path, {}); source_map = _source_by_decision(source); contracts_added = 0; settlements_added = 0
    if existed:
        for bridge_record in bridge_records:
            bridge_id = str(bridge_record.get("record_id") or ""); captured_at = _dt(bridge_record.get("captured_at"))
            if not bridge_id or captured_at is None or captured_at < activated_at or bridge_id in by_bridge_id:
                continue
            result = make_contract(bridge_record, now)
            row = {"record_id": bridge_id, "decision_id": bridge_record.get("decision_id"), "week_id": bridge_record.get("week_id"), "created_at": _iso_z(now), "contract_status": result["status"], "contract_reason": result["reason"], "contract": result["contract"], "settlement": {"status": "pending"} if result["contract"] else {"status": "not_eligible"}, "decision_influence": False}
            records.append(row); by_bridge_id[bridge_id] = row
            if result["contract"] is not None: contracts_added += 1
        for row in records:
            contract = row.get("contract")
            if not isinstance(contract, Mapping) or (row.get("settlement") or {}).get("status") == "resolved":
                continue
            source_row = source_map.get(str(row.get("decision_id") or ""))
            if source_row is None:
                row["settlement"] = {"status": "pending_wes_source", "decision_influence": False}; continue
            settlement = settle_contract(contract, source_row, now)
            if settlement.get("status") == "resolved": settlements_added += 1
            row["settlement"] = settlement
    records.sort(key=lambda r: (str(r.get("week_id") or ""), str(r.get("created_at") or ""), str(r.get("record_id") or ""))); _write_jsonl(records_path, records)
    settled_pairs = sum((r.get("settlement") or {}).get("status") == "resolved" for r in records)
    state.update({"schema_version": SCHEMA_VERSION, "mode": MODE, "safety_controls": safety_controls(), "last_run_at": _iso_z(now), "contracts_total": sum(isinstance(r.get("contract"), Mapping) for r in records), "settled_pairs": settled_pairs, "last_status": "activated_waiting_for_prospective_pr8_record" if not existed else "calibration_updated"}); _write_json(state_path, state)
    report = build_report(records, state, now); _write_json(report_path, report)
    return {"status": state["last_status"], "contracts_total": state["contracts_total"], "contracts_added": contracts_added, "settled_pairs": settled_pairs, "settlements_added": settlements_added, "sample_status": report["sample"]["status"], "active_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WES-SPX Engine-Belief calibration")
    parser.add_argument("--calibration-dir", required=True); parser.add_argument("--bridge-dir", required=True); parser.add_argument("--wes-source", required=True); parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args(); now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None: raise ValueError("invalid --now")
    result = run_calibration(Path(args.calibration_dir), Path(args.bridge_dir), Path(args.wes_source), now); print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
