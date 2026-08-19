#!/usr/bin/env python3
"""Prospective read-only WES-SPX <-> Belief Core bridge.

PR #8 freezes one point-in-time Engine-Belief Observation for each new
prospective WES SPX decision. It never changes WES/V5 decisions, direction,
entry, TP/SL, score, sizing/exposure, vetoes, learning, execution, or Belief
Core state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import brace_spx_belief_bridge as belief_bridge

SCHEMA_VERSION = "wes-spx-belief-readonly-v1"
REPORT_VERSION = "wes-spx-belief-bridge-report-v1"
MODE = "shadow"
SPX_ID = "sp500_futures"
MAX_CAPTURE_DELAY_MINUTES = 60.0
MAX_CAPTURE_CLOCK_SKEW_MINUTES = 5.0
STRONG_RELATIONSHIP_CONFIDENCE = 0.65


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


def _append_jsonl_unique(path: Path, rows: Iterable[Mapping[str, Any]], key: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            value = payload.get(key)
            if value:
                existing.add(str(value))
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            value = str(row.get(key) or "")
            if not value or value in existing:
                continue
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            existing.add(value)
            written += 1
    return written


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


def controls() -> Dict[str, bool]:
    return {
        "belief_influence": False,
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
        "bounded_modifier": False,
    }


def _assert_controls_hard_off(payload: Mapping[str, Any]) -> None:
    enabled = [key for key, value in payload.items() if value is not False]
    if enabled:
        raise RuntimeError("WES-SPX/Belief bridge safety invariant violated: " + ",".join(enabled))


def _wes_state(source_record: Mapping[str, Any]) -> Dict[str, Any]:
    actual = source_record.get("wes_actual") if isinstance(source_record.get("wes_actual"), Mapping) else {}
    direction = str(actual.get("direction") or "").lower()
    raw_score = _finite(actual.get("raw_score"))
    decision_at = source_record.get("decision_at")
    decision_type = str(source_record.get("decision_type") or "")
    source_first = source_record.get("first_captured_at")
    score_strength = min(1.0, abs(raw_score) / 100.0) if raw_score is not None else 0.0
    available = bool(
        str(source_record.get("instrument_id") or "") == SPX_ID
        and _dt(decision_at) is not None
        and direction in {"long", "short"}
        and isinstance(actual, Mapping)
        and bool(actual)
    )
    return {
        "available": available,
        "reason": "frozen_wes_spx_decision" if available else "wes_spx_actionable_decision_unavailable",
        "decision_id": source_record.get("decision_id"),
        "week_id": source_record.get("week_id"),
        "decision_type": decision_type,
        "decision_at": decision_at,
        "source_first_captured_at": source_first,
        "direction": direction if direction else "unavailable",
        "strategy_id": actual.get("strategy_id"),
        "raw_score": round(raw_score, 6) if raw_score is not None else None,
        "score_strength": round(score_strength, 6),
        "entry_class": actual.get("entry_class"),
        "entry_price": actual.get("entry_price"),
        "entry_captured_at": actual.get("entry_captured_at"),
        "risk_plan": deepcopy(actual.get("risk_plan")) if isinstance(actual.get("risk_plan"), Mapping) else None,
        "actionable": decision_type in {"entered_position", "authorized_trigger"},
    }


def _capture_status(source_record: Mapping[str, Any], captured_at: datetime) -> Dict[str, Any]:
    source_at = _dt(source_record.get("first_captured_at"))
    if source_at is None:
        return {"eligible": False, "status": "missed_not_reconstructed", "reason": "source_capture_timestamp_missing", "delay_minutes": None}
    delay = (captured_at.astimezone(timezone.utc) - source_at).total_seconds() / 60.0
    if delay < -MAX_CAPTURE_CLOCK_SKEW_MINUTES:
        return {"eligible": False, "status": "missed_not_reconstructed", "reason": "bridge_capture_precedes_wes_source_beyond_clock_skew", "delay_minutes": round(delay, 3)}
    if delay > MAX_CAPTURE_DELAY_MINUTES:
        return {"eligible": False, "status": "missed_not_reconstructed", "reason": "wes_belief_capture_window_missed", "delay_minutes": round(delay, 3)}
    return {"eligible": True, "status": "frozen_point_in_time", "reason": "captured_inside_wes_belief_window", "delay_minutes": round(delay, 3)}


def relationship(wes: Mapping[str, Any], belief: Mapping[str, Any]) -> Dict[str, Any]:
    if not wes.get("available"):
        return {"class": "UNAVAILABLE", "strength": 0.0, "alpha_eligible": False, "reason": wes.get("reason")}
    if not belief.get("available"):
        return {"class": "UNAVAILABLE", "strength": 0.0, "alpha_eligible": False, "reason": belief.get("reason")}
    if not wes.get("actionable"):
        return {"class": "UNAVAILABLE", "strength": 0.0, "alpha_eligible": False, "reason": "non_actionable_wes_observation"}
    direction = str(wes.get("direction") or "unavailable")
    stance = str(belief.get("stance") or "unavailable")
    strength = min(float(wes.get("score_strength") or 0.0), float(belief.get("confidence") or 0.0))
    if stance == "neutral":
        return {"class": "NEUTRAL", "strength": round(strength, 6), "alpha_eligible": True, "reason": "belief_state_neutral"}
    agrees = (direction == "long" and stance == "risk_on") or (direction == "short" and stance == "defensive")
    conflicts = (direction == "long" and stance == "defensive") or (direction == "short" and stance == "risk_on")
    if agrees:
        cls = "STRONG_AGREEMENT" if strength >= STRONG_RELATIONSHIP_CONFIDENCE else "WEAK_AGREEMENT"
    elif conflicts:
        cls = "STRONG_CONFLICT" if strength >= STRONG_RELATIONSHIP_CONFIDENCE else "WEAK_CONFLICT"
    else:
        cls = "UNAVAILABLE"
    return {"class": cls, "strength": round(strength, 6), "alpha_eligible": cls != "UNAVAILABLE", "reason": "read_only_wes_belief_relationship"}


def _initial_state(now: datetime) -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "mode": MODE, "activated_at": _iso_z(now), "controls": controls(), "last_capture_at": None, "records_total": 0}


def _load_observations(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _report(observations: list[Mapping[str, Any]], state: Mapping[str, Any], generated_at: datetime) -> Dict[str, Any]:
    classes = Counter(str((row.get("relationship") or {}).get("class") or "UNAVAILABLE") for row in observations)
    eligible = [row for row in observations if bool(row.get("engine_belief_calibration_eligible"))]
    return {
        "schema_version": REPORT_VERSION,
        "report_name": "WES_SPX_BELIEF_BRIDGE_REPORT",
        "generated_at": _iso_z(generated_at),
        "mode": MODE,
        "controls": controls(),
        "activation": {"activated_at": state.get("activated_at"), "historical_backfill_allowed": False, "prospective_only": True, "max_capture_delay_minutes": MAX_CAPTURE_DELAY_MINUTES},
        "coverage": {"spx": "full_bridge_scope", "eurusd": "deferred_partial_coverage", "btc": "deferred_partial_coverage"},
        "sample": {"observations_total": len(observations), "engine_belief_calibration_eligible": len(eligible), "relationship_classes": dict(sorted(classes.items()))},
        "decision_influence": False,
        "bounded_modifier": False,
        "status": "collecting_prospective_wes_spx_belief_observations",
    }


def run_bridge(bridge_dir: Path, wes_source_path: Path, belief_state_path: Path, now: datetime) -> Dict[str, Any]:
    _assert_controls_hard_off(controls())
    bridge_dir.mkdir(parents=True, exist_ok=True)
    state_path = bridge_dir / "bridge_state.json"
    observations_path = bridge_dir / "engine_belief_observations.jsonl"
    report_path = bridge_dir / "WES_SPX_BELIEF_BRIDGE_REPORT.json"
    existed = state_path.exists()
    state = _read_json(state_path, _initial_state(now))
    _assert_controls_hard_off(state.get("controls") or controls())
    activated_at = _dt(state.get("activated_at")) or now
    source = _read_json(wes_source_path, {})
    belief_source = _read_json(belief_state_path, {})
    source_rows = [row for row in (source.get("records") or []) if isinstance(row, Mapping)]
    existing_ids = {str(row.get("record_id")) for row in _load_observations(observations_path)}
    written = 0
    missed = 0
    if not existed:
        status = "activated_waiting_for_prospective_wes_decision"
    else:
        status = "no_new_prospective_wes_decision"
        new_rows = []
        for source_record in source_rows:
            decision_id = str(source_record.get("decision_id") or "")
            if not decision_id:
                continue
            record_id = "wes-spx-belief-" + hashlib.sha256(decision_id.encode("utf-8")).hexdigest()[:20]
            if record_id in existing_ids:
                continue
            decision_at = _dt(source_record.get("decision_at"))
            source_first = _dt(source_record.get("first_captured_at"))
            if decision_at is None or source_first is None or decision_at < activated_at or source_first < activated_at:
                continue
            wes = _wes_state(source_record)
            capture = _capture_status(source_record, now)
            if capture["eligible"] and wes.get("available"):
                belief = belief_bridge.select_frozen_belief_state(belief_source, decision_at)
                rel = relationship(wes, belief)
            else:
                belief = {"available": False, "stance": "unavailable", "confidence": 0.0, "reason": capture["reason"] if not capture["eligible"] else wes.get("reason"), "beliefs": []}
                rel = {"class": "UNAVAILABLE", "strength": 0.0, "alpha_eligible": False, "reason": belief["reason"]}
                if not capture["eligible"]:
                    missed += 1
            eligible = bool(capture["eligible"] and wes.get("available") and belief.get("available") and rel.get("alpha_eligible"))
            new_rows.append({
                "record_id": record_id,
                "decision_id": decision_id,
                "week_id": source_record.get("week_id"),
                "instrument_id": SPX_ID,
                "captured_at": _iso_z(now),
                "mode": MODE,
                "capture": capture,
                "point_in_time": {"wes_decision_at": wes.get("decision_at"), "belief_as_of": belief.get("forecast_at"), "prospective_after_activation": True, "historical_backfill": False},
                "wes": wes,
                "belief_state": belief,
                "relationship": rel,
                "engine_belief_calibration_eligible": eligible,
                "with_without_evaluation_enabled": False,
                "decision_influence": False,
                "bounded_modifier_applied": False,
                "controls": controls(),
                "provenance": {"wes_source_record_sha256": canonical_sha256(source_record), "wes_source_ledger_sha256": canonical_sha256(source), "belief_source_sha256": canonical_sha256(belief_source)},
            })
        written = _append_jsonl_unique(observations_path, new_rows, "record_id")
        if written:
            status = "captured"
            state["last_capture_at"] = _iso_z(now)
    observations = _load_observations(observations_path)
    state.update({"schema_version": SCHEMA_VERSION, "mode": MODE, "controls": controls(), "records_total": len(observations), "last_run_at": _iso_z(now), "last_status": status})
    _write_json(state_path, state)
    report = _report(observations, state, now)
    _write_json(report_path, report)
    return {"status": status, "records_total": len(observations), "written": written, "missed": missed, "engine_belief_calibration_eligible": report["sample"]["engine_belief_calibration_eligible"], "relationship_classes": report["sample"]["relationship_classes"], "decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prospective WES-SPX / Belief Core read-only bridge")
    parser.add_argument("--bridge-dir", required=True)
    parser.add_argument("--wes-source", required=True)
    parser.add_argument("--belief-state", required=True)
    parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args()
    now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("invalid --now")
    result = run_bridge(Path(args.bridge_dir), Path(args.wes_source), Path(args.belief_state), now)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
