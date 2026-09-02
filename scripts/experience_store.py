#!/usr/bin/env python3
"""Canonical Experience Store derived from the immutable BriefRooms Learning Ledger.

The store is a zero-authority research artifact for future AlfaX training and
current shadow evaluation. Decision-time facts are frozen from pre-existing
`decision` ledger events; outcomes are attached only from later `outcome` events.
No trading, policy tuning, promotion or belief writeback is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from learning_ledger import read_events, verify_chain
except ModuleNotFoundError:
    from scripts.learning_ledger import read_events, verify_chain

SCHEMA_VERSION = "briefrooms-experience-store-v1"
DEFAULT_LEDGER = Path("data/research/learning_ledger.jsonl")
DEFAULT_STORE = Path("data/research/experience_store.jsonl")
DEFAULT_STATUS = Path("data/research/experience_store_status.json")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return None


def _engine(decision: Mapping[str, Any], source_ref: str) -> str:
    explicit = _first(decision, "engine", "market", "decision_mode")
    if explicit:
        return str(explicit).strip().lower()
    ref = source_ref.lower()
    if "://" in ref:
        return ref.split("://", 1)[0].replace("-daily", "")
    return "unknown"


def _action(decision: Mapping[str, Any]) -> str:
    raw = str(_first(decision, "action", "direction", "decision") or "").strip().upper()
    aliases = {
        "BUY": "LONG", "TRADE": "LONG", "TRANSAKCJA": "LONG",
        "SELL": "SHORT", "NO_TRADE": "FLAT", "BRAK_TRANSAKCJI": "FLAT",
        "HOLD": "FLAT", "WAIT": "FLAT",
    }
    return aliases.get(raw, raw or "UNKNOWN")


def _return_fraction(outcome: Mapping[str, Any]) -> float | None:
    value = _first(outcome, "return_fraction")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    value = _first(outcome, "return_percent", "result_percent", "net_return_percent")
    if value is not None:
        try:
            return float(value) / 100.0
        except (TypeError, ValueError):
            return None
    return None


def _gross_return_fraction(outcome: Mapping[str, Any]) -> float | None:
    value = _first(outcome, "gross_return_fraction")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    value = _first(outcome, "gross_return_percent")
    if value is not None:
        try:
            return float(value) / 100.0
        except (TypeError, ValueError):
            return None
    return None


def _cost_fraction(outcome: Mapping[str, Any], net: float | None, gross: float | None) -> float | None:
    explicit = _first(outcome, "cost_fraction")
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    explicit_pct = _first(outcome, "cost_assumption_percent", "costs_percent")
    if explicit_pct is not None:
        try:
            return float(explicit_pct) / 100.0
        except (TypeError, ValueError):
            pass
    if net is not None and gross is not None:
        return gross - net
    return None


def _outcome_block(payload: Mapping[str, Any], occurred_at: str) -> dict[str, Any]:
    net = _return_fraction(payload)
    gross = _gross_return_fraction(payload)
    return {
        "settled_at": occurred_at,
        "status": _first(payload, "status") or "SETTLED",
        "entry_price": _first(payload, "entry_price", "entry"),
        "exit_price": _first(payload, "exit_price", "exit"),
        "exit_reason": _first(payload, "exit_reason"),
        "net_return_fraction": net,
        "gross_return_fraction": gross,
        "cost_fraction": _cost_fraction(payload, net, gross),
        "r_multiple": _first(payload, "r_multiple"),
        "mae_fraction": _first(payload, "mae_fraction", "maximum_adverse_excursion"),
        "mfe_fraction": _first(payload, "mfe_fraction", "maximum_favorable_excursion"),
        "pnl_15m": _first(payload, "pnl_15m"),
        "pnl_1h": _first(payload, "pnl_1h"),
        "pnl_close": _first(payload, "pnl_close"),
        "pnl_1d": _first(payload, "pnl_1d"),
        "pnl_2d": _first(payload, "pnl_2d"),
        "benchmark_return_fraction": _first(payload, "benchmark_return_fraction"),
        "realized_reward": _first(payload, "realized_reward"),
        "raw": dict(payload),
    }


def build_experiences(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    decisions: dict[str, Mapping[str, Any]] = {}
    outcomes: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        subject_id = str(event.get("subject_id") or "")
        if not subject_id:
            continue
        if event_type == "decision":
            # The immutable ledger guarantees event identity. Refuse ambiguous
            # multiple decision facts for one experience rather than guessing.
            if subject_id in decisions and decisions[subject_id].get("event_id") != event.get("event_id"):
                raise ValueError(f"multiple decision events for subject_id={subject_id}")
            decisions[subject_id] = event
        elif event_type == "outcome":
            outcomes.setdefault(subject_id, []).append(event)

    rows: list[dict[str, Any]] = []
    for subject_id, event in sorted(decisions.items(), key=lambda item: str(item[1].get("occurred_at") or "")):
        decision_at = str(event.get("occurred_at") or "")
        decision_dt = _parse_time(decision_at)
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        outcome_candidates = sorted(outcomes.get(subject_id, []), key=lambda row: str(row.get("occurred_at") or ""))
        valid_outcomes = [row for row in outcome_candidates if _parse_time(str(row.get("occurred_at") or "")) > decision_dt]
        if len(valid_outcomes) > 1:
            # Latest outcome is allowed as a lifecycle refinement, but every one
            # must occur strictly after the decision (anti-lookahead invariant).
            outcome_event = valid_outcomes[-1]
        elif valid_outcomes:
            outcome_event = valid_outcomes[0]
        else:
            outcome_event = None

        source_ref = str(event.get("source_ref") or "")
        identity = {
            "decision_event_id": event.get("event_id"),
            "subject_id": subject_id,
            "decision_at": decision_at,
        }
        row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "experience_id": "exp-" + _sha(identity)[:24],
            "subject_id": subject_id,
            "decision_event_id": event.get("event_id"),
            "decision_at": decision_at,
            "engine": _engine(payload, source_ref),
            "engine_version": _first(payload, "engine_version", "policy_version", "model_version"),
            "instrument": _first(payload, "instrument", "symbol", "ticker"),
            "action": _action(payload),
            "confidence": _first(payload, "confidence", "conviction", "entry_confidence", "score"),
            "entry": _first(payload, "entry", "entry_price", "reference_price", "entry_zone"),
            "stop_loss": _first(payload, "stop_loss", "stop"),
            "take_profit": _first(payload, "take_profit", "target"),
            "expected_return": _first(payload, "expected_return", "expected_return_fraction"),
            "market_snapshot_id": _first(payload, "market_snapshot_id", "snapshot_id"),
            "epistemic_state_id": _first(payload, "epistemic_state_id"),
            "decision_envelope_id": _first(payload, "decision_envelope_id", "decision_id"),
            "source_ref": source_ref,
            "decision": dict(payload),
            "status": "SETTLED" if outcome_event else "PENDING",
            "outcome_event_id": outcome_event.get("event_id") if outcome_event else None,
            "outcome": _outcome_block(
                outcome_event.get("payload") if isinstance(outcome_event.get("payload"), Mapping) else {},
                str(outcome_event.get("occurred_at") or ""),
            ) if outcome_event else None,
        }
        rows.append(row)
    return rows


def materialize(ledger: Path, store: Path, status_path: Path) -> dict[str, Any]:
    chain = verify_chain(ledger)
    if not chain.get("ok"):
        raise RuntimeError("invalid Learning Ledger: " + str(chain.get("error")))
    rows = build_experiences(read_events(ledger))
    encoded = "".join(_canonical(row) + "\n" for row in rows)
    _atomic_text(store, encoded)
    settled = sum(1 for row in rows if row["status"] == "SETTLED")
    status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ledger_head_hash": chain.get("head_hash"),
        "experience_count": len(rows),
        "settled_count": settled,
        "pending_count": len(rows) - settled,
        "engines": sorted({str(row.get("engine") or "unknown") for row in rows}),
        "zero_authority": True,
        "anti_lookahead": "outcome.occurred_at must be strictly later than decision.occurred_at",
    }
    _atomic_json(status_path, status)
    return status


def read_experiences(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Experience Store JSON at line {line_no}") from exc
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical shadow experiences from Learning Ledger")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    args = parser.parse_args()
    result = materialize(args.ledger, args.store, args.status)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
