#!/usr/bin/env python3
"""Prospective settlement layer for rejected GPW Daily Stock candidates.

The production selector remains authoritative. This module is observational only:

1. ``capture`` durably copies the already-existing immutable PR29.1 rejected-
   candidate freeze while the decision is still current.
2. ``settle`` observes the frozen selected plan and rejected risk plans over the
   same 1- and 2-session horizons.
3. Opportunity cost is computed only across rejected candidates that were not
   blocked by a hard decision gate. Hard-gate rejects are still observed so the
   future research loop can measure gate value, but they are never presented as
   legal alternatives to the selected trade.

There is deliberately no historical reconstruction. A decision without a
pre-existing prospective freeze cannot enter this dataset. Missing market data
is DATA_GAP, never zero return. This layer has no ranking, gate, execution,
position-sizing, learning or promotion authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from copy import deepcopy
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

try:
    from scripts import daily_stock_rejected_candidate_freeze as rejected_freeze
    from scripts import gpw_daily_outcome_monitor as selected_monitor
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:  # pragma: no cover - direct execution from scripts/
    import daily_stock_rejected_candidate_freeze as rejected_freeze
    import gpw_daily_outcome_monitor as selected_monitor
    import gpw_daily_pick as gpw

ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data/investments/rejected_candidate_outcomes/gpw"
INDEX_PATH = STORE_DIR / "index.json"
SCHEMA_VERSION = "gpw-rejected-candidate-outcomes-v1"
SOURCE_SCHEMA_VERSION = "gpw-rejected-candidate-source-snapshot-v1"
WARSAW = ZoneInfo("Europe/Warsaw")
HORIZONS = (1, 2)
ROUND_TRIP_COST_PERCENT = 0.38
SESSION_FINALIZE_TIME = clock_time(17, 15)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.replace(path)


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _daily_day(row: Any) -> Optional[date]:
    value = getattr(row, "day", None)
    if isinstance(value, date):
        return value
    if isinstance(row, Mapping):
        raw = row.get("day") or row.get("date")
        if isinstance(raw, date):
            return raw
        if raw:
            try:
                return date.fromisoformat(str(raw)[:10])
            except ValueError:
                return None
    return None


def _daily_number(row: Any, field: str) -> Optional[float]:
    if isinstance(row, Mapping):
        return _finite(row.get(field))
    return _finite(getattr(row, field, None))


def _intraday_time(row: Mapping[str, Any]) -> Optional[datetime]:
    value = row.get("timestamp")
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = _parse_time(value)
        return parsed
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(timezone.utc)


def _session_complete(session: date, now: datetime) -> bool:
    local = now.astimezone(WARSAW)
    return session < local.date() or (session == local.date() and local.time() >= SESSION_FINALIZE_TIME)


def _hard_blocked(candidate: Mapping[str, Any]) -> tuple[bool, list[str]]:
    path = candidate.get("decision_path") if isinstance(candidate.get("decision_path"), Mapping) else {}
    names: list[str] = []
    for gate in path.get("gates") or []:
        if not isinstance(gate, Mapping):
            continue
        if gate.get("hard") is True and gate.get("passed") is False:
            names.append(str(gate.get("name") or "unknown_hard_gate"))
    return bool(names), names


def _selected_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else None
    if not selection or not selection.get("symbol"):
        return {
            "action": "FLAT",
            "symbol": None,
            "reason": "producer_selected_no_trade",
        }
    required = ("entry_zone", "stop", "target")
    missing = [field for field in required if selection.get(field) is None]
    if missing:
        raise ValueError(f"selected comparison plan missing fields: {missing}")
    return {
        "action": "LONG",
        "symbol": selection.get("symbol"),
        "ticker": selection.get("ticker"),
        "name": selection.get("name"),
        "sector": selection.get("sector"),
        "entry_zone": deepcopy(selection.get("entry_zone")),
        "stop": selection.get("stop"),
        "target": selection.get("target"),
        "reward_risk": selection.get("reward_risk"),
        "reference_price": selection.get("reference_price"),
        "plan_source": "producer_selected_plan",
    }


def build_source_snapshot(payload: Mapping[str, Any], *, config: Mapping[str, Any]) -> dict[str, Any]:
    freeze = payload.get(rejected_freeze.FIELD)
    if not isinstance(freeze, Mapping):
        raise ValueError("prospective rejected-candidate freeze is required; historical reconstruction is forbidden")
    rejected_freeze._validate_freeze(freeze)
    if str(freeze.get("market") or "") != "gpw":
        raise ValueError("rejected-candidate freeze market mismatch")
    decision_at = str(payload.get("generated_at") or "")
    if str(freeze.get("decision_at") or "") != decision_at:
        raise ValueError("freeze and producer decision timestamps differ")
    decision_day = date.fromisoformat(str(payload.get("date")))
    valid_until = gpw.add_sessions(decision_day, 2, dict(config))
    source = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "market": "gpw",
        "decision_date": decision_day.isoformat(),
        "decision_at": decision_at,
        "producer_decision": payload.get("decision"),
        "producer_reason": payload.get("reason"),
        "selected_plan": _selected_plan(payload),
        "valid_until": valid_until.isoformat(),
        "horizon_sessions": list(HORIZONS),
        "rejected_candidate_freeze": deepcopy(freeze),
        "freeze_sha256": freeze.get("freeze_sha256"),
        "comparison_contract": {
            "activation_policy": "first_post_decision_5m_entry_zone_open_or_touch",
            "same_bar_policy": "stop_first_conservative",
            "expiry_policy": "horizon_session_close",
            "round_trip_cost_percent": ROUND_TRIP_COST_PERCENT,
            "opportunity_set": "economically_evaluable_and_no_failed_hard_gate",
            "hard_gate_rejects_observed_but_not_counted_as_legal_alternatives": True,
            "missing_market_data": "DATA_GAP_NOT_ZERO",
        },
        "governance": {
            "prospective_only": True,
            "historical_backfill": False,
            "decision_influence": False,
            "ranking_writeback": False,
            "gate_writeback": False,
            "production_trade_writeback": False,
            "automatic_learning_writeback": False,
            "automatic_promotion": False,
            "observational_only": True,
        },
    }
    return source


def build_record(payload: Mapping[str, Any], *, config: Mapping[str, Any], captured_at: Optional[datetime] = None) -> dict[str, Any]:
    source = build_source_snapshot(payload, config=config)
    record = {
        "schema_version": SCHEMA_VERSION,
        "market": "gpw",
        "decision_date": source["decision_date"],
        "captured_at": (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_snapshot": source,
        "source_snapshot_sha256": _sha(source),
        "settlement": {
            "status": "PENDING",
            "last_checked_at": None,
            "selected": None,
            "rejected_candidates": [],
            "summary": {
                "status": "PENDING",
                "reason": "horizon_not_complete",
            },
        },
    }
    verify_record(record)
    return record


def verify_record(record: Mapping[str, Any]) -> None:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("rejected-candidate outcomes schema mismatch")
    source = record.get("source_snapshot")
    if not isinstance(source, Mapping) or source.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError("source snapshot missing or invalid")
    if str(record.get("source_snapshot_sha256") or "") != _sha(source):
        raise ValueError("immutable source snapshot hash mismatch")
    freeze = source.get("rejected_candidate_freeze")
    if not isinstance(freeze, Mapping):
        raise ValueError("source snapshot has no rejected candidate freeze")
    rejected_freeze._validate_freeze(freeze)
    if str(source.get("freeze_sha256") or "") != str(freeze.get("freeze_sha256") or ""):
        raise ValueError("source freeze hash lineage mismatch")
    governance = source.get("governance") if isinstance(source.get("governance"), Mapping) else {}
    forbidden_true = ("decision_influence", "ranking_writeback", "gate_writeback", "production_trade_writeback", "automatic_learning_writeback", "automatic_promotion")
    if any(governance.get(field) is not False for field in forbidden_true):
        raise ValueError("zero-authority governance contract violated")
    if governance.get("historical_backfill") is not False or governance.get("prospective_only") is not True:
        raise ValueError("prospective-only contract violated")


def capture_current(*, payload_path: Path = gpw.PUBLIC_PATH, store_dir: Path = STORE_DIR, now: Optional[datetime] = None) -> tuple[Path, bool]:
    payload = gpw.load_json(payload_path)
    if not isinstance(payload, Mapping):
        raise ValueError("GPW public payload is missing")
    config = gpw.load_config()
    record = build_record(payload, config=config, captured_at=now)
    path = store_dir / f"{record['decision_date']}.json"
    if path.exists():
        existing = gpw.load_json(path)
        if not isinstance(existing, Mapping):
            raise ValueError(f"existing outcome record is invalid: {path}")
        verify_record(existing)
        if str(existing.get("source_snapshot_sha256")) != str(record.get("source_snapshot_sha256")):
            raise RuntimeError("refusing to replace an immutable rejected-candidate source snapshot")
        return path, False
    _atomic(path, record)
    return path, True


def _activation(plan: Mapping[str, Any], decision_at: datetime, valid_until: date, intraday: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    zone = plan.get("entry_zone") or []
    if not isinstance(zone, Sequence) or isinstance(zone, (str, bytes)) or len(zone) != 2:
        return None
    low, high = (_finite(value) for value in zone)
    if low is None or high is None:
        return None
    for bar in sorted(intraday, key=lambda row: _intraday_time(row) or datetime.max.replace(tzinfo=timezone.utc)):
        observed = _intraday_time(bar)
        if observed is None or observed < decision_at:
            continue
        local_day = observed.astimezone(WARSAW).date()
        if local_day > valid_until:
            break
        opening = _finite(bar.get("open"))
        low_bar = _finite(bar.get("low"))
        high_bar = _finite(bar.get("high"))
        if opening is None or low_bar is None or high_bar is None:
            continue
        if low <= opening <= high:
            return {"timestamp": observed, "price": opening, "evidence": "first_post_decision_5m_open_in_zone"}
        if opening > high and low_bar <= high:
            return {"timestamp": observed, "price": high, "evidence": "first_post_decision_5m_touch_from_above"}
        if opening < low and high_bar >= low:
            return {"timestamp": observed, "price": low, "evidence": "first_post_decision_5m_touch_from_below"}
    return None


def _daily_close(daily: Sequence[Any], day: date) -> Optional[float]:
    for row in daily:
        if _daily_day(row) == day:
            return _daily_number(row, "close")
    return None


def _horizon_result(
    *,
    plan: Mapping[str, Any],
    decision_at: datetime,
    decision_day: date,
    valid_until: date,
    horizon: int,
    intraday: Sequence[Mapping[str, Any]],
    daily: Sequence[Any],
    config: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    horizon_day = gpw.add_sessions(decision_day, horizon, dict(config))
    if not _session_complete(horizon_day, now):
        return {"status": "PENDING", "horizon_sessions": horizon, "session": horizon_day.isoformat()}
    activation = _activation(plan, decision_at, valid_until, intraday)
    if activation is None or activation["timestamp"].astimezone(WARSAW).date() > horizon_day:
        return {
            "status": "RESOLVED",
            "horizon_sessions": horizon,
            "session": horizon_day.isoformat(),
            "activated": False,
            "net_return_percent": 0.0,
            "gross_return_percent": 0.0,
            "exit_reason": "not_activated_by_horizon",
        }
    entry = float(activation["price"])
    stop = _finite(plan.get("stop"))
    target = _finite(plan.get("target"))
    if stop is None or target is None or entry <= 0:
        return {"status": "DATA_GAP", "horizon_sessions": horizon, "session": horizon_day.isoformat(), "reason": "invalid_frozen_risk_plan"}
    horizon_end = datetime.combine(horizon_day, clock_time(23, 59, 59), tzinfo=WARSAW).astimezone(timezone.utc)
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_at: Optional[datetime] = None
    for bar in sorted(intraday, key=lambda row: _intraday_time(row) or datetime.max.replace(tzinfo=timezone.utc)):
        observed = _intraday_time(bar)
        if observed is None or observed < activation["timestamp"] or observed > horizon_end:
            continue
        low_bar = _finite(bar.get("low"))
        high_bar = _finite(bar.get("high"))
        if low_bar is None or high_bar is None:
            continue
        stop_hit = low_bar <= stop
        target_hit = high_bar >= target
        if stop_hit or target_hit:
            if stop_hit:  # conservative if both occur inside one 5-minute bar
                exit_price, exit_reason = stop, "stop"
            else:
                exit_price, exit_reason = target, "target"
            exit_at = observed
            break
    if exit_price is None:
        close = _daily_close(daily, horizon_day)
        if close is None:
            return {"status": "DATA_GAP", "horizon_sessions": horizon, "session": horizon_day.isoformat(), "reason": "missing_horizon_close"}
        exit_price = close
        exit_reason = "horizon_close"
    gross = (exit_price / entry - 1.0) * 100.0
    risk = max(entry - stop, 0.01)
    return {
        "status": "RESOLVED",
        "horizon_sessions": horizon,
        "session": horizon_day.isoformat(),
        "activated": True,
        "activated_at": activation["timestamp"].astimezone(WARSAW).isoformat(timespec="seconds"),
        "activation_evidence": activation["evidence"],
        "entry_price": round(entry, 4),
        "exit_price": round(float(exit_price), 4),
        "exit_reason": exit_reason,
        "exit_at": exit_at.astimezone(WARSAW).isoformat(timespec="seconds") if exit_at else None,
        "gross_return_percent": round(gross, 4),
        "net_return_percent": round(gross - ROUND_TRIP_COST_PERCENT, 4),
        "r_multiple": round((float(exit_price) - entry) / risk, 4),
        "cost_assumption_percent": ROUND_TRIP_COST_PERCENT,
    }


def _settle_plan(
    *,
    plan: Mapping[str, Any],
    decision_at: datetime,
    decision_day: date,
    valid_until: date,
    config: Mapping[str, Any],
    now: datetime,
    intraday_fetcher: Callable[[str], Sequence[Mapping[str, Any]]],
    daily_fetcher: Callable[[str], Sequence[Any]],
) -> dict[str, Any]:
    if str(plan.get("action") or "LONG").upper() == "FLAT":
        horizons = []
        for horizon in HORIZONS:
            session = gpw.add_sessions(decision_day, horizon, dict(config))
            if _session_complete(session, now):
                horizons.append({"status": "RESOLVED", "horizon_sessions": horizon, "session": session.isoformat(), "activated": False, "net_return_percent": 0.0, "gross_return_percent": 0.0, "exit_reason": "flat_baseline"})
            else:
                horizons.append({"status": "PENDING", "horizon_sessions": horizon, "session": session.isoformat()})
        return {"symbol": None, "action": "FLAT", "horizons": horizons}
    symbol = str(plan.get("symbol") or "")
    if not symbol:
        return {"symbol": None, "action": "LONG", "status": "DATA_GAP", "reason": "missing_symbol", "horizons": []}
    try:
        intraday = list(intraday_fetcher(symbol))
        daily = list(daily_fetcher(symbol))
    except Exception as exc:
        return {
            "symbol": symbol,
            "action": "LONG",
            "status": "DATA_GAP",
            "reason": f"market_data_failure:{type(exc).__name__}:{str(exc)[:180]}",
            "horizons": [
                {"status": "DATA_GAP", "horizon_sessions": horizon, "session": gpw.add_sessions(decision_day, horizon, dict(config)).isoformat(), "reason": "market_data_failure"}
                if _session_complete(gpw.add_sessions(decision_day, horizon, dict(config)), now)
                else {"status": "PENDING", "horizon_sessions": horizon, "session": gpw.add_sessions(decision_day, horizon, dict(config)).isoformat()}
                for horizon in HORIZONS
            ],
        }
    horizons = [
        _horizon_result(
            plan=plan,
            decision_at=decision_at,
            decision_day=decision_day,
            valid_until=valid_until,
            horizon=horizon,
            intraday=intraday,
            daily=daily,
            config=config,
            now=now,
        )
        for horizon in HORIZONS
    ]
    return {"symbol": symbol, "action": "LONG", "horizons": horizons}


def _horizon(row: Mapping[str, Any], number: int) -> Optional[Mapping[str, Any]]:
    for horizon in row.get("horizons") or []:
        if isinstance(horizon, Mapping) and int(horizon.get("horizon_sessions") or 0) == number:
            return horizon
    return None


def _complete_return(row: Mapping[str, Any], number: int = 2) -> Optional[float]:
    horizon = _horizon(row, number)
    if not isinstance(horizon, Mapping) or horizon.get("status") != "RESOLVED":
        return None
    return _finite(horizon.get("net_return_percent"))


def _summary(selected: Mapping[str, Any], rejected: Sequence[Mapping[str, Any]], *, now: datetime, decision_day: date, config: Mapping[str, Any]) -> dict[str, Any]:
    t2 = gpw.add_sessions(decision_day, 2, dict(config))
    if not _session_complete(t2, now):
        return {"status": "PENDING", "reason": "t_plus_2_not_complete", "comparison_session": t2.isoformat()}
    selected_return = _complete_return(selected, 2)
    if selected_return is None:
        return {"status": "DATA_GAP", "reason": "selected_comparison_outcome_incomplete", "comparison_session": t2.isoformat()}
    legal = [row for row in rejected if row.get("opportunity_candidate") is True]
    if not legal:
        return {
            "status": "RESOLVED",
            "reason": "no_admissible_rejected_alternatives",
            "comparison_session": t2.isoformat(),
            "selected_return_percent": round(selected_return, 4),
            "opportunity_cost_percent": 0.0,
            "selection_advantage_percent": 0.0,
        }
    incomplete = [str(row.get("symbol")) for row in legal if _complete_return(row, 2) is None]
    if incomplete:
        return {
            "status": "DATA_GAP",
            "reason": "admissible_alternative_outcomes_incomplete",
            "comparison_session": t2.isoformat(),
            "missing_symbols": incomplete,
        }
    best = max(legal, key=lambda row: float(_complete_return(row, 2) or 0.0))
    best_return = float(_complete_return(best, 2) or 0.0)
    delta = best_return - selected_return
    return {
        "status": "RESOLVED",
        "reason": "complete_admissible_opportunity_set",
        "comparison_session": t2.isoformat(),
        "selected_symbol": selected.get("symbol"),
        "selected_return_percent": round(selected_return, 4),
        "best_rejected_symbol": best.get("symbol"),
        "best_rejected_return_percent": round(best_return, 4),
        "signed_best_rejected_minus_selected_percent": round(delta, 4),
        "opportunity_cost_percent": round(max(delta, 0.0), 4),
        "selection_advantage_percent": round(max(-delta, 0.0), 4),
        "admissible_rejected_count": len(legal),
    }


def settle_record(
    record: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    now: Optional[datetime] = None,
    intraday_fetcher: Callable[[str], Sequence[Mapping[str, Any]]] = selected_monitor.fetch_intraday,
    daily_fetcher: Callable[[str], Sequence[Any]] = lambda symbol: gpw.fetch_yahoo_bars(symbol, range_value="3mo"),
) -> dict[str, Any]:
    verify_record(record)
    current = deepcopy(dict(record))
    source = current["source_snapshot"]
    decision_at = _parse_time(source.get("decision_at"))
    if decision_at is None:
        raise ValueError("source decision_at is invalid")
    decision_day = date.fromisoformat(str(source["decision_date"]))
    valid_until = date.fromisoformat(str(source["valid_until"]))
    checked_at = now or datetime.now(timezone.utc)

    selected_plan = source.get("selected_plan") if isinstance(source.get("selected_plan"), Mapping) else {"action": "FLAT"}
    selected = _settle_plan(
        plan=selected_plan,
        decision_at=decision_at,
        decision_day=decision_day,
        valid_until=valid_until,
        config=config,
        now=checked_at,
        intraday_fetcher=intraday_fetcher,
        daily_fetcher=daily_fetcher,
    )
    selected["comparison_role"] = "selected_baseline"

    rejected_rows: list[dict[str, Any]] = []
    freeze = source["rejected_candidate_freeze"]
    for candidate in freeze.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        eligibility = candidate.get("settlement_eligibility") if isinstance(candidate.get("settlement_eligibility"), Mapping) else {}
        blocked, hard_gate_names = _hard_blocked(candidate)
        base = {
            "candidate_id": candidate.get("candidate_id"),
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name"),
            "sector": candidate.get("sector"),
            "rank": ((candidate.get("score_state") or {}).get("rank") if isinstance(candidate.get("score_state"), Mapping) else None),
            "first_blocking_gate": deepcopy(candidate.get("first_blocking_gate")),
            "hard_blocked": blocked,
            "hard_blocking_gates": hard_gate_names,
            "economically_evaluable": eligibility.get("eligible") is True,
            "opportunity_candidate": eligibility.get("eligible") is True and not blocked,
        }
        risk_plan = candidate.get("risk_plan") if isinstance(candidate.get("risk_plan"), Mapping) else None
        if eligibility.get("eligible") is not True or risk_plan is None:
            base.update({"action": "LONG", "status": "NOT_EVALUABLE", "reason": eligibility.get("reason") or "missing_frozen_risk_plan", "horizons": []})
            rejected_rows.append(base)
            continue
        plan = {**dict(risk_plan), "symbol": candidate.get("symbol"), "action": "LONG"}
        observed = _settle_plan(
            plan=plan,
            decision_at=decision_at,
            decision_day=decision_day,
            valid_until=valid_until,
            config=config,
            now=checked_at,
            intraday_fetcher=intraday_fetcher,
            daily_fetcher=daily_fetcher,
        )
        base.update(observed)
        rejected_rows.append(base)

    summary = _summary(selected, rejected_rows, now=checked_at, decision_day=decision_day, config=config)
    t2_status = summary.get("status")
    current["settlement"] = {
        "status": "RESOLVED" if t2_status == "RESOLVED" else ("DATA_GAP" if t2_status == "DATA_GAP" else "PENDING"),
        "last_checked_at": checked_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "selected": selected,
        "rejected_candidates": rejected_rows,
        "summary": summary,
        "contract": {
            "source_snapshot_immutable": True,
            "historical_backfill_performed": False,
            "missing_review_is_zero_return": False,
            "decision_influence": False,
            "automatic_learning_writeback": False,
        },
    }
    verify_record(current)
    return current


def build_index(records: Sequence[Mapping[str, Any]], *, now: Optional[datetime] = None) -> dict[str, Any]:
    rows = []
    for record in sorted(records, key=lambda item: str(item.get("decision_date") or ""), reverse=True):
        settlement = record.get("settlement") if isinstance(record.get("settlement"), Mapping) else {}
        summary = settlement.get("summary") if isinstance(settlement.get("summary"), Mapping) else {}
        rejected = settlement.get("rejected_candidates") or []
        rows.append({
            "decision_date": record.get("decision_date"),
            "status": settlement.get("status"),
            "source_snapshot_sha256": record.get("source_snapshot_sha256"),
            "selected_symbol": ((settlement.get("selected") or {}).get("symbol") if isinstance(settlement.get("selected"), Mapping) else None),
            "rejected_count": len(rejected),
            "opportunity_candidate_count": sum(1 for row in rejected if isinstance(row, Mapping) and row.get("opportunity_candidate") is True),
            "summary": deepcopy(summary),
        })
    return {
        "schema_version": "gpw-rejected-candidate-outcomes-index-v1",
        "updated_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "record_count": len(rows),
        "resolved_count": sum(1 for row in rows if row.get("status") == "RESOLVED"),
        "data_gap_count": sum(1 for row in rows if row.get("status") == "DATA_GAP"),
        "records": rows,
        "governance": {
            "observational_only": True,
            "decision_influence": False,
            "automatic_learning_writeback": False,
            "historical_backfill": False,
        },
    }


def settle_store(*, store_dir: Path = STORE_DIR, now: Optional[datetime] = None) -> dict[str, Any]:
    config = gpw.load_config()
    checked_at = now or datetime.now(timezone.utc)
    changed = 0
    records: list[dict[str, Any]] = []
    for path in sorted(store_dir.glob("????-??-??.json")):
        payload = gpw.load_json(path)
        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid rejected-candidate outcome record: {path}")
        before = _canonical(payload)
        settled = settle_record(payload, config=config, now=checked_at)
        if _canonical(settled) != before:
            _atomic(path, settled)
            changed += 1
        records.append(settled)
    index = build_index(records, now=checked_at)
    _atomic(store_dir / "index.json", index)
    return {"status": "OK", "changed_records": changed, "record_count": len(records), "index": index}


def verify_store(*, store_dir: Path = STORE_DIR) -> dict[str, Any]:
    records = []
    for path in sorted(store_dir.glob("????-??-??.json")):
        payload = gpw.load_json(path)
        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid rejected-candidate outcome record: {path}")
        verify_record(payload)
        records.append(payload)
    return {"ok": True, "records": len(records), "historical_backfill": False, "decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true", help="Persist today's already-frozen rejected candidate source state.")
    mode.add_argument("--settle", action="store_true", help="Update T+1/T+2 observations for captured records.")
    mode.add_argument("--verify", action="store_true", help="Verify immutable source lineage for every captured record.")
    args = parser.parse_args()
    if args.capture:
        path, changed = capture_current()
        print(json.dumps({"status": "OK", "mode": "capture", "path": str(path.relative_to(ROOT)), "changed": changed}, ensure_ascii=False))
        return 0
    if args.settle:
        print(json.dumps(settle_store(), ensure_ascii=False))
        return 0
    print(json.dumps(verify_store(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
