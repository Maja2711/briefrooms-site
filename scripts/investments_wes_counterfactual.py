#!/usr/bin/env python3
"""Prospective WES vs V5 frozen-baseline counterfactual evaluator.

This module is evidence-only. It enriches a V5 baseline that was already
captured prospectively by the WES-SPX/BRACE-SPX bridge, then replays that
frozen risk plan on later 5-minute OHLC data using the same conservative
stop-first rule used by the weekly paper engine.

It never changes WES/V5 decisions, candidate scores, TP/SL, exposure, learning
parameters, BRACE-SPX research, or broker controls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

import investments_weekly_v2 as v2
import investments_wes_spx_brace_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
POLICY_PATH = ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json"
LEDGER_PATH = ROOT / "data" / "investments" / "wes_spx_brace_bridge.json"
ALPHA_REPORT_PATH = ROOT / "data" / "investments" / "wes_spx_brace_alpha_report.json"
INCREMENTAL_REPORT_PATH = ROOT / "data" / "investments" / "wes_incremental_alpha_report.json"

CONTRACT_VERSION = "wes-v5-counterfactual-contract-v1"
REPORT_VERSION = "wes-v5-incremental-alpha-v1"
SPX_ID = "sp500_futures"
TZ = ZoneInfo("Europe/Warsaw")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=TZ)
    return out.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _week_path(week_id: str) -> Path:
    return WEEKLY_DIR / f"{week_id}.json"


def _find_spx(week: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    for row in week.get("instruments", []) or []:
        if isinstance(row, Mapping) and str(row.get("instrument_id")) == SPX_ID:
            return row
    return None


def _policy_spx(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    for row in policy.get("instruments", []) or []:
        if isinstance(row, Mapping) and str(row.get("instrument_id")) == SPX_ID:
            return row
    return {}


def _cost_percent(entry_price: float, policy_row: Mapping[str, Any]) -> float:
    cost = float(_finite(policy_row.get("round_trip_cost")) or 0.0)
    unit = str(policy_row.get("cost_unit") or "")
    if unit == "points":
        return cost / entry_price * 100.0 if entry_price else 0.0
    if unit == "percent":
        return cost
    return 0.0


def _risk_plan_from_pre_wes_state(
    baseline: Mapping[str, Any],
    item: Mapping[str, Any],
) -> tuple[Optional[dict[str, Any]], str]:
    existing = baseline.get("risk_plan")
    if isinstance(existing, Mapping):
        sl = _finite(existing.get("stop_loss_price"))
        tp = _finite(existing.get("take_profit_price"))
        if sl is not None and tp is not None:
            return deepcopy(dict(existing)), "frozen_item_risk_plan_pre_wes"

    entry = _finite(baseline.get("entry_price"))
    direction = str(baseline.get("direction") or "neutral")
    distance = item.get("risk_distance") if isinstance(item.get("risk_distance"), Mapping) else {}
    stop_distance = _finite(distance.get("stop_price_distance"))
    take_distance = _finite(distance.get("take_price_distance"))
    if (
        entry is None
        or entry <= 0
        or direction not in {"long", "short"}
        or stop_distance is None
        or stop_distance <= 0
        or take_distance is None
        or take_distance <= 0
    ):
        return None, "missing_pre_wes_risk_plan_and_frozen_risk_distance"

    if direction == "long":
        sl, tp = entry - stop_distance, entry + take_distance
    else:
        sl, tp = entry + stop_distance, entry - take_distance
    return {
        "model_version": "V5-baseline-from-frozen-risk-distance",
        "created_from_frozen_forecast": True,
        "direction": direction,
        "stop_loss_price": round(sl, 8),
        "take_profit_price": round(tp, 8),
        "stop_loss_distance": round(stop_distance, 8),
        "take_profit_distance": round(take_distance, 8),
        "reward_to_risk": round(take_distance / stop_distance, 4),
        "same_bar_rule": "stop_loss_first_conservative",
    }, "derived_point_in_time_from_frozen_risk_distance"


def make_replay_contract(
    record: Mapping[str, Any],
    week: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    frozen_at: Optional[datetime] = None,
) -> dict[str, Any]:
    baseline = record.get("v5_counterfactual")
    capture = record.get("v5_counterfactual_capture") or {}
    if not isinstance(baseline, Mapping) or capture.get("eligible") is not True:
        return {
            "status": "not_eligible",
            "reason": "prospective_v5_baseline_not_frozen",
            "contract": None,
        }

    item = _find_spx(week)
    if item is None:
        return {"status": "not_replayable", "reason": "spx_week_item_missing", "contract": None}

    entry = _finite(baseline.get("entry_price"))
    entry_at = baseline.get("entry_captured_at")
    direction = str(baseline.get("direction") or "neutral")
    scheduled_exit = (week.get("market_window") or {}).get("exit_target_local")
    symbol = str(item.get("symbol") or "")
    if entry is None or entry <= 0 or not entry_at or direction not in {"long", "short"}:
        return {"status": "not_replayable", "reason": "baseline_entry_incomplete", "contract": None}
    if not scheduled_exit or not symbol:
        return {"status": "not_replayable", "reason": "scheduled_exit_or_symbol_missing", "contract": None}

    risk_plan, risk_source = _risk_plan_from_pre_wes_state(baseline, item)
    if risk_plan is None:
        return {"status": "not_replayable", "reason": risk_source, "contract": None}

    sl = _finite(risk_plan.get("stop_loss_price"))
    tp = _finite(risk_plan.get("take_profit_price"))
    if sl is None or tp is None:
        return {"status": "not_replayable", "reason": "risk_levels_missing", "contract": None}

    cost = _cost_percent(entry, _policy_spx(policy))
    payload = {
        "schema_version": CONTRACT_VERSION,
        "baseline_scope": "frozen_pre_wes_v5_risk_plan",
        "decision_id": record.get("decision_id"),
        "week_id": record.get("week_id"),
        "instrument_id": SPX_ID,
        "symbol": symbol,
        "direction": direction,
        "entry_price": round(entry, 10),
        "entry_captured_at": entry_at,
        "scheduled_exit": scheduled_exit,
        "risk_plan": risk_plan,
        "risk_plan_source": risk_source,
        "same_bar_rule": str(risk_plan.get("same_bar_rule") or "stop_loss_first_conservative"),
        "round_trip_cost_percent": round(cost, 8),
        "market_data_rule": "5m_OHLC_after_entry_no_current_quote_fallback",
        "scheduled_close_rule": "first_5m_bar_at_or_after_frozen_deadline",
        "frozen_at": (frozen_at or _now()).isoformat(timespec="seconds"),
        "decision_influence": False,
    }
    payload["contract_sha256"] = canonical_sha256({k: v for k, v in payload.items() if k != "contract_sha256"})
    return {"status": "frozen", "reason": "self_contained_point_in_time_replay_contract", "contract": payload}


def freeze_contracts(
    ledger: Mapping[str, Any],
    *,
    weeks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    policy: Optional[Mapping[str, Any]] = None,
    frozen_at: Optional[datetime] = None,
) -> dict[str, Any]:
    out = deepcopy(dict(ledger))
    rows = [deepcopy(x) for x in (out.get("records") or []) if isinstance(x, Mapping)]
    policy = policy if policy is not None else _read(POLICY_PATH, {})
    for row in rows:
        baseline = row.get("v5_counterfactual")
        if not isinstance(baseline, dict):
            continue
        if baseline.get("replay_contract") or baseline.get("replay_contract_status") == "not_replayable":
            continue
        week_id = str(row.get("week_id") or "")
        week = (weeks or {}).get(week_id) if weeks is not None else _read(_week_path(week_id), {})
        result = make_replay_contract(row, week or {}, policy, frozen_at=frozen_at)
        baseline["replay_contract_status"] = result["status"]
        baseline["replay_contract_reason"] = result["reason"]
        if result["contract"] is not None:
            baseline["replay_contract"] = result["contract"]
            baseline["outcome_status"] = "frozen_replay_contract_pending_market_outcome"
    out["records"] = rows
    out["active_decision_influence"] = False
    out["updated_at"] = (frozen_at or _now()).isoformat(timespec="seconds")
    return out


def _row_value(row: Any, name: str) -> Optional[float]:
    try:
        value = row[name]
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return _finite(value)
    except Exception:
        return None


def _bar_timestamp(ts: Any) -> Optional[datetime]:
    try:
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def replay_from_bars(
    contract: Mapping[str, Any],
    bars: Any,
    *,
    evaluated_at: Optional[datetime] = None,
) -> dict[str, Any]:
    now = (evaluated_at or _now()).astimezone(timezone.utc)
    entry_at = _dt(contract.get("entry_captured_at"))
    scheduled = _dt(contract.get("scheduled_exit"))
    entry = _finite(contract.get("entry_price"))
    direction = str(contract.get("direction") or "neutral")
    plan = contract.get("risk_plan") if isinstance(contract.get("risk_plan"), Mapping) else {}
    sl, tp = _finite(plan.get("stop_loss_price")), _finite(plan.get("take_profit_price"))
    cost = float(_finite(contract.get("round_trip_cost_percent")) or 0.0)

    if None in {entry_at, scheduled, entry, sl, tp} or direction not in {"long", "short"}:
        return {"status": "not_replayable", "reason": "contract_incomplete"}
    if bars is None or getattr(bars, "empty", True):
        return {"status": "pending_market_data", "reason": "five_minute_bars_unavailable"}

    threshold_rows = []
    scheduled_close = None
    try:
        for ts, row in bars.iterrows():
            ts_dt = _bar_timestamp(ts)
            if ts_dt is None or ts_dt <= entry_at:
                continue
            if ts_dt <= scheduled and ts_dt <= now:
                threshold_rows.append((ts_dt, row))
            if scheduled_close is None and ts_dt >= scheduled and ts_dt <= now:
                scheduled_close = (ts_dt, row)
    except Exception:
        return {"status": "pending_market_data", "reason": "bars_not_iterable"}

    hit = None
    for ts_dt, row in threshold_rows:
        high, low = _row_value(row, "High"), _row_value(row, "Low")
        if high is None or low is None:
            continue
        if direction == "long":
            sl_hit, tp_hit = low <= sl, high >= tp
        else:
            sl_hit, tp_hit = high >= sl, low <= tp
        if sl_hit and tp_hit:
            hit = ("stop_loss", sl, ts_dt, "same_bar_stop_first_conservative")
            break
        if sl_hit:
            hit = ("stop_loss", sl, ts_dt, "frozen_level_first_hit")
            break
        if tp_hit:
            hit = ("take_profit", tp, ts_dt, "frozen_level_first_hit")
            break

    if hit is None:
        if now < scheduled:
            return {"status": "pending", "reason": "no_frozen_level_hit_and_deadline_not_reached"}
        if scheduled_close is None:
            return {"status": "pending_market_data", "reason": "scheduled_close_bar_unavailable"}
        ts_dt, row = scheduled_close
        exit_price = _row_value(row, "Open") or _row_value(row, "Close")
        if exit_price is None:
            return {"status": "pending_market_data", "reason": "scheduled_close_price_unavailable"}
        reason, execution_rule = "scheduled_week_close", "first_5m_bar_at_or_after_frozen_deadline"
    else:
        reason, exit_price, ts_dt, execution_rule = hit

    gross = ((exit_price - entry) / entry * 100.0) if direction == "long" else ((entry - exit_price) / entry * 100.0)
    net = gross - cost
    return {
        "status": "resolved",
        "reason": reason,
        "exit_reason": reason,
        "exit_price": round(float(exit_price), 10),
        "exit_captured_at": ts_dt.isoformat(timespec="seconds"),
        "execution_rule": execution_rule,
        "gross_result_percent": round(gross, 8),
        "round_trip_cost_percent": round(cost, 8),
        "net_result_percent": round(net, 8),
        "contract_sha256": contract.get("contract_sha256"),
        "evaluated_at": now.isoformat(timespec="seconds"),
        "decision_influence": False,
    }


def _download_contract_bars(contract: Mapping[str, Any], now: datetime) -> Any:
    entry_at = _dt(contract.get("entry_captured_at"))
    scheduled = _dt(contract.get("scheduled_exit"))
    symbol = str(contract.get("symbol") or "")
    if entry_at is None or scheduled is None or not symbol:
        return None
    end = min(now, scheduled + timedelta(hours=3))
    if end <= entry_at:
        return None
    return v2.intraday_bars(symbol, entry_at.astimezone(TZ), end.astimezone(TZ))


def apply_evaluations(
    ledger: Mapping[str, Any],
    *,
    evaluated_at: Optional[datetime] = None,
    bars_by_decision: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    out = deepcopy(dict(ledger))
    rows = [deepcopy(x) for x in (out.get("records") or []) if isinstance(x, Mapping)]
    now = (evaluated_at or _now()).astimezone(timezone.utc)
    for row in rows:
        baseline = row.get("v5_counterfactual")
        if not isinstance(baseline, dict):
            continue
        if _finite(baseline.get("net_result_percent")) is not None:
            result = baseline.get("replay_evaluation") or {}
        else:
            contract = baseline.get("replay_contract")
            if not isinstance(contract, Mapping):
                continue
            decision_id = str(row.get("decision_id") or "")
            bars = bars_by_decision.get(decision_id) if bars_by_decision is not None else _download_contract_bars(contract, now)
            result = replay_from_bars(contract, bars, evaluated_at=now)
            baseline["replay_evaluation"] = result
            if result.get("status") == "resolved":
                baseline["outcome_status"] = "resolved_counterfactual_replay"
                baseline["net_result_percent"] = result["net_result_percent"]

        wes_net = _finite((row.get("outcome") or {}).get("wes_net_result_percent"))
        v5_net = _finite(baseline.get("net_result_percent"))
        outcome = row.setdefault("outcome", {})
        outcome["v5_counterfactual_net_result_percent"] = round(v5_net, 8) if v5_net is not None else None
        if wes_net is not None and v5_net is not None:
            incremental = wes_net - v5_net
            outcome["incremental_wes_vs_v5_percent"] = round(incremental, 8)
            outcome["status"] = "resolved_incremental_alpha"
        elif wes_net is not None:
            outcome["incremental_wes_vs_v5_percent"] = None
            outcome["status"] = "wes_observed_v5_counterfactual_pending"

    out["records"] = rows
    out["active_decision_influence"] = False
    out["updated_at"] = now.isoformat(timespec="seconds")
    out["content_sha256"] = bridge.canonical_sha256({
        "schema_version": out.get("schema_version"),
        "governance": out.get("governance"),
        "records": rows,
    })
    return out


def _stats(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        float(v)
        for v in (_finite((row.get("outcome") or {}).get("incremental_wes_vs_v5_percent")) for row in rows)
        if v is not None
    ]
    return {
        "resolved_pairs": len(values),
        "mean_incremental_alpha_percent": round(fmean(values), 8) if values else None,
        "median_incremental_alpha_percent": round(median(values), 8) if values else None,
        "wes_better_than_v5_rate": round(sum(v > 0 for v in values) / len(values), 6) if values else None,
        "best_incremental_alpha_percent": round(max(values), 8) if values else None,
        "worst_incremental_alpha_percent": round(min(values), 8) if values else None,
    }


def build_incremental_report(ledger: Mapping[str, Any]) -> dict[str, Any]:
    records = [x for x in (ledger.get("records") or []) if isinstance(x, Mapping)]
    resolved = [
        row for row in records
        if _finite((row.get("outcome") or {}).get("incremental_wes_vs_v5_percent")) is not None
    ]
    by_strategy: dict[str, list[Mapping[str, Any]]] = {}
    by_entry_class: dict[str, list[Mapping[str, Any]]] = {}
    by_relationship: dict[str, list[Mapping[str, Any]]] = {}
    for row in resolved:
        actual = row.get("wes_actual") if isinstance(row.get("wes_actual"), Mapping) else {}
        by_strategy.setdefault(str(actual.get("strategy_id") or "unknown"), []).append(row)
        by_entry_class.setdefault(str(actual.get("entry_class") or "unknown"), []).append(row)
        relation = row.get("relationship") if isinstance(row.get("relationship"), Mapping) else {}
        if relation.get("alpha_eligible") is True:
            by_relationship.setdefault(str(relation.get("class") or "UNKNOWN"), []).append(row)

    n = len(resolved)
    readiness = (
        "collecting_prospective_pairs"
        if n == 0
        else "warmup_insufficient_evidence"
        if n < 12
        else "analysis_available_not_policy_authorized"
    )
    return {
        "schema_version": REPORT_VERSION,
        "active_decision_influence": False,
        "bounded_influence_enabled": False,
        "baseline_definition": "prospectively_frozen_pre_wes_v5_risk_plan",
        "historical_backfill_allowed": False,
        "overall": _stats(resolved),
        "by_strategy": {key: _stats(rows) for key, rows in sorted(by_strategy.items())},
        "by_entry_class": {key: _stats(rows) for key, rows in sorted(by_entry_class.items())},
        "agreement_conflict_incremental_alpha": {
            key: _stats(rows) for key, rows in sorted(by_relationship.items())
        },
        "sample": {
            "economic_decisions": n,
            "effective_samples": float(n),
            "minimum_before_descriptive_analysis": 12,
            "status": readiness,
        },
        "interpretation": {
            "incremental_alpha": "WES net result minus replayed frozen V5 baseline net result for the same economic decision.",
            "agreement_conflict": "Only point-in-time alpha-eligible BRACE-SPX relationships are grouped here.",
            "policy": "Descriptive evidence only. No score, threshold, TP/SL or exposure adjustment is authorized.",
        },
    }


def extend_bridge_alpha_report(ledger: Mapping[str, Any], incremental: Mapping[str, Any]) -> dict[str, Any]:
    report = bridge.build_alpha_report(ledger)
    report["incremental_alpha"] = deepcopy(incremental)
    report["active_decision_influence"] = False
    report["bounded_influence_enabled"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["freeze", "evaluate", "all"], default="all")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--alpha-report", type=Path, default=ALPHA_REPORT_PATH)
    parser.add_argument("--incremental-report", type=Path, default=INCREMENTAL_REPORT_PATH)
    args = parser.parse_args()

    ledger = _read(args.ledger, bridge._new_ledger())
    if args.stage in {"freeze", "all"}:
        ledger = freeze_contracts(ledger)
    if args.stage in {"evaluate", "all"}:
        ledger = apply_evaluations(ledger)

    incremental = build_incremental_report(ledger)
    alpha = extend_bridge_alpha_report(ledger, incremental)
    _write(args.ledger, ledger)
    _write(args.incremental_report, incremental)
    _write(args.alpha_report, alpha)
    print(json.dumps({
        "stage": args.stage,
        "resolved_pairs": incremental["overall"]["resolved_pairs"],
        "sample_status": incremental["sample"]["status"],
        "active_decision_influence": False,
        "bounded_influence_enabled": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
