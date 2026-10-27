#!/usr/bin/env python3
"""Immutable HOLD/EXIT experience ledger for Stock Trading v2.

The canonical portfolio already reviews open positions with open-ended holding,
SL/TP risk ratchets and model-thesis exits. This ledger freezes those *actual*
position-management decisions prospectively so future v2 learning can compare
HOLD versus EXIT policies without inventing historical model state.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio
    import stock_trading_v2_contracts as contracts

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_position_experience"
SCHEMA_VERSION = "stock-trading-v2-position-decision-v1"
OBSERVATION_SCHEMA_VERSION = "stock-trading-v2-position-read-observation-v1"
DEFAULT_OBSERVATION_ROOT = ROOT / "data/investments/stock_trading_v2_position_observations"


class ImmutablePositionExperienceConflict(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _event_id(*, market: str, position_id: str, action: str, decision_at: str) -> str:
    digest = contracts.payload_sha256(
        {"market": market, "position_id": position_id, "action": action, "decision_at": decision_at}
    )[:24]
    return f"stposv2-{digest}"


def _position_state(position: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "position_id",
        "market",
        "symbol",
        "ticker",
        "name",
        "sector",
        "opened_at",
        "entry",
        "stop",
        "target",
        "initial_risk_amount",
        "strategic_target_rr",
        "risk_percent",
        "reward_risk",
        "entry_score",
        "last_mark",
        "peak_mark",
        "thesis_score",
        "peak_thesis_score",
        "thesis_status",
        "last_reviewed_at",
        "risk_review_date",
        "risk_last_changed_at",
        "holding_policy",
        "scheduled_exit",
        "valid_until",
        "time_stop",
        "closed_at",
        "exit_price",
        "exit_reason",
        "return_percent",
        "r_multiple",
    )
    state = {key: deepcopy(position.get(key)) for key in keys if key in position}
    reviews = position.get("risk_reviews") or []
    if isinstance(reviews, list) and reviews:
        state["latest_risk_review"] = deepcopy(reviews[-1])
    return state


def make_event(
    *,
    market: str,
    position: Mapping[str, Any],
    action: str,
    decision_at: str,
    portfolio_updated_at: str | None,
    reason: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    market = str(market).upper()
    if market not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("position experience market unsupported")
    action = str(action).upper()
    if action not in {"HOLD", "EXIT"}:
        raise contracts.ContractError("position experience action unsupported")
    position_id = str(position.get("position_id") or "").strip()
    symbol = str(position.get("symbol") or "").strip()
    if not position_id or not symbol or not decision_at:
        raise contracts.ContractError("position experience identity missing")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": _event_id(market=market, position_id=position_id, action=action, decision_at=decision_at),
        "event_type": "POSITION_DECISION",
        "market": market,
        "symbol": symbol,
        "position_id": position_id,
        "action": action,
        "reason": str(reason or action.lower()),
        "decision_at": str(decision_at),
        "recorded_at": recorded_at or contracts.iso_utc(),
        "portfolio_updated_at": portfolio_updated_at,
        "position_state": _position_state(position),
        "governance": {
            "immutable": True,
            "prospective_observation": True,
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "learning_eligible": True,
        },
    }
    body = dict(payload)
    payload["event_sha256"] = contracts.payload_sha256(body)
    validate_event(payload)
    return payload


def validate_event(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("event_type") != "POSITION_DECISION":
        raise contracts.ContractError("position experience schema/type mismatch")
    if payload.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("position experience market unsupported")
    if payload.get("action") not in {"HOLD", "EXIT"}:
        raise contracts.ContractError("position experience action unsupported")
    for key in ("event_id", "symbol", "position_id", "decision_at", "recorded_at", "reason"):
        if not str(payload.get(key) or "").strip():
            raise contracts.ContractError(f"position experience missing {key}")
    state = payload.get("position_state")
    if not isinstance(state, Mapping):
        raise contracts.ContractError("position_state must be an object")
    if str(state.get("holding_policy") or "OPEN_ENDED_MODEL_CONTROLLED") != "OPEN_ENDED_MODEL_CONTROLLED":
        raise contracts.ContractError("position experience restored a fixed holding horizon")
    governance = payload.get("governance") or {}
    if governance.get("immutable") is not True or governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("position experience governance invariant failed")
    body = dict(payload)
    stored = str(body.pop("event_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("position experience hash mismatch")


def event_path(root: Path, payload: Mapping[str, Any]) -> Path:
    decision_at = str(payload["decision_at"]).replace("Z", "+00:00")
    try:
        session = datetime.fromisoformat(decision_at).date().isoformat()
    except ValueError:
        session = "unknown-date"
    return root / str(payload["market"]).lower() / session / f"{payload['event_id']}.json"


def _decision_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Exclude only collection metadata, never a position or decision field."""
    body = deepcopy(dict(payload))
    for key in ("recorded_at", "event_sha256", "portfolio_updated_at"):
        body.pop(key, None)
    return body


def observation_root_for(root: Path) -> Path:
    return root.parent / ("stock_trading_v2_position_observations" if root.name == DEFAULT_ROOT.name else root.name + "_observations")


def _observe(root: Path, frozen: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    identity = {
        "source_event_id": frozen["event_id"],
        "source_event_sha256": frozen["event_sha256"],
        "portfolio_updated_at": incoming.get("portfolio_updated_at"),
    }
    observation_id = "stposreadv2-" + contracts.payload_sha256(identity)[:24]
    payload = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "observation_id": observation_id,
        **identity,
        "observed_at": incoming["recorded_at"],
        "governance": {"immutable": True, "production_decision_influence": False, "decision_rewrite": False},
    }
    payload["observation_sha256"] = contracts.payload_sha256(payload)
    path = root / str(frozen["market"]).lower() / f"{observation_id}.json"
    if path.exists():
        prior = _read_json(path)
        validate_read_observation(prior)
        if any(prior.get(key) != value for key, value in identity.items()):
            raise ImmutablePositionExperienceConflict("position observation identity collision")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create prevents a concurrent retry from replacing immutable bytes.
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        return _observe(root, frozen, incoming)
    return True


def validate_read_observation(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise contracts.ContractError("position read observation schema mismatch")
    for key in ("observation_id", "source_event_id", "source_event_sha256", "observed_at"):
        if not str(payload.get(key) or "").strip():
            raise contracts.ContractError(f"position read observation missing {key}")
    identity = {key: payload.get(key) for key in ("source_event_id", "source_event_sha256", "portfolio_updated_at")}
    if payload["observation_id"] != "stposreadv2-" + contracts.payload_sha256(identity)[:24]:
        raise contracts.ContractError("position read observation identity mismatch")
    if payload.get("governance") != {"immutable": True, "production_decision_influence": False, "decision_rewrite": False}:
        raise contracts.ContractError("position read observation governance mismatch")
    body = dict(payload)
    digest = body.pop("observation_sha256", None)
    if digest != contracts.payload_sha256(body):
        raise contracts.ContractError("position read observation hash mismatch")


def persist(root: Path, payload: Mapping[str, Any], *, observation_root: Path | None = None) -> bool:
    validate_event(payload)
    path = event_path(root, payload)
    if path.exists():
        existing = _read_json(path)
        validate_event(existing)
        if _decision_body(existing) == _decision_body(payload):
            _observe(observation_root or observation_root_for(root), existing, payload)
            return False
        raise ImmutablePositionExperienceConflict(f"refusing to mutate {payload['event_id']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    try:
        # Windows rename refuses an existing destination; POSIX link supplies
        # the same exclusive publish contract without replacing old bytes.
        if os.name == "nt":
            temp.rename(path)
        else:
            os.link(temp, path)
    except FileExistsError:
        return persist(root, payload, observation_root=observation_root)
    finally:
        temp.unlink(missing_ok=True)
    _observe(observation_root or observation_root_for(root), payload, payload)
    return True


def events_from_portfolio_state(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    updated_at = str(state.get("updated_at") or "") or None
    events: list[dict[str, Any]] = []
    markets = state.get("markets") or {}
    for market in ("GPW", "US"):
        row = markets.get(market) if isinstance(markets, Mapping) else None
        if not isinstance(row, Mapping):
            continue
        for raw in row.get("open_positions") or []:
            if not isinstance(raw, Mapping):
                continue
            decision_at = str(raw.get("last_reviewed_at") or updated_at or "").strip()
            if not decision_at:
                continue
            latest_review = (raw.get("risk_reviews") or [])[-1] if isinstance(raw.get("risk_reviews"), list) and raw.get("risk_reviews") else {}
            action = "HOLD"
            reason = "risk_reviewed" if isinstance(latest_review, Mapping) and str(latest_review.get("reviewed_at") or "") == decision_at else "model_hold"
            events.append(
                make_event(
                    market=market,
                    position=raw,
                    action=action,
                    decision_at=decision_at,
                    portfolio_updated_at=updated_at,
                    reason=reason,
                )
            )
        for raw in row.get("closed_positions") or []:
            if not isinstance(raw, Mapping):
                continue
            decision_at = str(raw.get("closed_at") or "").strip()
            if not decision_at:
                continue
            events.append(
                make_event(
                    market=market,
                    position=raw,
                    action="EXIT",
                    decision_at=decision_at,
                    portfolio_updated_at=updated_at,
                    reason=str(raw.get("exit_reason") or "exit"),
                )
            )
    return events


def ingest(
    *,
    state_path: Path = portfolio.STATE_PATH,
    root: Path = DEFAULT_ROOT,
    observation_root: Path | None = None,
) -> dict[str, Any]:
    state = _read_json(state_path)
    events = events_from_portfolio_state(state)
    written = existing = 0
    actions = {"HOLD": 0, "EXIT": 0}
    for event in events:
        actions[str(event["action"])] += 1
        if persist(root, event, observation_root=observation_root):
            written += 1
        else:
            existing += 1
    return {
        "schema_version": "stock-trading-v2-position-experience-run-v1",
        "events_seen": len(events),
        "written": written,
        "existing": existing,
        "actions": actions,
        "observation_root": str(observation_root or observation_root_for(root)),
        "production_decision_influence": False,
    }


def iter_files(root: Path = DEFAULT_ROOT) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.json"))


def verify(root: Path = DEFAULT_ROOT, *, observation_root: Path | None = None) -> dict[str, Any]:
    ids: set[str] = set()
    hashes: dict[str, str] = {}
    count = 0
    actions = {"HOLD": 0, "EXIT": 0}
    for path in iter_files(root):
        payload = _read_json(path)
        validate_event(payload)
        if payload["event_id"] in ids:
            raise contracts.ContractError("duplicate position experience event")
        ids.add(payload["event_id"])
        hashes[str(payload["event_id"])] = str(payload["event_sha256"])
        actions[str(payload["action"])] += 1
        count += 1
    reads = 0
    for path in sorted((observation_root or observation_root_for(root)).rglob("*.json")):
        row = _read_json(path)
        validate_read_observation(row)
        if row["source_event_id"] not in ids:
            raise contracts.ContractError("position read observation missing source event")
        if row["source_event_sha256"] != hashes[row["source_event_id"]]:
            raise contracts.ContractError("position read observation source hash mismatch")
        reads += 1
    return {"schema_version": SCHEMA_VERSION, "ok": True, "count": count, "actions": actions, "read_observations": reads, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=portfolio.STATE_PATH)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--observation-root", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.root, observation_root=args.observation_root) if args.verify else ingest(state_path=args.state, root=args.root, observation_root=args.observation_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
