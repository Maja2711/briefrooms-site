#!/usr/bin/env python3
"""Prospective zero-authority bridge from live-shadow trade state to Experience Store.

The bridge keeps its own immutable Learning Ledger chain. It currently consumes
Daily EURUSD A/B/C Live Shadow trade plans and paths without modifying the source
experiment. Decisions are copied only while their future outcome is still unknown;
terminal outcomes are attached only when the corresponding decision was already
present before the current bridge cycle. This preserves the same anti-hindsight
contract as the canonical Learning Outcome Loop.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from learning_ledger import append_event, read_events, verify_chain
except ModuleNotFoundError:
    from scripts.learning_ledger import append_event, read_events, verify_chain

SCHEMA_VERSION = "briefrooms-shadow-trade-bridge-v1"
ACTIVATION_FILENAME = "shadow_trade_activation.json"
LEDGER_FILENAME = "shadow_trade_ledger.jsonl"
STATUS_FILENAME = "shadow_trade_bridge_status.json"
TERMINAL_TRADE_STATUSES = {"CLOSED", "AMBIGUOUS"}


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def safety_controls() -> dict[str, bool]:
    return {
        "source_state_writeback": False,
        "historical_backfill": False,
        "same_cycle_outcome_binding": False,
        "decision_engine_influence": False,
        "trade_execution": False,
        "automatic_tuning": False,
        "automatic_promotion": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("shadow bridge zero-authority invariant violated: " + ",".join(bad))


def ensure_activation(state_dir: Path, *, now: datetime | None = None, bootstrap: bool = False) -> dict[str, Any]:
    path = state_dir / ACTIVATION_FILENAME
    existing = _load_json(path)
    if isinstance(existing, dict):
        if existing.get("schema_version") != SCHEMA_VERSION or not existing.get("activated_at"):
            raise RuntimeError("invalid shadow bridge activation state")
        parse_time(str(existing["activated_at"]))
        return existing
    if not bootstrap:
        raise RuntimeError("shadow bridge activation missing; refusing silent reset")
    activated = now or datetime.now(timezone.utc)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": iso_z(activated),
        "source": "Daily EURUSD A-B-C Live Shadow",
        "anti_hindsight": {
            "historical_backfill": False,
            "terminal_outcome_requires_preexisting_decision": True,
            "same_cycle_outcome_binding": False,
        },
        "zero_authority": safety_controls(),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / LEDGER_FILENAME).touch(exist_ok=True)
    _atomic_json(path, payload)
    return payload


def _event_index(events: list[Mapping[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(row.get("event_type") or ""), str(row.get("subject_id") or ""))
        for row in events
    }


def _append_once(
    ledger: Path,
    current_index: set[tuple[str, str]],
    *,
    event_type: str,
    occurred_at: str,
    subject_id: str,
    source_ref: str,
    payload: Mapping[str, Any],
) -> bool:
    key = (event_type, subject_id)
    if key in current_index:
        return False
    append_event(
        ledger,
        event_type=event_type,
        occurred_at=occurred_at,
        subject_id=subject_id,
        source_ref=source_ref,
        payload=dict(payload),
    )
    current_index.add(key)
    return True


def _fraction_from_bps(value: Any) -> float | None:
    try:
        return float(value) / 10000.0 if value is not None else None
    except (TypeError, ValueError):
        return None


def _r_multiple(path_arm: Mapping[str, Any], plan: Mapping[str, Any], plan_arm: Mapping[str, Any]) -> float | None:
    realized_bps = path_arm.get("realized_bps")
    try:
        entry = float(plan_arm.get("entry_price"))
        risk_distance = float((plan.get("risk_contract") or {}).get("risk_distance"))
        if realized_bps is None or entry <= 0 or risk_distance <= 0:
            return None
        risk_bps = risk_distance / entry * 10000.0
        return round(float(realized_bps) / risk_bps, 6) if risk_bps else None
    except (TypeError, ValueError):
        return None


def _market_24h_outcome(capture: Mapping[str, Any]) -> Mapping[str, Any] | None:
    horizon = (capture.get("horizons") or {}).get("1440m") or {}
    outcome = horizon.get("outcome")
    return outcome if isinstance(outcome, Mapping) else None


def _decision_payload(capture: Mapping[str, Any], arm_id: str) -> dict[str, Any]:
    arm = ((capture.get("arms") or {}).get(arm_id) or {})
    plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), Mapping) else {}
    plan_arm = ((plan.get("arms") or {}).get(arm_id) or {})
    direction = str(arm.get("direction") or "UNAVAILABLE").upper()
    return {
        "engine": f"eurusd-abc-{arm_id.lower()}",
        "engine_version": capture.get("engine_version"),
        "instrument": "EUR/USD",
        "action": direction,
        "confidence": arm.get("confidence"),
        "score": arm.get("score"),
        "entry": plan_arm.get("entry_price"),
        "stop_loss": plan_arm.get("stop_price"),
        "take_profit": plan_arm.get("target_price"),
        "capture_id": capture.get("capture_id"),
        "arm_id": arm_id,
        "arm_label": arm.get("label"),
        "captured_at": capture.get("captured_at"),
        "market_observed_at": capture.get("market_observed_at"),
        "reference_price": capture.get("reference_price"),
        "decision_sha256": capture.get("decision_sha256"),
        "trade_plan_sha256": capture.get("trade_plan_sha256"),
        "risk_contract": dict(plan.get("risk_contract") or {}),
        "signal_snapshot": dict(arm),
        "execution_mode": "research_shadow_virtual",
        "executable_bid_ask_available": False,
        "cost_model_status": "UNAVAILABLE_YAHOO_OHLC",
        "research_boundary": dict(capture.get("research_boundary") or {}),
    }


def _trade_outcome_payload(capture: Mapping[str, Any], arm_id: str) -> tuple[str, dict[str, Any]] | None:
    plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), Mapping) else {}
    path = capture.get("trade_path") if isinstance(capture.get("trade_path"), Mapping) else {}
    plan_arm = ((plan.get("arms") or {}).get(arm_id) or {})
    path_arm = ((path.get("arms") or {}).get(arm_id) or {})
    status = str(path_arm.get("status") or "")
    if status not in TERMINAL_TRADE_STATUSES:
        return None
    occurred_at = str(path_arm.get("exit_at") or path_arm.get("first_touch_at") or "")
    if not occurred_at:
        return None
    market_24h = _market_24h_outcome(capture) or {}
    gross = _fraction_from_bps(path_arm.get("realized_bps"))
    payload = {
        "engine": f"eurusd-abc-{arm_id.lower()}",
        "engine_version": capture.get("engine_version"),
        "instrument": "EUR/USD",
        "status": "RESOLVED" if status == "CLOSED" else "AMBIGUOUS",
        "entry_price": plan_arm.get("entry_price"),
        "exit_price": path_arm.get("exit_price"),
        "exit_reason": path_arm.get("exit_reason"),
        "gross_return_fraction": gross,
        "cost_adjusted": False,
        "cost_fraction": None,
        "cost_model_status": "UNAVAILABLE_YAHOO_OHLC",
        "r_multiple": _r_multiple(path_arm, plan, plan_arm),
        "mae_fraction": _fraction_from_bps(path_arm.get("mae_bps")),
        "mfe_fraction": _fraction_from_bps(path_arm.get("mfe_bps")),
        "market_return_fraction": _fraction_from_bps(market_24h.get("raw_return_bps")),
        "market_return_resolved_at": market_24h.get("resolved_at"),
        "minutes_to_first_touch": path_arm.get("minutes_to_first_touch"),
        "capture_id": capture.get("capture_id"),
        "arm_id": arm_id,
        "execution_mode": "research_shadow_virtual",
    }
    return occurred_at, payload


def _flat_outcome_payload(capture: Mapping[str, Any], arm_id: str) -> tuple[str, dict[str, Any]] | None:
    market_24h = _market_24h_outcome(capture)
    if not market_24h:
        return None
    occurred_at = str(market_24h.get("resolved_at") or "")
    if not occurred_at:
        return None
    payload = {
        "engine": f"eurusd-abc-{arm_id.lower()}",
        "engine_version": capture.get("engine_version"),
        "instrument": "EUR/USD",
        "status": "RESOLVED",
        "exit_reason": "FLAT_24H",
        "return_fraction": 0.0,
        "gross_return_fraction": 0.0,
        "cost_fraction": 0.0,
        "cost_adjusted": True,
        "market_return_fraction": _fraction_from_bps(market_24h.get("raw_return_bps")),
        "capture_id": capture.get("capture_id"),
        "arm_id": arm_id,
        "execution_mode": "research_shadow_virtual",
    }
    return occurred_at, payload


def sync_eurusd_abc(
    state_dir: Path,
    abc_state_path: Path,
    *,
    now: datetime | None = None,
    bootstrap: bool = False,
) -> dict[str, Any]:
    _assert_safety()
    activation = ensure_activation(state_dir, now=now, bootstrap=bootstrap)
    activated_at = parse_time(str(activation["activated_at"]))
    ledger = state_dir / LEDGER_FILENAME
    chain = verify_chain(ledger)
    if not chain.get("ok"):
        raise RuntimeError("invalid shadow trade ledger before sync: " + str(chain.get("error")))

    abc = _load_json(abc_state_path, {})
    if not isinstance(abc, Mapping):
        raise RuntimeError("EURUSD A/B/C state is unavailable or invalid")
    if str(abc.get("mode") or "") != "research_shadow":
        raise RuntimeError("EURUSD A/B/C source must remain research_shadow")

    preexisting_events = read_events(ledger)
    preexisting_index = _event_index(preexisting_events)
    current_index = set(preexisting_index)
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": activation["activated_at"],
        "synced_at": iso_z(now or datetime.now(timezone.utc)),
        "source_engine_version": abc.get("engine_version"),
        "captures_seen": 0,
        "decisions_appended": 0,
        "outcomes_appended": 0,
        "skipped_pre_activation": 0,
        "skipped_hindsight": 0,
        "skipped_unavailable": 0,
        "events_before": len(preexisting_events),
        "events_after": 0,
        "zero_authority": safety_controls(),
    }

    for capture in sorted((row for row in abc.get("captures") or [] if isinstance(row, Mapping)), key=lambda row: str(row.get("captured_at") or "")):
        capture_id = str(capture.get("capture_id") or "")
        captured_at = str(capture.get("captured_at") or "")
        if not capture_id or not captured_at:
            continue
        summary["captures_seen"] += 1
        if parse_time(captured_at) < activated_at:
            summary["skipped_pre_activation"] += 1
            continue
        plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), Mapping) else {}
        path = capture.get("trade_path") if isinstance(capture.get("trade_path"), Mapping) else {}
        market_24h = _market_24h_outcome(capture)

        for arm_id in ("A", "B", "C"):
            arm = ((capture.get("arms") or {}).get(arm_id) or {})
            if not arm.get("available") or str(arm.get("direction") or "UNAVAILABLE").upper() == "UNAVAILABLE":
                summary["skipped_unavailable"] += 1
                continue
            subject_id = f"eurusd-abc:{capture_id}:{arm_id}"
            decision_key = ("decision", subject_id)
            had_decision_before = decision_key in preexisting_index
            direction = str(arm.get("direction") or "UNAVAILABLE").upper()
            path_status = str((((path.get("arms") or {}).get(arm_id) or {}).get("status") or ""))
            outcome_already_known = (
                path_status in TERMINAL_TRADE_STATUSES
                if direction in {"LONG", "SHORT"}
                else market_24h is not None
            )

            if not had_decision_before and outcome_already_known:
                summary["skipped_hindsight"] += 1
                continue

            if _append_once(
                ledger,
                current_index,
                event_type="decision",
                occurred_at=captured_at,
                subject_id=subject_id,
                source_ref=f"eurusd-abc-shadow://capture/{capture_id}/{arm_id}",
                payload=_decision_payload(capture, arm_id),
            ):
                summary["decisions_appended"] += 1

            # Outcomes may bind only to a decision that existed before this cycle.
            if not had_decision_before:
                continue
            outcome = (
                _trade_outcome_payload(capture, arm_id)
                if direction in {"LONG", "SHORT"}
                else _flat_outcome_payload(capture, arm_id)
            )
            if outcome is None:
                continue
            occurred_at, payload = outcome
            if parse_time(occurred_at) <= parse_time(captured_at):
                raise RuntimeError("shadow outcome timestamp must be strictly later than decision")
            if _append_once(
                ledger,
                current_index,
                event_type="outcome",
                occurred_at=occurred_at,
                subject_id=subject_id,
                source_ref=f"eurusd-abc-shadow://outcome/{capture_id}/{arm_id}",
                payload=payload,
            ):
                summary["outcomes_appended"] += 1

    final = verify_chain(ledger)
    if not final.get("ok"):
        raise RuntimeError("invalid shadow trade ledger after sync: " + str(final.get("error")))
    summary["events_after"] = int(final.get("count") or 0)
    summary["ledger_head_hash"] = final.get("head_hash")
    summary["pending_decisions"] = sum(
        1 for row in read_events(ledger)
        if row.get("event_type") == "decision"
        and ("outcome", str(row.get("subject_id") or "")) not in current_index
    )
    _atomic_json(state_dir / STATUS_FILENAME, summary)
    return summary


def verify_state(state_dir: Path) -> dict[str, Any]:
    activation = _load_json(state_dir / ACTIVATION_FILENAME)
    if not isinstance(activation, Mapping) or activation.get("schema_version") != SCHEMA_VERSION:
        return {"ok": False, "error": "invalid_activation"}
    if any(value is not False for value in (activation.get("zero_authority") or {}).values()):
        return {"ok": False, "error": "authority_violation"}
    chain = verify_chain(state_dir / LEDGER_FILENAME)
    return {"ok": bool(chain.get("ok")), "activation": dict(activation), "ledger": chain}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prospectively bridge live-shadow trades into an immutable research ledger")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--abc-state", type=Path)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--now")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_state(args.state_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 2
    if args.abc_state is None:
        parser.error("--abc-state is required unless --verify is used")
    result = sync_eurusd_abc(
        args.state_dir,
        args.abc_state,
        now=parse_time(args.now) if args.now else None,
        bootstrap=args.bootstrap,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
