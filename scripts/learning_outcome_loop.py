#!/usr/bin/env python3
"""PR28 — prospective Learning Ledger / Outcome Loop integration.

Copies only prospectively observed immutable facts from existing BriefRooms
producer state into the PR27 Learning Ledger. It has zero decision authority.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

try:
    from learning_ledger import append_event, read_events, safety_controls as ledger_safety, verify_chain
except ModuleNotFoundError:
    from scripts.learning_ledger import append_event, read_events, safety_controls as ledger_safety, verify_chain

SCHEMA_VERSION = "learning-outcome-loop-v1"
ACTIVATION_FILENAME = "learning_loop_activation.json"
LEDGER_FILENAME = "learning_ledger.jsonl"
STATUS_FILENAME = "learning_loop_status.json"


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


def integration_safety_controls() -> Dict[str, bool]:
    controls = dict(ledger_safety())
    controls.update({
        "source_state_writeback": False,
        "historical_backfill": False,
        "same_cycle_outcome_binding": False,
        "decision_engine_influence": False,
    })
    return controls


def _assert_safety() -> None:
    bad = [key for key, value in integration_safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR28 zero-authority invariant violated: " + ",".join(bad))


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


def ensure_activation(
    state_dir: Path,
    *,
    now: Optional[datetime] = None,
    bootstrap: bool = False,
) -> Dict[str, Any]:
    path = state_dir / ACTIVATION_FILENAME
    existing = _load_json(path)
    if isinstance(existing, dict):
        if existing.get("schema_version") != SCHEMA_VERSION or not existing.get("activated_at"):
            raise RuntimeError("invalid learning-loop activation state")
        parse_time(str(existing["activated_at"]))
        return existing
    if not bootstrap:
        raise RuntimeError("learning-loop activation state missing; refusing silent first-run reset")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": iso_z(now or datetime.now(timezone.utc)),
        "anti_hindsight": {
            "historical_backfill": False,
            "outcome_requires_preexisting_upstream_event": True,
            "same_cycle_outcome_binding": False,
        },
        "authority": integration_safety_controls(),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / LEDGER_FILENAME).touch(exist_ok=True)
    _atomic_json(path, payload)
    return payload


def _event_key(event_type: str, subject_id: str) -> tuple[str, str]:
    return event_type, subject_id


def _event_index(events: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    return {
        _event_key(str(row.get("event_type") or ""), str(row.get("subject_id") or ""))
        for row in events
    }


def _after_activation(value: str, activated_at: datetime) -> bool:
    return parse_time(value) >= activated_at


def _inc(summary: Dict[str, Any], source: str, field: str, amount: int = 1) -> None:
    bucket = summary.setdefault("sources", {}).setdefault(
        source,
        {"appended": 0, "skipped_hindsight": 0, "skipped_pre_activation": 0},
    )
    bucket[field] = int(bucket.get(field, 0)) + amount


def _append_once(
    ledger: Path,
    current_index: set[tuple[str, str]],
    summary: Dict[str, Any],
    *,
    source: str,
    event_type: str,
    occurred_at: str,
    subject_id: str,
    source_ref: str,
    payload: Mapping[str, Any],
) -> None:
    key = _event_key(event_type, subject_id)
    if key in current_index:
        return
    append_event(
        ledger,
        event_type=event_type,
        occurred_at=occurred_at,
        subject_id=subject_id,
        source_ref=source_ref,
        payload=dict(payload),
    )
    current_index.add(key)
    _inc(summary, source, "appended")
    counts = summary["appended_by_type"]
    counts[event_type] = int(counts.get(event_type, 0)) + 1


def sync_belief_core(
    state_path: Path,
    ledger: Path,
    *,
    activated_at: datetime,
    preexisting_index: set[tuple[str, str]],
    current_index: set[tuple[str, str]],
    summary: Dict[str, Any],
) -> None:
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        return
    forecasts = [row for row in (state.get("forecasts") or []) if isinstance(row, dict)]
    verifications = [row for row in (state.get("verifications") or []) if isinstance(row, dict)]
    verified_ids = {str(row.get("forecast_id")) for row in verifications if row.get("forecast_id")}

    for row in sorted(forecasts, key=lambda x: (str(x.get("forecast_at") or ""), str(x.get("forecast_id") or ""))):
        forecast_id = str(row.get("forecast_id") or "")
        forecast_at = str(row.get("forecast_at") or "")
        if not forecast_id or not forecast_at:
            continue
        if not _after_activation(forecast_at, activated_at):
            _inc(summary, "belief_core", "skipped_pre_activation")
            continue
        if forecast_id in verified_ids and _event_key("forecast", forecast_id) not in preexisting_index:
            _inc(summary, "belief_core", "skipped_hindsight")
            continue
        _append_once(
            ledger,
            current_index,
            summary,
            source="belief_core",
            event_type="forecast",
            occurred_at=forecast_at,
            subject_id=forecast_id,
            source_ref=f"belief-core://forecast/{forecast_id}",
            payload={
                "forecast_set_id": row.get("forecast_set_id"),
                "belief_id": row.get("belief_id"),
                "predicted_probability": row.get("predicted_probability"),
                "forecast_confidence": row.get("forecast_confidence"),
                "target_at": row.get("target_at"),
                "horizon_hours": row.get("horizon_hours"),
                "domain": row.get("domain"),
                "entity": row.get("entity"),
                "regime": row.get("regime"),
                "alternative_group": row.get("alternative_group"),
                "outcome_rule": row.get("outcome_rule"),
                "representative_evidence_ids": list(row.get("representative_evidence_ids") or []),
            },
        )

    for row in sorted(verifications, key=lambda x: (str(x.get("verified_at") or ""), str(x.get("verification_id") or ""))):
        forecast_id = str(row.get("forecast_id") or "")
        verified_at = str(row.get("verified_at") or "")
        if not forecast_id or not verified_at or bool(row.get("legacy")):
            continue
        if not _after_activation(verified_at, activated_at):
            _inc(summary, "belief_core", "skipped_pre_activation")
            continue
        if _event_key("forecast", forecast_id) not in preexisting_index:
            _inc(summary, "belief_core", "skipped_hindsight")
            continue
        outcome_ref = str(row.get("outcome_ref") or "")
        _append_once(
            ledger,
            current_index,
            summary,
            source="belief_core",
            event_type="outcome",
            occurred_at=verified_at,
            subject_id=forecast_id,
            source_ref=outcome_ref or f"belief-core://verification/{row.get('verification_id') or forecast_id}",
            payload={
                "outcome": bool(row.get("outcome")),
                "outcome_source": row.get("outcome_source"),
                "outcome_ref": row.get("outcome_ref"),
                "target_at": row.get("target_at"),
            },
        )
        _append_once(
            ledger,
            current_index,
            summary,
            source="belief_core",
            event_type="verification",
            occurred_at=verified_at,
            subject_id=forecast_id,
            source_ref=f"belief-core://verification/{row.get('verification_id') or forecast_id}",
            payload={
                "verification_id": row.get("verification_id"),
                "belief_id": row.get("belief_id"),
                "predicted_probability": row.get("predicted_probability"),
                "forecast_confidence": row.get("forecast_confidence"),
                "outcome": bool(row.get("outcome")),
                "brier_score": row.get("brier_score"),
                "log_loss": row.get("log_loss"),
                "calibration_eligible": bool(row.get("calibration_eligible", True)),
                "horizon_hours": row.get("horizon_hours"),
                "domain": row.get("domain"),
                "entity": row.get("entity"),
                "regime": row.get("regime"),
            },
        )


def _stock_payloads(history_dir: Path, current_path: Optional[Path], market: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if history_dir.exists():
        if market == "us":
            try:
                try:
                    from us_daily_stock_position_lifecycle import canonical_history_payloads
                except ModuleNotFoundError:
                    from scripts.us_daily_stock_position_lifecycle import canonical_history_payloads
                canonical, _ = canonical_history_payloads(history_dir)
                rows.extend(x for x in canonical if isinstance(x, dict))
            except Exception as exc:
                raise RuntimeError("unable to canonicalize US Daily Stock history") from exc
        else:
            for path in sorted(history_dir.glob("????-??-??.json")):
                payload = _load_json(path)
                if isinstance(payload, dict):
                    rows.append(payload)
    if current_path:
        current = _load_json(current_path)
        if isinstance(current, dict):
            signature = (str(current.get("date") or ""), str(current.get("generated_at") or ""))
            known = {(str(x.get("date") or ""), str(x.get("generated_at") or "")) for x in rows}
            if signature not in known:
                rows.append(current)
    return rows


def _stock_identity(market: str, row: Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any], str]:
    selection = row.get("selection") if isinstance(row.get("selection"), Mapping) else {}
    generated_at = str(row.get("generated_at") or "")
    decision = str(row.get("decision") or "")
    if market == "us" and isinstance(row.get("position"), Mapping):
        position = row["position"]
        if position.get("position_id") and position.get("opened_at"):
            entry_selection = position.get("entry_selection")
            if isinstance(entry_selection, Mapping):
                selection = entry_selection
            return str(position["position_id"]), str(position["opened_at"]), selection, "TRADE"
    symbol = str(selection.get("symbol") or selection.get("ticker") or "NO_TRADE")
    date = str(row.get("date") or "unknown-date")
    return f"{market}:{date}:{symbol}", generated_at, selection, decision


def sync_daily_stock(
    *,
    market: str,
    history_dir: Path,
    current_path: Optional[Path],
    ledger: Path,
    activated_at: datetime,
    preexisting_index: set[tuple[str, str]],
    current_index: set[tuple[str, str]],
    summary: Dict[str, Any],
) -> None:
    trade_decision = "TRADE" if market == "us" else "TRANSAKCJA"
    rows = _stock_payloads(history_dir, current_path, market)
    for row in sorted(rows, key=lambda x: str(x.get("generated_at") or "")):
        subject_id, decision_at, selection, decision = _stock_identity(market, row)
        if not decision_at:
            continue
        if not _after_activation(decision_at, activated_at):
            _inc(summary, market, "skipped_pre_activation")
            continue
        outcome = row.get("outcome") if isinstance(row.get("outcome"), Mapping) else {}
        resolved = str(outcome.get("status") or "").upper() == "RESOLVED"
        had_decision_before = _event_key("decision", subject_id) in preexisting_index
        if resolved and not had_decision_before:
            _inc(summary, market, "skipped_hindsight")
            continue

        _append_once(
            ledger,
            current_index,
            summary,
            source=market,
            event_type="decision",
            occurred_at=decision_at,
            subject_id=subject_id,
            source_ref=f"{market}-daily://{row.get('date') or subject_id}",
            payload={
                "market": market,
                "decision": decision,
                "reason": row.get("reason"),
                "policy_version": row.get("policy_version"),
                "date": row.get("date"),
                "symbol": selection.get("symbol") or selection.get("ticker"),
                "sector": selection.get("sector"),
                "score": selection.get("score"),
                "conviction": selection.get("conviction"),
                "reference_price": selection.get("reference_price"),
                "entry_zone": selection.get("entry_zone"),
                "stop": selection.get("stop"),
                "target": selection.get("target"),
                "reward_risk": selection.get("reward_risk"),
                "valid_until": selection.get("valid_until"),
                "is_trade": decision == trade_decision,
            },
        )
        if not resolved:
            continue
        if not had_decision_before:
            _inc(summary, market, "skipped_hindsight")
            continue
        resolved_at = str(
            outcome.get("resolved_at")
            or outcome.get("closed_at")
            or outcome.get("exit_bar_at")
            or decision_at
        )
        _append_once(
            ledger,
            current_index,
            summary,
            source=market,
            event_type="outcome",
            occurred_at=resolved_at,
            subject_id=subject_id,
            source_ref=f"{market}-daily://outcome/{row.get('date') or subject_id}",
            payload={
                "market": market,
                "status": outcome.get("status"),
                "activated": outcome.get("activated"),
                "activated_at": outcome.get("activated_at"),
                "entry_price": outcome.get("entry_price"),
                "exit_price": outcome.get("exit_price"),
                "exit_reason": outcome.get("exit_reason"),
                "closed_at": outcome.get("closed_at"),
                "return_percent": outcome.get("return_percent"),
                "gross_return_percent": outcome.get("gross_return_percent"),
                "r_multiple": outcome.get("r_multiple"),
                "outcome": outcome.get("outcome"),
                "cost_assumption_percent": outcome.get("cost_assumption_percent"),
                "settlement_policy": outcome.get("settlement_policy"),
            },
        )


def sync_eurusd(
    current_path: Path,
    history_path: Path,
    ledger: Path,
    *,
    activated_at: datetime,
    preexisting_index: set[tuple[str, str]],
    current_index: set[tuple[str, str]],
    summary: Dict[str, Any],
) -> None:
    current = _load_json(current_path, {})
    if isinstance(current, dict):
        metadata = current.get("metadata") if isinstance(current.get("metadata"), Mapping) else {}
        position = metadata.get("position") if isinstance(metadata.get("position"), Mapping) else {}
        trade_id = str(position.get("trade_id") or "")
        opened_at = str(position.get("opened_at") or "")
        if trade_id and opened_at and str(position.get("status") or "").upper() == "OPEN":
            if _after_activation(opened_at, activated_at):
                _append_once(
                    ledger,
                    current_index,
                    summary,
                    source="eurusd",
                    event_type="decision",
                    occurred_at=opened_at,
                    subject_id=trade_id,
                    source_ref=f"eurusd-daily://trade/{trade_id}",
                    payload={
                        "instrument": current.get("instrument") or "EUR/USD",
                        "direction": position.get("direction") or current.get("direction"),
                        "entry": position.get("entry"),
                        "stop": position.get("stop"),
                        "target": position.get("target"),
                        "expires_at": position.get("expires_at"),
                        "entry_score": position.get("entry_score"),
                        "entry_confidence": position.get("entry_confidence"),
                        "entry_components": position.get("entry_components") or {},
                        "entry_weights": position.get("entry_weights") or {},
                        "engine_version": position.get("engine_version") or current.get("engine_version"),
                        "decision_mode": current.get("decision_mode"),
                    },
                )
            else:
                _inc(summary, "eurusd", "skipped_pre_activation")

    history = _load_json(history_path, {})
    trades = history.get("trades") if isinstance(history, dict) and isinstance(history.get("trades"), list) else []
    for trade in sorted((x for x in trades if isinstance(x, dict)), key=lambda x: str(x.get("closed_at") or "")):
        trade_id = str(trade.get("trade_id") or "")
        opened_at = str(trade.get("opened_at") or "")
        closed_at = str(trade.get("closed_at") or "")
        if not trade_id or not opened_at or not closed_at:
            continue
        if not _after_activation(opened_at, activated_at):
            _inc(summary, "eurusd", "skipped_pre_activation")
            continue
        if _event_key("decision", trade_id) not in preexisting_index:
            _inc(summary, "eurusd", "skipped_hindsight")
            continue
        _append_once(
            ledger,
            current_index,
            summary,
            source="eurusd",
            event_type="outcome",
            occurred_at=closed_at,
            subject_id=trade_id,
            source_ref=f"eurusd-daily://outcome/{trade_id}",
            payload={
                "instrument": trade.get("instrument") or "EUR/USD",
                "direction": trade.get("direction"),
                "entry": trade.get("entry"),
                "exit_price": trade.get("exit_price"),
                "exit_reason": trade.get("exit_reason"),
                "result_percent": trade.get("result_percent"),
                "return_fraction": trade.get("return_fraction"),
                "r_multiple": trade.get("r_multiple"),
                "outcome": trade.get("outcome"),
                "engine_version": trade.get("engine_version"),
                "monitor": trade.get("monitor") or {},
            },
        )


def sync_all(
    state_dir: Path,
    *,
    belief_state_path: Optional[Path],
    investments_dir: Path,
    now: Optional[datetime] = None,
    bootstrap: bool = False,
) -> Dict[str, Any]:
    _assert_safety()
    activation = ensure_activation(state_dir, now=now, bootstrap=bootstrap)
    activated_at = parse_time(str(activation["activated_at"]))
    ledger = state_dir / LEDGER_FILENAME
    chain = verify_chain(ledger)
    if not chain["ok"]:
        raise RuntimeError("invalid learning ledger before sync: " + str(chain.get("error")))

    preexisting_events = read_events(ledger)
    preexisting_index = _event_index(preexisting_events)
    current_index = set(preexisting_index)
    summary: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "activated_at": activation["activated_at"],
        "synced_at": iso_z(now or datetime.now(timezone.utc)),
        "events_before": len(preexisting_events),
        "events_after": 0,
        "appended_by_type": {},
        "sources": {},
        "zero_authority": integration_safety_controls(),
    }

    if belief_state_path and belief_state_path.exists():
        sync_belief_core(
            belief_state_path,
            ledger,
            activated_at=activated_at,
            preexisting_index=preexisting_index,
            current_index=current_index,
            summary=summary,
        )
    sync_daily_stock(
        market="gpw",
        history_dir=investments_dir / "gpw_daily_pick_history",
        current_path=investments_dir / "gpw_daily_pick.json",
        ledger=ledger,
        activated_at=activated_at,
        preexisting_index=preexisting_index,
        current_index=current_index,
        summary=summary,
    )
    sync_daily_stock(
        market="us",
        history_dir=investments_dir / "us_daily_stock_history",
        current_path=investments_dir / "us_daily_stock.json",
        ledger=ledger,
        activated_at=activated_at,
        preexisting_index=preexisting_index,
        current_index=current_index,
        summary=summary,
    )
    sync_eurusd(
        investments_dir / "eurusd_daily_spot.json",
        investments_dir / "eurusd_daily_history.json",
        ledger,
        activated_at=activated_at,
        preexisting_index=preexisting_index,
        current_index=current_index,
        summary=summary,
    )

    final_chain = verify_chain(ledger)
    if not final_chain["ok"]:
        raise RuntimeError("invalid learning ledger after sync: " + str(final_chain.get("error")))
    summary["events_after"] = int(final_chain["count"])
    summary["ledger_head_hash"] = final_chain.get("head_hash")
    _atomic_json(state_dir / STATUS_FILENAME, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prospectively copy engine forecasts/decisions/outcomes into Learning Ledger")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--belief-state", type=Path)
    parser.add_argument("--investments-dir", type=Path, default=Path("data/investments"))
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--now")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        result = verify_chain(args.state_dir / LEDGER_FILENAME)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 2
    result = sync_all(
        args.state_dir,
        belief_state_path=args.belief_state,
        investments_dir=args.investments_dir,
        now=parse_time(args.now) if args.now else None,
        bootstrap=args.bootstrap,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
