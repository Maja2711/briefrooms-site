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
import tempfile
from copy import deepcopy
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


class ImmutableAdmissionConflict(RuntimeError):
    pass


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
) -> dict[str, Any]:
    action, reason, diagnostics = champion_admission(event, policy)
    policy_fingerprint = contracts.payload_sha256(policy)
    raw_id = {
        "source_event_id": event["event_id"],
        "policy_version": policy.get("policy_version"),
        "policy_sha256": policy_fingerprint,
    }
    observation_id = "stadv2-" + contracts.payload_sha256(raw_id)[:24]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
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
    body = dict(payload)
    payload["observation_sha256"] = contracts.payload_sha256(body)
    validate_observation(payload)
    return payload


def validate_observation(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
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


def observation_path(root: Path, payload: Mapping[str, Any]) -> Path:
    return root / str(payload["market"]).lower() / str(payload["session_date"]) / f"{payload['observation_id']}.json"


def persist(root: Path, payload: Mapping[str, Any]) -> bool:
    validate_observation(payload)
    path = observation_path(root, payload)
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
    temp.replace(path)
    return True


def iter_events(root: Path = experience.DEFAULT_STORE_ROOT) -> Iterable[dict[str, Any]]:
    for path in experience.iter_event_files(root):
        yield _read_json(path)


def freeze_all(
    *,
    experience_root: Path = experience.DEFAULT_STORE_ROOT,
    admission_root: Path = DEFAULT_ROOT,
    policy_path: Path = portfolio.POLICY_PATH,
) -> dict[str, Any]:
    policy = portfolio.load_policy(policy_path)
    seen = written = existing = 0
    actions = {"LONG": 0, "CASH": 0}
    reasons: dict[str, int] = {}
    for event in iter_events(experience_root):
        seen += 1
        observation = make_observation(event, policy=policy)
        actions[observation["champion"]["action"]] += 1
        reason = str(observation["champion"]["reason"])
        reasons[reason] = reasons.get(reason, 0) + 1
        if persist(admission_root, observation):
            written += 1
        else:
            existing += 1
    return {
        "schema_version": "stock-trading-v2-admission-ledger-run-v1",
        "events_seen": seen,
        "observations_written": written,
        "observations_existing": existing,
        "actions": actions,
        "reasons": reasons,
        "production_decision_influence": False,
    }


def verify(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    count = 0
    ids: set[str] = set()
    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        payload = _read_json(path)
        validate_observation(payload)
        if payload["observation_id"] in ids:
            raise contracts.ContractError("duplicate admission observation id")
        ids.add(payload["observation_id"])
        count += 1
    return {"schema_version": SCHEMA_VERSION, "ok": True, "count": count, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experience-root", type=Path, default=experience.DEFAULT_STORE_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--policy", type=Path, default=portfolio.POLICY_PATH)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.root) if args.verify else freeze_all(
        experience_root=args.experience_root,
        admission_root=args.root,
        policy_path=args.policy,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
