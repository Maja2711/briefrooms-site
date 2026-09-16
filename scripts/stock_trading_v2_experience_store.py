#!/usr/bin/env python3
"""Immutable experience store for Stock Trading v2 Phase 1A.

The store consumes the current GPW/US producer outputs in shadow mode.  It
captures both selected LONG candidates and prospectively frozen rejected LONG
candidates.  Nothing in this module can alter production ranking or portfolio
admission.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:  # pragma: no cover
    from scripts import stock_trading_v2_contracts as contracts

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_ROOT = ROOT / "data/investments/stock_trading_v2_experience"
FREEZE_FIELD = "counterfactual_rejected_candidate_freeze"
FREEZE_SCHEMA = "daily-stock-rejected-candidate-freeze-v1"


class ImmutableStoreConflict(RuntimeError):
    """Raised when an existing immutable event would have to be changed."""


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _source_payload_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = deepcopy(dict(payload))
    body.pop(FREEZE_FIELD, None)
    return body


def _source_payload_sha(payload: Mapping[str, Any]) -> str:
    return contracts.payload_sha256(_source_payload_body(payload))


def _validate_freeze(freeze: Mapping[str, Any], *, expected_source_sha: str) -> None:
    if freeze.get("schema_version") != FREEZE_SCHEMA:
        raise contracts.ContractError("legacy rejected-candidate freeze schema mismatch")
    body = dict(freeze)
    stored = str(body.pop("freeze_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("legacy rejected-candidate freeze hash mismatch")
    source_sha = str(freeze.get("source_payload_sha256") or "")
    if source_sha != expected_source_sha:
        raise contracts.ContractError("legacy freeze/source payload hash mismatch")
    seen: set[str] = set()
    for raw in freeze.get("candidates") or []:
        if not isinstance(raw, Mapping):
            raise contracts.ContractError("legacy freeze candidate must be an object")
        row = dict(raw)
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            raise contracts.ContractError("legacy freeze candidate id missing/duplicated")
        seen.add(candidate_id)
        state_hash = str(row.pop("state_sha256", ""))
        if not state_hash or state_hash != contracts.payload_sha256(row):
            raise contracts.ContractError(f"legacy freeze candidate hash mismatch: {candidate_id}")
        if raw.get("selected") is not False:
            raise contracts.ContractError("legacy freeze may contain rejected candidates only")


def _session_date(payload: Mapping[str, Any]) -> str:
    quality = payload.get("data_quality") if isinstance(payload.get("data_quality"), Mapping) else {}
    return str(quality.get("expected_session") or payload.get("date") or "").strip()


def _selected_candidate_state(payload: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    ev = selection.get("expected_value_model") if isinstance(selection.get("expected_value_model"), Mapping) else {}
    score_keys = (
        "score",
        "legacy_composite_score",
        "opening_adjusted_score",
        "quant_pre_score",
        "quant_rank",
        "opening_confirmation_score",
        "expected_value_score",
        "expected_value_weight",
    )
    risk_keys = (
        "reference_price",
        "entry_zone",
        "skip_above",
        "stop",
        "target",
        "risk_percent",
        "reward_risk",
        "atr",
    )
    score_state = {key: deepcopy(selection.get(key)) for key in score_keys if selection.get(key) is not None}
    score_state["scores"] = deepcopy(selection.get("scores") or {})
    score_state["returns"] = deepcopy(selection.get("returns") or {})
    if ev:
        score_state["expected_value"] = {
            key: deepcopy(ev.get(key))
            for key in (
                "engine",
                "horizon_sessions",
                "role",
                "analogue_count",
                "selected_reward_risk",
                "expected_gross_r",
                "expected_net_r",
                "conservative_ev_r",
                "standard_error_r",
                "effective_sample_size",
                "confidence",
            )
            if ev.get(key) is not None
        }

    risk_plan = {key: deepcopy(selection.get(key)) for key in risk_keys if selection.get(key) is not None}
    risk_plan.update(
        {
            "holding_policy": selection.get("holding_policy"),
            "valid_until_legacy": selection.get("valid_until"),
            "time_stop_legacy": selection.get("time_stop"),
            "early_exit": selection.get("early_exit"),
        }
    )
    return {
        "identity": {
            "name": selection.get("name"),
            "sector": selection.get("sector"),
            "ticker": selection.get("ticker"),
        },
        "decision_path": {
            "producer_decision": payload.get("decision"),
            "producer_reason": payload.get("reason"),
            "selection_mode": selection.get("selection_mode"),
            "first_blocking_gate": None,
        },
        "score_state": score_state,
        "market_state": {
            "historical_data_gate": deepcopy(selection.get("historical_data_gate")),
            "execution_data_gate": deepcopy(selection.get("execution_data_gate")),
            "opening_confirmation": deepcopy(selection.get("opening_confirmation")),
        },
        "risk_plan": risk_plan,
        "thesis_state": {
            "thesis": selection.get("thesis"),
            "why_now": selection.get("why_now"),
            "risk_factors": deepcopy(selection.get("risk_factors") or []),
        },
        "evidence_state": {
            "sources": deepcopy(selection.get("sources") or []),
            "review": deepcopy(selection.get("review")),
        },
        "legacy_source_snapshot": {
            "producer_methodology": deepcopy(payload.get("methodology")),
            "producer_outcome": deepcopy(payload.get("outcome")),
        },
    }


def _rejected_candidate_state(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "name": row.get("name"),
            "sector": row.get("sector"),
        },
        "decision_path": deepcopy(row.get("decision_path") or {}),
        "first_blocking_gate": deepcopy(row.get("first_blocking_gate")),
        "score_state": deepcopy(row.get("score_state") or {}),
        "market_state": deepcopy(row.get("market_state") or {}),
        "risk_plan": deepcopy(row.get("risk_plan")),
        "settlement_eligibility": deepcopy(row.get("settlement_eligibility") or {}),
        "source_freeze_candidate_id": row.get("candidate_id"),
        "source_state_sha256": row.get("state_sha256"),
        "source_governance": deepcopy(row.get("governance") or {}),
    }


def events_from_legacy_payload(
    payload: Mapping[str, Any],
    *,
    market: str,
    recorded_at: str | None = None,
) -> list[dict[str, Any]]:
    market_key = str(market).upper().strip()
    if market_key not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError(f"unsupported market: {market}")
    decision_at = str(payload.get("generated_at") or "").strip()
    session_date = _session_date(payload)
    if not decision_at or not session_date:
        return []

    source_sha = _source_payload_sha(payload)
    source_engine = str(payload.get("schema_version") or "legacy-daily-stock")
    source_schema = str(payload.get("schema_version") or "unknown")
    source_policy = str(payload.get("policy_version") or "unknown")
    stamp = recorded_at or contracts.iso_utc()
    events: list[dict[str, Any]] = []

    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else None
    selected_symbol = ""
    if selection:
        selected_symbol = str(selection.get("symbol") or "").strip()
        if selected_symbol:
            events.append(
                contracts.make_experience_event(
                    market=market_key,
                    symbol=selected_symbol,
                    decision_at=decision_at,
                    session_date=session_date,
                    selected=True,
                    source_engine=source_engine,
                    source_schema_version=source_schema,
                    source_policy_version=source_policy,
                    source_payload_sha256=source_sha,
                    candidate_state=_selected_candidate_state(payload, selection),
                    recorded_at=stamp,
                )
            )

    freeze = payload.get(FREEZE_FIELD)
    if isinstance(freeze, Mapping):
        _validate_freeze(freeze, expected_source_sha=source_sha)
        for row in freeze.get("candidates") or []:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if not symbol or symbol == selected_symbol:
                continue
            events.append(
                contracts.make_experience_event(
                    market=market_key,
                    symbol=symbol,
                    decision_at=decision_at,
                    session_date=session_date,
                    selected=False,
                    source_engine=source_engine,
                    source_schema_version=source_schema,
                    source_policy_version=source_policy,
                    source_payload_sha256=source_sha,
                    candidate_state=_rejected_candidate_state(row),
                    recorded_at=stamp,
                )
            )

    ids = [str(event["event_id"]) for event in events]
    if len(ids) != len(set(ids)):
        raise contracts.ContractError("duplicate experience event ids from producer payload")
    return events


def event_path(root: Path, event: Mapping[str, Any]) -> Path:
    market = str(event["market"]).lower()
    session = str(event["session_date"])
    event_id = str(event["event_id"])
    if any(token in event_id for token in ("/", "\\", "..")):
        raise contracts.ContractError("unsafe event_id")
    return root / market / session / f"{event_id}.json"


def _atomic_write_new(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    try:
        if path.exists():
            raise ImmutableStoreConflict(f"immutable event already exists: {path}")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def persist_event(root: Path, event: Mapping[str, Any]) -> bool:
    contracts.validate_experience_event(event)
    path = event_path(root, event)
    if path.exists():
        existing = _read_json(path)
        contracts.validate_experience_event(existing)
        if contracts.event_without_runtime_fields(existing) == contracts.event_without_runtime_fields(event):
            return False
        raise ImmutableStoreConflict(f"refusing to mutate immutable event: {event['event_id']}")
    _atomic_write_new(path, event)
    return True


def ingest_payload(
    payload: Mapping[str, Any],
    *,
    market: str,
    root: Path = DEFAULT_STORE_ROOT,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    events = events_from_legacy_payload(payload, market=market, recorded_at=recorded_at)
    written = 0
    existing = 0
    for event in events:
        if persist_event(root, event):
            written += 1
        else:
            existing += 1
    return {
        "schema_version": contracts.STORE_SCHEMA_VERSION,
        "market": str(market).upper(),
        "producer_decision_at": payload.get("generated_at"),
        "events_seen": len(events),
        "events_written": written,
        "events_existing": existing,
        "production_decision_influence": False,
    }


def iter_event_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def verify_store(root: Path = DEFAULT_STORE_ROOT) -> dict[str, Any]:
    seen: set[str] = set()
    markets: dict[str, int] = {}
    count = 0
    for path in iter_event_files(root):
        event = _read_json(path)
        contracts.validate_experience_event(event)
        expected = event_path(root, event)
        if expected.resolve() != path.resolve():
            raise contracts.ContractError(f"experience event stored at non-canonical path: {path}")
        event_id = str(event["event_id"])
        if event_id in seen:
            raise contracts.ContractError(f"duplicate experience event id: {event_id}")
        seen.add(event_id)
        market = str(event["market"])
        markets[market] = markets.get(market, 0) + 1
        count += 1
    return {
        "schema_version": contracts.STORE_SCHEMA_VERSION,
        "ok": True,
        "event_count": count,
        "markets": markets,
        "production_decision_influence": False,
    }


def _default_payload(market: str) -> Path:
    if market == "GPW":
        return ROOT / "data/investments/gpw_daily_pick.json"
    return ROOT / "data/investments/us_daily_stock.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["GPW", "US", "gpw", "us"])
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--root", type=Path, default=DEFAULT_STORE_ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        print(json.dumps(verify_store(args.root), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not args.market:
        parser.error("--market is required unless --verify is used")
    market = str(args.market).upper()
    path = args.payload or _default_payload(market)
    payload = _read_json(path)
    result = ingest_payload(payload, market=market, root=args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
