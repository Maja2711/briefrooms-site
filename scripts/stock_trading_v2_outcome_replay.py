#!/usr/bin/env python3
"""Immutable multi-horizon counterfactual outcome replay for Stock Trading v2.

Candidate state is frozen at decision time. This module only looks forward from
that decision, applies one pre-registered execution model to selected and
rejected candidates, and writes an immutable record once a horizon is fully
known (or a terminal SL/TP event makes it known earlier).
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import stock_trading_v2_admission_ledger as admission
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_discovery as discovery
    from scripts import stock_trading_v2_experience_store as experience
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_admission_ledger as admission
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_discovery as discovery
    import stock_trading_v2_experience_store as experience

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_learning_config.json"
DEFAULT_ROOT = ROOT / "data/investments/stock_trading_v2_outcomes"
SCHEMA_VERSION = "stock-trading-v2-horizon-outcome-v1"


class ImmutableOutcomeConflict(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = _read_json(path)
    if config.get("schema_version") != "stock-trading-v2-learning-config-v1":
        raise contracts.ContractError("learning config schema mismatch")
    if config.get("governance", {}).get("production_decision_influence") is not False:
        raise contracts.ContractError("learning loop must remain shadow-only")
    horizons = [int(value) for value in config.get("horizons_sessions") or []]
    if not horizons or any(value <= 0 for value in horizons) or horizons != sorted(set(horizons)):
        raise contracts.ContractError("learning horizons must be positive, unique and sorted")
    return config


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bar_value(bar: Any, field: str) -> Any:
    if isinstance(bar, Mapping):
        return bar.get(field)
    return getattr(bar, field)


def _bar_day(bar: Any) -> str:
    value = _bar_value(bar, "day")
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _decision_date(event: Mapping[str, Any]) -> str:
    raw = str(event.get("decision_at") or "").strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError:
        return str(event.get("session_date") or "")


def extract_frozen_plan(event: Mapping[str, Any]) -> dict[str, Any] | None:
    contracts.validate_experience_event(event)
    state = event.get("candidate_state") or {}
    risk = state.get("risk_plan")
    if not isinstance(risk, Mapping):
        return None
    reference = _float(risk.get("reference_price"))
    stop = _float(risk.get("stop"))
    target = _float(risk.get("target"))
    if reference is None or stop is None or target is None or not (0 < stop < reference < target):
        return None
    entry_zone = risk.get("entry_zone") if isinstance(risk.get("entry_zone"), (list, tuple)) else []
    zone = [_float(value) for value in entry_zone]
    zone = [value for value in zone if value is not None]
    skip_above = _float(risk.get("skip_above"))
    if skip_above is None and zone:
        skip_above = max(zone)
    return {
        "reference_price": reference,
        "stop": stop,
        "target": target,
        "entry_zone": zone,
        "skip_above": skip_above,
        "frozen_reward_risk": _float(risk.get("reward_risk")),
        "frozen_risk_percent": _float(risk.get("risk_percent")),
    }


def _future_bars(event: Mapping[str, Any], bars: Sequence[Any]) -> list[Any]:
    decision_day = _decision_date(event)
    return sorted([bar for bar in bars if _bar_day(bar) > decision_day], key=_bar_day)


def replay_horizon(
    event: Mapping[str, Any],
    bars: Sequence[Any],
    *,
    horizon_sessions: int,
    cost_stress_percent: float,
    replay_version: str,
    champion_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a settled horizon outcome, or None while the horizon is unresolved."""
    if horizon_sessions <= 0:
        raise ValueError("horizon_sessions must be positive")
    plan = extract_frozen_plan(event)
    if plan is None:
        return {
            "status": "UNPLAYABLE",
            "reason": "missing_or_invalid_frozen_risk_plan",
            "horizon_sessions": horizon_sessions,
        }
    future = _future_bars(event, bars)
    if not future:
        return None

    first = future[0]
    entry = _float(_bar_value(first, "open"))
    if entry is None or entry <= 0:
        return {"status": "UNPLAYABLE", "reason": "next_session_open_missing", "horizon_sessions": horizon_sessions}
    skip_above = plan.get("skip_above")
    if skip_above is not None and entry > float(skip_above):
        return {
            "status": "NOT_ACTIVATED",
            "reason": "next_session_open_above_frozen_skip",
            "horizon_sessions": horizon_sessions,
            "entry_session": _bar_day(first),
            "observed_open": entry,
        }
    stop = float(plan["stop"])
    target = float(plan["target"])
    if entry <= stop:
        return {
            "status": "NOT_ACTIVATED",
            "reason": "next_session_open_at_or_below_frozen_stop",
            "horizon_sessions": horizon_sessions,
            "entry_session": _bar_day(first),
            "observed_open": entry,
        }
    if entry >= target:
        return {
            "status": "NOT_ACTIVATED",
            "reason": "next_session_open_at_or_above_frozen_target",
            "horizon_sessions": horizon_sessions,
            "entry_session": _bar_day(first),
            "observed_open": entry,
        }

    available = future[:horizon_sessions]
    risk_amount = entry - stop
    if risk_amount <= 0:
        return {"status": "UNPLAYABLE", "reason": "non_positive_replay_risk", "horizon_sessions": horizon_sessions}
    peak_high = entry
    trough_low = entry
    exit_price: float | None = None
    exit_reason: str | None = None
    exit_session: str | None = None
    sessions_held = 0

    for index, bar in enumerate(available, start=1):
        bar_open = float(_bar_value(bar, "open"))
        high = float(_bar_value(bar, "high"))
        low = float(_bar_value(bar, "low"))
        peak_high = max(peak_high, high)
        trough_low = min(trough_low, low)
        sessions_held = index
        if index > 1 and bar_open <= stop:
            exit_price = bar_open
            exit_reason = "GAP_STOP"
            exit_session = _bar_day(bar)
            break
        stop_hit = low <= stop
        target_hit = high >= target
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP_FIRST" if target_hit else "STOP"
            exit_session = _bar_day(bar)
            break
        if target_hit:
            exit_price = target
            exit_reason = "TARGET"
            exit_session = _bar_day(bar)
            break

    terminal = exit_price is not None
    if not terminal and len(future) < horizon_sessions:
        return None
    if not terminal:
        final_bar = available[-1]
        exit_price = float(_bar_value(final_bar, "close"))
        exit_reason = "HORIZON_CLOSE"
        exit_session = _bar_day(final_bar)
        sessions_held = horizon_sessions

    gross_return = float(exit_price) / entry - 1.0
    cost_fraction = max(0.0, float(cost_stress_percent)) / 100.0
    net_return = gross_return - cost_fraction
    risk_fraction = risk_amount / entry
    gross_r = gross_return / risk_fraction
    net_r = net_return / risk_fraction
    mfe = peak_high / entry - 1.0
    mae = trough_low / entry - 1.0
    champion = champion_observation.get("champion") if isinstance(champion_observation, Mapping) else None
    return {
        "status": "SETTLED",
        "reason": "terminal_before_horizon" if terminal and sessions_held < horizon_sessions else "horizon_resolved",
        "horizon_sessions": horizon_sessions,
        "replay_version": replay_version,
        "activation_model": "next_session_open_subject_to_frozen_skip_and_stop_target_geometry",
        "same_bar_policy": "stop_first_conservative",
        "entry_session": _bar_day(first),
        "entry_price": round(entry, 8),
        "frozen_reference_price": round(float(plan["reference_price"]), 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "exit_session": exit_session,
        "exit_price": round(float(exit_price), 8),
        "exit_reason": exit_reason,
        "sessions_held": sessions_held,
        "gross_return_percent": round(gross_return * 100.0, 8),
        "cost_stress_percent": round(float(cost_stress_percent), 8),
        "net_return_percent": round(net_return * 100.0, 8),
        "gross_r": round(gross_r, 8),
        "net_r": round(net_r, 8),
        "mfe_percent": round(mfe * 100.0, 8),
        "mae_percent": round(mae * 100.0, 8),
        "champion_action_at_freeze": champion.get("action") if isinstance(champion, Mapping) else None,
        "champion_reason_at_freeze": champion.get("reason") if isinstance(champion, Mapping) else None,
    }


def make_outcome(
    event: Mapping[str, Any],
    replay: Mapping[str, Any],
    *,
    replay_version: str,
    settled_at: str | None = None,
) -> dict[str, Any]:
    if replay.get("status") not in {"SETTLED", "NOT_ACTIVATED", "UNPLAYABLE"}:
        raise contracts.ContractError("cannot persist unresolved replay")
    horizon = int(replay["horizon_sessions"])
    outcome_id = "stoutv2-" + contracts.payload_sha256(
        {"event_id": event["event_id"], "horizon": horizon, "replay_version": replay_version}
    )[:24]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "outcome_id": outcome_id,
        "market": event["market"],
        "symbol": event["symbol"],
        "session_date": event["session_date"],
        "decision_at": event["decision_at"],
        "settled_at": settled_at or contracts.iso_utc(),
        "source_event_id": event["event_id"],
        "source_event_sha256": event["event_sha256"],
        "candidate_decision": event["decision"],
        "horizon_sessions": horizon,
        "replay": deepcopy(dict(replay)),
        "governance": {
            "immutable": True,
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "no_lookahead": True,
        },
    }
    body = dict(payload)
    payload["outcome_sha256"] = contracts.payload_sha256(body)
    validate_outcome(payload)
    return payload


def validate_outcome(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("outcome schema mismatch")
    if payload.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("outcome market unsupported")
    if int(payload.get("horizon_sessions") or 0) <= 0:
        raise contracts.ContractError("outcome horizon invalid")
    replay = payload.get("replay")
    if not isinstance(replay, Mapping) or replay.get("status") not in {"SETTLED", "NOT_ACTIVATED", "UNPLAYABLE"}:
        raise contracts.ContractError("outcome replay invalid")
    governance = payload.get("governance") or {}
    if governance.get("immutable") is not True or governance.get("production_decision_influence") is not False or governance.get("no_lookahead") is not True:
        raise contracts.ContractError("outcome governance invariant failed")
    body = dict(payload)
    stored = str(body.pop("outcome_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("outcome hash mismatch")


def outcome_path(root: Path, payload: Mapping[str, Any]) -> Path:
    return root / str(payload["market"]).lower() / str(payload["session_date"]) / f"{payload['outcome_id']}.json"


def persist(root: Path, payload: Mapping[str, Any]) -> bool:
    validate_outcome(payload)
    path = outcome_path(root, payload)
    if path.exists():
        existing = _read_json(path)
        validate_outcome(existing)
        left = deepcopy(existing)
        right = deepcopy(dict(payload))
        for body in (left, right):
            body.pop("settled_at", None)
            body.pop("outcome_sha256", None)
        if left == right:
            return False
        raise ImmutableOutcomeConflict(f"refusing to mutate settled outcome {payload['outcome_id']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)
    return True


def _load_admissions(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return result
    for path in root.rglob("*.json"):
        row = _read_json(path)
        admission.validate_observation(row)
        result[str(row["source_event_id"])] = row
    return result


def settle_store(
    *,
    experience_root: Path = experience.DEFAULT_STORE_ROOT,
    admission_root: Path = admission.DEFAULT_ROOT,
    outcome_root: Path = DEFAULT_ROOT,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    config = load_config(config_path)
    admissions = _load_admissions(admission_root)
    events = [_read_json(path) for path in experience.iter_event_files(experience_root)]
    by_market_symbol: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        contracts.validate_experience_event(event)
        by_market_symbol[(str(event["market"]), str(event["symbol"]))].append(event)

    network = _read_json(ROOT / "data/investments/stock_trading_v2_discovery_config.json").get("network") or {}
    hosts = [str(value) for value in network.get("provider_hosts") or ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]]
    bars_cache: dict[tuple[str, str], list[discovery.Bar]] = {}
    provider_failures: dict[str, str] = {}
    for key in by_market_symbol:
        market, symbol = key
        market_symbol = symbol
        try:
            bars, _ = discovery.fetch_yahoo_history(
                market_symbol,
                range_value="2y",
                timeout=int(network.get("timeout_seconds") or 15),
                attempts_per_host=int(network.get("attempts_per_host") or 2),
                hosts=hosts,
            )
            bars_cache[key] = bars
        except Exception as exc:
            provider_failures[f"{market}:{symbol}"] = f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:600]

    written = existing = unresolved = unplayable = 0
    for event in events:
        key = (str(event["market"]), str(event["symbol"]))
        bars = bars_cache.get(key)
        if not bars:
            unresolved += len(config["horizons_sessions"])
            continue
        market_cost = float((config.get("cost_stress_percent") or {}).get(event["market"], 0.0))
        replay_version = str((config.get("replay") or {}).get("version") or "unknown")
        champion = admissions.get(str(event["event_id"]))
        for horizon in config["horizons_sessions"]:
            replay = replay_horizon(
                event,
                bars,
                horizon_sessions=int(horizon),
                cost_stress_percent=market_cost,
                replay_version=replay_version,
                champion_observation=champion,
            )
            if replay is None:
                unresolved += 1
                continue
            if replay.get("status") == "UNPLAYABLE":
                unplayable += 1
            outcome = make_outcome(event, replay, replay_version=replay_version)
            if persist(outcome_root, outcome):
                written += 1
            else:
                existing += 1
    return {
        "schema_version": "stock-trading-v2-outcome-settlement-run-v1",
        "events": len(events),
        "symbols_with_history": len(bars_cache),
        "outcomes_written": written,
        "outcomes_existing": existing,
        "unresolved_horizons": unresolved,
        "unplayable_horizons": unplayable,
        "provider_failures": provider_failures,
        "production_decision_influence": False,
    }


def iter_outcomes(root: Path = DEFAULT_ROOT) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    return (_read_json(path) for path in sorted(root.rglob("*.json")))


def verify(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    ids: set[str] = set()
    count = 0
    for row in iter_outcomes(root):
        validate_outcome(row)
        if row["outcome_id"] in ids:
            raise contracts.ContractError("duplicate outcome id")
        ids.add(row["outcome_id"])
        count += 1
    return {"schema_version": SCHEMA_VERSION, "ok": True, "count": count, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experience-root", type=Path, default=experience.DEFAULT_STORE_ROOT)
    parser.add_argument("--admission-root", type=Path, default=admission.DEFAULT_ROOT)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.root) if args.verify else settle_store(
        experience_root=args.experience_root,
        admission_root=args.admission_root,
        outcome_root=args.root,
        config_path=args.config,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
