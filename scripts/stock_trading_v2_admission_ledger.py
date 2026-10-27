#!/usr/bin/env python3
"""Immutable Champion admission ledger for Stock Trading v2 shadow learning.

The Experience Store freezes what the candidate producer knew. This companion
ledger freezes what the *current canonical portfolio policy* would do with that
candidate before outcomes are known. Keeping admission separate preserves the
immutability of the original candidate event and makes later policy comparisons
causally auditable.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts import stock_trading_portfolio as portfolio
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_experience_store as experience
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_portfolio as portfolio
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_experience_store as experience

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_admission"
SCHEMA_VERSION = "stock-trading-v2-admission-observation-v1"
FORMAL_SCHEMA_VERSION = "stock-trading-v2-admission-observation-v2"
EPOCH_SCHEMA_VERSION = "stock-trading-v2-admission-epoch-v1"
MAX_FREEZE_DELAY_SECONDS = 900


class ImmutableAdmissionConflict(RuntimeError):
    pass


def _timestamp(raw: Any) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise contracts.ContractError("admission timestamp invalid") from exc
    if value.tzinfo is None:
        raise contracts.ContractError("admission timestamp requires timezone")
    return value.astimezone(timezone.utc)


def _context(event: Mapping[str, Any]) -> dict[str, Any]:
    value = (event.get("candidate_state") or {}).get("decision_context")
    if not isinstance(value, Mapping) or value.get("schema_version") != "stock-trading-v2-decision-context-v1":
        raise contracts.ContractError("formal admission requires frozen v2 decision context")
    context = deepcopy(dict(value))
    for name in ("policy", "opportunity_config", "portfolio"):
        snapshot = context.get(name + "_snapshot")
        if not isinstance(snapshot, Mapping) or context.get(name + "_sha256") != contracts.payload_sha256(snapshot):
            raise contracts.ContractError(f"formal admission frozen {name} hash mismatch")
    if context.get("market") != event.get("market") or context.get("decision_at") != event.get("decision_at"):
        raise contracts.ContractError("formal admission context source mismatch")
    opportunity = context.get("opportunity_snapshot")
    if not isinstance(opportunity, Mapping) or context.get("opportunity_sha256") != contracts.payload_sha256(opportunity):
        raise contracts.ContractError("formal admission frozen opportunity hash mismatch")
    return context


def _formal_champion(event: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    state = event.get("candidate_state") or {}
    path = state.get("decision_path") or {}
    score = state.get("score_state") or {}
    market_policy = (context["policy_snapshot"].get("markets") or {}).get(event["market"]) or {}
    utility = contracts.finite_number(score.get("portfolio_utility", score.get("utility")))
    margin = contracts.finite_number(market_policy.get("minimum_cash_edge_points"))
    cash = contracts.finite_number(path.get("cash_utility"))
    if utility is None or margin is None or cash is None or not 0 <= margin <= 25 or cash != 50:
        raise contracts.ContractError("formal admission requires finite v2 CASH contract")
    if contracts.finite_number(path.get("minimum_cash_edge_points")) != margin:
        raise contracts.ContractError("formal admission source margin differs from frozen policy")
    eligible = path.get("eligible") is True
    available = contracts.finite_number(path.get("available_slots"))
    free_slot = available is not None and available > 0
    held = path.get("already_held") is True
    action = str(path.get("portfolio_action") or path.get("action") or "").upper()
    rotation = action == "REPLACE" or (event.get("selected") is True and not free_slot)
    long = event.get("selected") is True and eligible and not held and free_slot and utility >= cash + margin and not rotation
    if long:
        reason = "selected_cash_margin_entry"
    elif path.get("cash_margin_only_blocker") is True and eligible and not held and free_slot and not rotation:
        reason = "cash_admission_margin"
    else:
        blocker = path.get("first_blocking_gate") or state.get("first_blocking_gate") or ("rotation_excluded" if rotation else "producer_rejected")
        reason = str(blocker.get("name") or blocker.get("reason")) if isinstance(blocker, Mapping) else str(blocker)
    return "LONG" if long else "CASH", reason, {
        "component_id": "cash_admission_margin", "candidate_utility": utility,
        "cash_utility": cash, "minimum_cash_edge_points": margin,
        "candidate_cash_edge_points": utility - cash, "eligible": eligible,
        "already_held": held, "available_slots": available, "rotation_excluded": rotation,
        "decision_context_sha256": contracts.payload_sha256(context),
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _pseudo_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    state = event.get("candidate_state") or {}
    score = state.get("score_state") or {}
    risk = state.get("risk_plan") or {}
    path = state.get("decision_path") or {}
    market_state = state.get("market_state") or {}
    ev = score.get("expected_value") or {}
    reference = risk.get("reference_price")
    selected = bool(event.get("selected"))
    market = str(event.get("market") or "").upper()
    producer_decision = path.get("producer_decision")
    if not producer_decision:
        producer_decision = "TRANSAKCJA" if market == "GPW" and selected else "TRADE" if market == "US" and selected else "NO_TRADE"

    historical_gate = market_state.get("historical_data_gate") or {}
    execution_gate = market_state.get("execution_data_gate") or {}
    healthy = (
        historical_gate.get("accepted") is not False
        and str(execution_gate.get("status") or "accepted").lower() not in {"rejected", "conflict", "stale"}
    )
    selection_mode = path.get("selection_mode")
    mandatory_applied = str(selection_mode or "").upper() == "MANDATORY_DAILY_FINAL"
    return {
        "date": event.get("session_date"),
        "generated_at": event.get("decision_at"),
        "decision": producer_decision,
        "data_quality": {
            "status": "healthy" if healthy else "degraded",
            "mandatory_selection": {"applied": mandatory_applied},
        },
        "selection": {
            "symbol": event.get("symbol"),
            "ticker": (state.get("identity") or {}).get("ticker"),
            "name": (state.get("identity") or {}).get("name"),
            "sector": (state.get("identity") or {}).get("sector"),
            "selection_mode": selection_mode,
            "score": score.get("score", score.get("legacy_composite_score")),
            "reward_risk": risk.get("reward_risk"),
            "risk_percent": risk.get("risk_percent"),
            "reference_price": reference,
            "market_snapshot": {"last": reference},
            "stop": risk.get("stop"),
            "target": risk.get("target"),
            "expected_value_model": deepcopy(dict(ev)) if isinstance(ev, Mapping) else {},
        },
    }


def champion_admission(
    event: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    contracts.validate_experience_event(event)
    if isinstance((event.get("candidate_state") or {}).get("decision_context"), Mapping):
        return _formal_champion(event, _context(event))
    if event.get("selected") is not True:
        state = event.get("candidate_state") or {}
        decision_path = state.get("decision_path") or {}
        blocker = decision_path.get("first_blocking_gate") or state.get("first_blocking_gate")
        reason = f"producer_rejected:{blocker or 'unspecified'}"
        return "CASH", reason, {"producer_selected": False, "portfolio_qualification_ran": False}

    pseudo = _pseudo_payload(event)
    ok, reason = portfolio.qualify_candidate(str(event["market"]), pseudo, policy)
    return (
        "LONG" if ok else "CASH",
        reason,
        {
            "producer_selected": True,
            "portfolio_qualification_ran": True,
            "reconstructed_payload_sha256": contracts.payload_sha256(pseudo),
        },
    )


def make_observation(
    event: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    recorded_at: str | None = None,
    epoch: Mapping[str, Any] | None = None,
    retrospective_reason: str | None = None,
) -> dict[str, Any]:
    context = _context(event) if epoch is not None else None
    if context is not None:
        policy = context["policy_snapshot"]
    if retrospective_reason is not None:
        contracts.validate_experience_event(event)
        action, reason, diagnostics = "CASH", "retrospective_not_formal", {"exclusion_reason": retrospective_reason, "portfolio_qualification_ran": False}
    else:
        action, reason, diagnostics = champion_admission(event, policy)
    policy_fingerprint = contracts.payload_sha256(policy)
    raw_id = {
        "source_event_id": event["event_id"],
        "policy_version": policy.get("policy_version"),
        "policy_sha256": policy_fingerprint,
    }
    if context is not None:
        # One prospective baseline per source event, independent of later policy changes.
        raw_id = {"source_event_id": event["event_id"], "contract": FORMAL_SCHEMA_VERSION}
    observation_id = "stadv2-" + contracts.payload_sha256(raw_id)[:24]
    payload: dict[str, Any] = {
        "schema_version": FORMAL_SCHEMA_VERSION if context is not None else SCHEMA_VERSION,
        "observation_id": observation_id,
        "market": event["market"],
        "session_date": event["session_date"],
        "symbol": event["symbol"],
        "decision_at": event["decision_at"],
        "recorded_at": recorded_at or contracts.iso_utc(),
        "source_event_id": event["event_id"],
        "source_event_sha256": event["event_sha256"],
        "candidate_decision": event["decision"],
        "champion": {
            "policy_version": policy.get("policy_version"),
            "policy_sha256": policy_fingerprint,
            "action": action,
            "reason": reason,
            "diagnostics": diagnostics,
        },
        "governance": {
            "immutable": True,
            "prospective_before_outcome": True,
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
        },
    }
    if context is not None:
        payload["observation_mode"] = "PROSPECTIVE_FORMAL"
        payload["epoch"] = deepcopy(dict(epoch))
        payload["source_recorded_at"] = event["recorded_at"]
        payload["source_event_snapshot"] = deepcopy(dict(event))
        payload["champion"]["policy_snapshot"] = deepcopy(dict(policy))
        payload["admission_contract"] = deepcopy(diagnostics)
    if retrospective_reason is not None:
        payload["observation_mode"] = "RETROSPECTIVE_DIAGNOSTIC"
        payload["exclusion_reason"] = retrospective_reason
        payload["governance"]["prospective_before_outcome"] = False
        payload["governance"]["formal_holdout_eligible"] = False
    body = dict(payload)
    payload["observation_sha256"] = contracts.payload_sha256(body)
    validate_observation(payload)
    return payload


def validate_observation(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") not in {SCHEMA_VERSION, FORMAL_SCHEMA_VERSION}:
        raise contracts.ContractError("admission observation schema mismatch")
    if payload.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("admission observation market unsupported")
    for key in ("observation_id", "source_event_id", "source_event_sha256", "symbol", "decision_at", "recorded_at"):
        if not str(payload.get(key) or "").strip():
            raise contracts.ContractError(f"admission observation missing {key}")
    champion = payload.get("champion")
    if not isinstance(champion, Mapping) or champion.get("action") not in {"LONG", "CASH"}:
        raise contracts.ContractError("invalid champion admission")
    governance = payload.get("governance") or {}
    if governance.get("immutable") is not True or governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("admission governance invariant failed")
    body = dict(payload)
    stored = str(body.pop("observation_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("admission observation hash mismatch")
    if payload.get("schema_version") == FORMAL_SCHEMA_VERSION:
        event = payload.get("source_event_snapshot")
        if not isinstance(event, Mapping):
            raise contracts.ContractError("formal admission missing source event")
        contracts.validate_experience_event(event)
        context = _context(event)
        for key, source_key in (("source_event_id", "event_id"), ("source_event_sha256", "event_sha256"), ("source_recorded_at", "recorded_at"), ("decision_at", "decision_at"), ("market", "market"), ("symbol", "symbol")):
            if payload.get(key) != event.get(source_key):
                raise contracts.ContractError("formal admission source binding mismatch")
        frozen_policy = context["policy_snapshot"]
        if champion.get("policy_snapshot") != frozen_policy or champion.get("policy_sha256") != context["policy_sha256"] or champion.get("policy_version") != frozen_policy.get("policy_version"):
            raise contracts.ContractError("formal admission policy binding mismatch")
        epoch = payload.get("epoch")
        validate_epoch(epoch)
        decision, source_recorded, observed = (_timestamp(payload[key]) for key in ("decision_at", "source_recorded_at", "recorded_at"))
        activation = _timestamp(epoch["activated_at"])
        if not activation <= decision <= source_recorded <= observed or (observed - decision).total_seconds() > MAX_FREEZE_DELAY_SECONDS:
            raise contracts.ContractError("formal admission was not frozen prospectively")
        action, reason, diagnostics = _formal_champion(event, context)
        if champion.get("action") != action or champion.get("reason") != reason or champion.get("diagnostics") != diagnostics or payload.get("admission_contract") != diagnostics:
            raise contracts.ContractError("formal admission decision proof mismatch")
        expected_id = "stadv2-" + contracts.payload_sha256({"source_event_id": event["event_id"], "contract": FORMAL_SCHEMA_VERSION})[:24]
        if payload.get("observation_id") != expected_id or payload.get("observation_mode") != "PROSPECTIVE_FORMAL" or governance.get("prospective_before_outcome") is not True:
            raise contracts.ContractError("formal admission identity/governance mismatch")


def is_formal_observation(payload: Mapping[str, Any]) -> bool:
    validate_observation(payload)
    return payload.get("schema_version") == FORMAL_SCHEMA_VERSION and payload.get("observation_mode") == "PROSPECTIVE_FORMAL"


def validate_epoch(payload: Any) -> None:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != EPOCH_SCHEMA_VERSION:
        raise contracts.ContractError("admission epoch schema mismatch")
    _timestamp(payload.get("activated_at"))
    body = dict(payload)
    digest = body.pop("epoch_sha256", None)
    if digest != contracts.payload_sha256(body) or payload.get("immutable") is not True:
        raise contracts.ContractError("admission epoch hash/governance mismatch")


def ensure_epoch(root: Path, policy: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    path = root.parent / (root.name + "_epochs") / "prospective-v2.json"
    if path.exists():
        epoch = _read_json(path)
        validate_epoch(epoch)
        return epoch
    epoch = {"schema_version": EPOCH_SCHEMA_VERSION, "activated_at": now, "initial_policy_sha256": contracts.payload_sha256(policy), "immutable": True}
    epoch["epoch_sha256"] = contracts.payload_sha256(epoch)
    validate_epoch(epoch)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(epoch, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        return ensure_epoch(root, policy, now=now)
    return epoch


def observation_path(root: Path, payload: Mapping[str, Any]) -> Path:
    return root / str(payload["market"]).lower() / str(payload["session_date"]) / f"{payload['observation_id']}.json"


def persist(root: Path, payload: Mapping[str, Any]) -> bool:
    validate_observation(payload)
    path = observation_path(root, payload)
    if is_formal_observation(payload):
        epoch_path = root.parent / (root.name + "_epochs") / "prospective-v2.json"
        if not epoch_path.exists() or _read_json(epoch_path) != payload.get("epoch"):
            raise contracts.ContractError("formal admission does not bind the persisted epoch")
        for prior in iter_observations(root):
            if prior["source_event_id"] == payload["source_event_id"] and prior["observation_id"] != payload["observation_id"]:
                raise ImmutableAdmissionConflict("source event already has a frozen Champion baseline")
    if path.exists():
        existing = _read_json(path)
        validate_observation(existing)
        left = deepcopy(existing)
        right = deepcopy(dict(payload))
        for body in (left, right):
            body.pop("recorded_at", None)
            body.pop("observation_sha256", None)
        if left == right:
            return False
        raise ImmutableAdmissionConflict(f"refusing to mutate {payload['observation_id']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    try:
        if os.name == "nt":
            temp.rename(path)
        else:
            os.link(temp, path)
    except FileExistsError:
        return persist(root, payload)
    finally:
        temp.unlink(missing_ok=True)
    return True


def iter_events(root: Path = experience.DEFAULT_STORE_ROOT) -> Iterable[dict[str, Any]]:
    for path in experience.iter_event_files(root):
        yield _read_json(path)


def iter_observations(root: Path = DEFAULT_ROOT, *, formal_only: bool = False) -> Iterable[dict[str, Any]]:
    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        row = _read_json(path)
        validate_observation(row)
        if not formal_only or is_formal_observation(row):
            yield row


def freeze_all(
    *,
    experience_root: Path = experience.DEFAULT_STORE_ROOT,
    admission_root: Path = DEFAULT_ROOT,
    policy_path: Path = portfolio.POLICY_PATH,
    now: str | None = None,
) -> dict[str, Any]:
    policy = portfolio.load_policy(policy_path)
    stamp = now or contracts.iso_utc()
    epoch = ensure_epoch(admission_root, policy, now=stamp)
    frozen = {str(row["source_event_id"]): row for row in iter_observations(admission_root)}
    seen = written = existing = 0
    retrospective = 0
    actions = {"LONG": 0, "CASH": 0}
    reasons: dict[str, int] = {}
    for event in iter_events(experience_root):
        seen += 1
        contracts.validate_experience_event(event)
        if str(event["event_id"]) in frozen:
            # Never reinterpret an existing event with today's Champion.
            existing += 1
            continue
        try:
            observation = make_observation(event, policy=policy, recorded_at=stamp, epoch=epoch)
        except contracts.ContractError as exc:
            observation = make_observation(event, policy=policy, recorded_at=stamp, retrospective_reason=str(exc))
            persist(admission_root.parent / (admission_root.name + "_retrospective"), observation)
            retrospective += 1
            continue
        actions[observation["champion"]["action"]] += 1
        reason = str(observation["champion"]["reason"])
        reasons[reason] = reasons.get(reason, 0) + 1
        if persist(admission_root, observation):
            written += 1
            frozen[str(event["event_id"])] = observation
        else:
            existing += 1
    return {
        "schema_version": "stock-trading-v2-admission-ledger-run-v1",
        "events_seen": seen,
        "observations_written": written,
        "observations_existing": existing,
        "retrospective_excluded": retrospective,
        "epoch_activated_at": epoch["activated_at"],
        "actions": actions,
        "reasons": reasons,
        "production_decision_influence": False,
    }


def verify(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    count = 0
    ids: set[str] = set()
    formal_sources: set[str] = set()
    epoch_path = root.parent / (root.name + "_epochs") / "prospective-v2.json"
    epoch = _read_json(epoch_path) if epoch_path.exists() else None
    if epoch is not None:
        validate_epoch(epoch)
    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        payload = _read_json(path)
        validate_observation(payload)
        if payload["observation_id"] in ids:
            raise contracts.ContractError("duplicate admission observation id")
        ids.add(payload["observation_id"])
        if is_formal_observation(payload):
            if epoch is None or payload.get("epoch") != epoch:
                raise contracts.ContractError("formal admission does not bind the persisted epoch")
            if payload["source_event_id"] in formal_sources:
                raise contracts.ContractError("duplicate formal Champion source baseline")
            formal_sources.add(payload["source_event_id"])
        count += 1
    return {"schema_version": SCHEMA_VERSION, "ok": True, "count": count, "formal_count": len(formal_sources), "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experience-root", type=Path, default=experience.DEFAULT_STORE_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--policy", "--policy-snapshot", type=Path, default=portfolio.POLICY_PATH)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--activate-epoch", action="store_true", help="Create the immutable prospective boundary before discovery; do not freeze source events")
    args = parser.parse_args()
    if args.activate_epoch:
        result = ensure_epoch(args.root, portfolio.load_policy(args.policy), now=contracts.iso_utc())
    elif args.verify:
        result = verify(args.root)
    else:
        result = freeze_all(experience_root=args.experience_root, admission_root=args.root, policy_path=args.policy)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
