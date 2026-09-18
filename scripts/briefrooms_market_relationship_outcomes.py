#!/usr/bin/env python3
"""Prospective follow-through settlement for BriefRooms relationship triggers.

This module settles only the small set of trigger observations frozen by
briefrooms_market_relationship_trigger.py. It never scans the full market.

For each frozen observation it measures future 1/3/5/20-session returns from the
observation session close, direction-adjusted continuation, MFE/MAE and whether
the hypothesised move actually followed through. Outcomes are immutable and are
aggregated into a research report by trigger type / event relation.

No production policy is changed automatically.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_discovery as discovery
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_discovery as discovery

ROOT = Path(__file__).resolve().parents[1]
HISTORY_ROOT = ROOT / "data/investments/market_relationship_trigger_history"
OUTCOME_ROOT = ROOT / "data/investments/market_relationship_trigger_outcomes"
REPORT_PATH = ROOT / "data/investments/market_relationship_trigger_learning.json"

OUTCOME_SCHEMA = "briefrooms-market-relationship-outcome-v1"
REPORT_SCHEMA = "briefrooms-market-relationship-learning-v1"
HORIZONS = (1, 3, 5, 20)
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


class RelationshipOutcomeError(RuntimeError):
    pass


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def iter_observations(root: Path = HISTORY_ROOT) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, Mapping):
            continue
        if payload.get("schema_version") != "briefrooms-market-relationship-observation-v1":
            continue
        if str(payload.get("market") or "").upper() != "US":
            continue
        rows.append(dict(payload))
    return rows


def _bar_day(bar: Any) -> str:
    value = bar.day if hasattr(bar, "day") else (bar.get("day") if isinstance(bar, Mapping) else "")
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _bar_value(bar: Any, field: str) -> float | None:
    value = getattr(bar, field, None) if not isinstance(bar, Mapping) else bar.get(field)
    return _finite(value)


def settle_horizon(
    observation: Mapping[str, Any],
    bars: Sequence[Any],
    *,
    horizon_sessions: int,
) -> dict[str, Any] | None:
    if horizon_sessions not in HORIZONS:
        raise ValueError("unsupported relationship outcome horizon")
    session = str(observation.get("session_date") or "")
    direction = str(observation.get("direction") or "").upper()
    if direction not in {"UP", "DOWN"} or not session:
        return {
            "status": "UNPLAYABLE",
            "reason": "missing_direction_or_session",
            "horizon_sessions": horizon_sessions,
        }

    ordered = sorted(bars, key=_bar_day)
    future = [bar for bar in ordered if _bar_day(bar) > session]
    if len(future) < horizon_sessions:
        return None

    reference_state = observation.get("reference_market_state") or {}
    reference = _finite(reference_state.get("price"))
    if reference_state.get("point_in_time_frozen") is not True:
        return {
            "status": "UNPLAYABLE",
            "reason": "reference_not_point_in_time_frozen",
            "horizon_sessions": horizon_sessions,
        }
    if reference is None or reference <= 0:
        return {
            "status": "UNPLAYABLE",
            "reason": "frozen_reference_price_missing",
            "horizon_sessions": horizon_sessions,
        }

    terminal = _bar_value(future[horizon_sessions - 1], "close")
    if terminal is None:
        return {
            "status": "UNPLAYABLE",
            "reason": "terminal_close_missing",
            "horizon_sessions": horizon_sessions,
        }

    window = future[:horizon_sessions]
    highs = [_bar_value(bar, "high") for bar in window]
    lows = [_bar_value(bar, "low") for bar in window]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]

    raw_return = terminal / reference - 1.0
    sign = 1.0 if direction == "UP" else -1.0
    signed_return = raw_return * sign

    if direction == "UP":
        mfe = (max(highs) / reference - 1.0) if highs else raw_return
        mae = (min(lows) / reference - 1.0) if lows else raw_return
    else:
        mfe = (1.0 - min(lows) / reference) if lows else -raw_return
        mae = (1.0 - max(highs) / reference) if highs else -raw_return

    return {
        "status": "SETTLED",
        "horizon_sessions": horizon_sessions,
        "reference_session": str(reference_state.get("session_date") or session),
        "reference_price": round(reference, 8),
        "reference_observed_at": reference_state.get("observed_at"),
        "reference_price_source": reference_state.get("source"),
        "reference_point_in_time_frozen": True,
        "terminal_session": _bar_day(window[-1]),
        "terminal_close": round(terminal, 8),
        "raw_return": round(raw_return, 8),
        "directional_return": round(signed_return, 8),
        "continuation": signed_return > 0.0,
        "strong_continuation": signed_return >= 0.02,
        "mfe_directional": round(float(mfe), 8),
        "mae_directional": round(float(mae), 8),
    }


def outcome_id(observation_id: str, horizon: int) -> str:
    return "relout-" + contracts.payload_sha256(
        {"observation_id": observation_id, "horizon_sessions": int(horizon)}
    )[:24]


def build_outcome(
    observation: Mapping[str, Any],
    replay: Mapping[str, Any],
    *,
    settled_at: str | None = None,
) -> dict[str, Any]:
    if replay.get("status") not in {"SETTLED", "UNPLAYABLE"}:
        raise contracts.ContractError("relationship outcome cannot persist unresolved replay")
    horizon = int(replay.get("horizon_sessions") or 0)
    oid = outcome_id(str(observation.get("observation_id") or ""), horizon)
    strongest = ((observation.get("event_context") or [{}])[0] or {})
    payload: dict[str, Any] = {
        "schema_version": OUTCOME_SCHEMA,
        "outcome_id": oid,
        "observation_id": observation.get("observation_id"),
        "observation_sha256": observation.get("observation_sha256"),
        "market": observation.get("market"),
        "symbol": observation.get("symbol"),
        "observed_at": observation.get("observed_at"),
        "session_date": observation.get("session_date"),
        "settled_at": settled_at or _iso_now(),
        "horizon_sessions": horizon,
        "trigger_type": observation.get("trigger_type"),
        "direction": observation.get("direction"),
        "attention_score": observation.get("attention_score"),
        "attention_source": observation.get("attention_source"),
        "event_relation": strongest.get("relation"),
        "event_kind": strongest.get("event_kind"),
        "event_domain": strongest.get("event_domain"),
        "strongest_event_id": strongest.get("event_id"),
        "peer_agreement_rate": (observation.get("peer_context") or {}).get("agreement_rate"),
        "peer_leader_symbol": (observation.get("peer_context") or {}).get("leader_symbol"),
        "lead_lag_watch": bool((observation.get("peer_context") or {}).get("lead_lag_watch")),
        "replay": dict(replay),
        "governance": {
            "immutable": True,
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "future_only": True,
        },
    }
    payload["outcome_sha256"] = contracts.payload_sha256(payload)
    validate_outcome(payload)
    return payload


def validate_outcome(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != OUTCOME_SCHEMA:
        raise contracts.ContractError("relationship outcome schema mismatch")
    if payload.get("market") != "US":
        raise contracts.ContractError("relationship outcome market mismatch")
    horizon = int(payload.get("horizon_sessions") or 0)
    if horizon not in HORIZONS:
        raise contracts.ContractError("relationship outcome horizon unsupported")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("relationship outcome escaped shadow governance")
    if governance.get("automatic_policy_writeback") is not False:
        raise contracts.ContractError("relationship outcome cannot auto-write policy")
    replay = payload.get("replay") or {}
    if replay.get("status") not in {"SETTLED", "UNPLAYABLE"}:
        raise contracts.ContractError("relationship outcome unresolved replay persisted")
    body = dict(payload)
    stored = str(body.pop("outcome_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("relationship outcome hash mismatch")


def outcome_path(root: Path, payload: Mapping[str, Any]) -> Path:
    symbol = str(payload.get("symbol") or "").replace("/", "-")
    day = str(payload.get("session_date") or "unknown")
    horizon = int(payload.get("horizon_sessions") or 0)
    oid = str(payload.get("outcome_id") or "")
    return root / "us" / day / symbol / f"{oid}-h{horizon}.json"


def persist_outcome(root: Path, payload: Mapping[str, Any]) -> bool:
    validate_outcome(payload)
    path = outcome_path(root, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_json(path)
        if isinstance(existing, Mapping):
            validate_outcome(existing)
            left = dict(existing)
            right = dict(payload)
            left.pop("settled_at", None)
            right.pop("settled_at", None)
            left.pop("outcome_sha256", None)
            right.pop("outcome_sha256", None)
            if left == right:
                return False
        raise contracts.ContractError(f"immutable relationship outcome conflict: {path}")
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.replace(path)
    return True


def iter_outcomes(root: Path = OUTCOME_ROOT) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, Mapping):
            continue
        validate_outcome(payload)
        rows.append(dict(payload))
    return rows


def build_learning_report(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[int, str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in outcomes:
        replay = row.get("replay") or {}
        if replay.get("status") != "SETTLED":
            continue
        key = (
            int(row.get("horizon_sessions") or 0),
            str(row.get("attention_source") or "unknown"),
            str(row.get("trigger_type") or "UNKNOWN"),
            str(row.get("event_relation") or "none"),
            str(row.get("event_kind") or "none"),
        )
        groups[key].append(row)

    group_rows: list[dict[str, Any]] = []
    for (horizon, attention_source, trigger_type, relation, event_kind), rows in sorted(groups.items()):
        returns = [
            float((row.get("replay") or {}).get("directional_return") or 0.0)
            for row in rows
        ]
        continuation = [
            bool((row.get("replay") or {}).get("continuation"))
            for row in rows
        ]
        symbols = {str(row.get("symbol") or "") for row in rows}
        lead_lag = [row for row in rows if row.get("lead_lag_watch") is True]
        group_rows.append({
            "horizon_sessions": horizon,
            "attention_source": attention_source,
            "trigger_type": trigger_type,
            "event_relation": relation,
            "event_kind": event_kind,
            "observations": len(rows),
            "unique_symbols": len(symbols),
            "mean_directional_return": round(statistics.mean(returns), 8) if returns else None,
            "median_directional_return": round(statistics.median(returns), 8) if returns else None,
            "continuation_rate": round(sum(continuation) / len(continuation), 8) if continuation else None,
            "lead_lag_observations": len(lead_lag),
            "promotion_state": (
                "ELIGIBLE_FOR_WEIGHT_CHALLENGER"
                if attention_source == "trigger"
                and len(rows) >= 30
                and len(symbols) >= 8
                and statistics.mean(returns) > 0
                and sum(continuation) / len(continuation) >= 0.58
                else "CONTROL_ARM_ONLY"
                if attention_source == "exploration"
                else "COLLECT_MORE_PROSPECTIVE_EVIDENCE"
            ),
        })

    settled = [
        row for row in outcomes
        if (row.get("replay") or {}).get("status") == "SETTLED"
    ]
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _iso_now(),
        "mode": "shadow_prospective_relationship_learning",
        "settled_outcomes": len(settled),
        "outcome_records": len(outcomes),
        "groups": group_rows,
        "promotion_policy": {
            "automatic_promotion": False,
            "exploration_control_cannot_promote_weights": True,
            "minimum_group_observations": 30,
            "minimum_unique_symbols": 8,
            "minimum_continuation_rate": 0.58,
            "positive_mean_directional_return_required": True,
            "requires_separate_challenger_holdout": True,
        },
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "correlation_is_not_causation": True,
            "prospective_only": True,
        },
    }
    payload["report_sha256"] = contracts.payload_sha256(payload)
    validate_report(payload)
    return payload


def validate_report(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != REPORT_SCHEMA:
        raise contracts.ContractError("relationship learning report schema mismatch")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False:
        raise contracts.ContractError("relationship learning escaped shadow governance")
    if governance.get("automatic_policy_writeback") is not False:
        raise contracts.ContractError("relationship learning cannot auto-write policy")
    body = dict(payload)
    stored = str(body.pop("report_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("relationship learning report hash mismatch")


def fetch_history(symbol: str) -> list[discovery.Bar]:
    bars, _ = discovery.fetch_yahoo_history(
        symbol,
        range_value="6mo",
        timeout=15,
        attempts_per_host=2,
        hosts=YAHOO_HOSTS,
    )
    return bars


def _business_days_elapsed(session_date: str, today: date) -> int:
    try:
        start = date.fromisoformat(str(session_date))
    except ValueError:
        return 0
    if today <= start:
        return 0
    count = 0
    cursor = start + timedelta(days=1)
    while cursor <= today:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def _expected_outcome_path(root: Path, observation: Mapping[str, Any], horizon: int) -> Path:
    symbol = str(observation.get("symbol") or "").replace("/", "-")
    day = str(observation.get("session_date") or "unknown")
    oid = outcome_id(str(observation.get("observation_id") or ""), horizon)
    return root / "us" / day / symbol / f"{oid}-h{horizon}.json"


def due_horizons(
    observation: Mapping[str, Any],
    *,
    outcome_root: Path,
    today: date,
) -> list[int]:
    elapsed = _business_days_elapsed(str(observation.get("session_date") or ""), today)
    return [
        horizon
        for horizon in HORIZONS
        if elapsed >= horizon and not _expected_outcome_path(outcome_root, observation, horizon).exists()
    ]


def settle_all(
    *,
    history_root: Path = HISTORY_ROOT,
    outcome_root: Path = OUTCOME_ROOT,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    observations = list(iter_observations(history_root))
    today = datetime.now(timezone.utc).date()
    by_symbol: dict[str, list[tuple[dict[str, Any], list[int]]]] = defaultdict(list)
    pending_not_due = 0
    for observation in observations:
        due = due_horizons(observation, outcome_root=outcome_root, today=today)
        if not due:
            if any(
                not _expected_outcome_path(outcome_root, observation, horizon).exists()
                for horizon in HORIZONS
            ):
                pending_not_due += 1
            continue
        by_symbol[str(observation.get("symbol") or "")].append((observation, due))

    written = existing = unresolved = unplayable = provider_errors = 0
    for symbol, rows in sorted(by_symbol.items()):
        if not symbol:
            continue
        try:
            bars = fetch_history(symbol)
        except Exception:
            provider_errors += 1
            continue
        for observation, due in rows:
            for horizon in due:
                replay = settle_horizon(observation, bars, horizon_sessions=horizon)
                if replay is None:
                    unresolved += 1
                    continue
                if replay.get("status") == "UNPLAYABLE":
                    unplayable += 1
                payload = build_outcome(observation, replay)
                if persist_outcome(outcome_root, payload):
                    written += 1
                else:
                    existing += 1

    outcomes = list(iter_outcomes(outcome_root))
    report = build_learning_report(outcomes)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "observations": len(observations),
        "symbols": len(by_symbol),
        "written": written,
        "existing": existing,
        "unresolved": unresolved,
        "unplayable": unplayable,
        "provider_errors": provider_errors,
        "pending_not_due": pending_not_due,
        "symbols_fetched": len(by_symbol),
        "settled_outcomes": report["settled_outcomes"],
        "production_decision_influence": False,
    }


def verify(
    *,
    outcome_root: Path = OUTCOME_ROOT,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    outcomes = list(iter_outcomes(outcome_root))
    report = _read_json(report_path)
    if isinstance(report, Mapping):
        validate_report(report)
    return {
        "ok": True,
        "outcomes": len(outcomes),
        "report_present": isinstance(report, Mapping),
        "production_decision_influence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-root", type=Path, default=HISTORY_ROOT)
    parser.add_argument("--outcome-root", type=Path, default=OUTCOME_ROOT)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify(outcome_root=args.outcome_root, report_path=args.report)
    else:
        result = settle_all(
            history_root=args.history_root,
            outcome_root=args.outcome_root,
            report_path=args.report,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
