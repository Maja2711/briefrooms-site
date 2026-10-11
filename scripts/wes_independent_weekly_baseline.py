#!/usr/bin/env python3
"""Independent, prospective weekly forecast benchmark for WES SPX (research only).

This is the *frozen weekly forecast under V5 governance*, whose underlying
forecast may be v2.0.0. It is NOT an independent execution of governed V5.
One immutable intention per week is captured BEFORE WES preflight/execution.
No original fill, no synthetic broker order, no historical backdating.
Only later observed completed 5-minute OHLC bars can produce a virtual fill.
"""
from __future__ import annotations

import argparse
import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import investments_weekly_v2 as v2
import investments_wes_counterfactual as cf

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/investments/wes_independent_weekly_baseline.json"
REPORT = ROOT / "data/investments/wes_independent_weekly_baseline_report.json"
WEEKS = ROOT / "data/investments/weekly"
SPX = "sp500_futures"
TZ = ZoneInfo("Europe/Warsaw")
SCHEMA = "wes-independent-weekly-forecast-baseline-v1"


def dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        val = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if val.tzinfo is None:
            return None
        return val.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def finite(value: Any) -> float | None:
    try:
        num = float(value)
        return num if math.isfinite(num) else None
    except (ValueError, TypeError):
        return None


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA, "execution_authority": False,
            "automatic_promotion": False, "historical_backfill_allowed": False, "weeks": {}}


def capture(state: Mapping[str, Any], week: Mapping[str, Any], now: datetime,
            policy: Mapping[str, Any]) -> dict[str, Any]:
    """Capture exactly once. Missed timestamps are terminal, never reconstructed."""
    out = deepcopy(dict(state))
    out.setdefault("weeks", {})
    wid = str(week.get("week_id") or "")
    if not wid or wid in out["weeks"]:
        return out
    frozen = week.get("frozen_forecast") or {}
    lock = dt(frozen.get("forecast_locked_at"))
    window = frozen.get("market_window") or {}
    target, latest, expiry = (dt(window.get(key)) for key in
        ("entry_target_local", "entry_latest_local", "exit_target_local"))
    row = next((x for x in (frozen.get("instruments") or [])
                if isinstance(x, Mapping) and x.get("instrument_id") == SPX), None)
    result = {"week_id": wid, "capture_at": now.isoformat(), "source": "weekly.frozen_forecast",
              "authority": "shadow", "decision_influence": False, "status": "not_eligible",
              "reason": "missing_or_untrusted_frozen_forecast", "outcome": None}
    if (not isinstance(row, Mapping) or frozen.get("week_id") != wid or
            not all((lock, target, latest, expiry)) or
            not (lock <= now <= target < latest < expiry) or
            row.get("data_quality") != "passed"):
        result["reason"] = "missing_forecast_or_capture_after_original_entry_target"
    else:
        side = str(row.get("direction") or "neutral")
        score = finite(row.get("score"))
        distance = row.get("risk_distance") or {}
        stop, take = finite(distance.get("stop_price_distance")), finite(distance.get("take_price_distance"))
        cfg = next((x for x in (policy.get("instruments") or []) if isinstance(x, Mapping)
                    and x.get("instrument_id") == SPX), {})
        cost, unit = finite(cfg.get("round_trip_cost")), cfg.get("cost_unit")
        symbol = str(row.get("symbol") or "")
        if (side not in {"long", "short", "neutral"} or score is None or not symbol or
                cost is None or cost < 0 or unit not in {"points", "percent"} or
                (side != "neutral" and (stop is None or take is None or stop <= 0 or take <= 0))):
            result["reason"] = "invalid_frozen_forecast_or_cost_policy"
        else:
            plan = {"direction": side, "score": score, "symbol": symbol,
                    "forecast_version": frozen.get("base_method_version"),
                    "forecast_locked_at": frozen["forecast_locked_at"],
                    "forecast_fingerprint": cf.canonical_sha256(frozen),
                    "entry_target": window["entry_target_local"],
                    "entry_latest": window["entry_latest_local"],
                    "scheduled_exit": window["exit_target_local"],
                    "risk_distance": deepcopy(distance),
                    "round_trip_cost": cost, "cost_unit": unit,
                    "virtual_fill_rule": "first_verified_completed_5m_bar_open_at_or_after_frozen_entry_target",
                    "same_bar_rule": "stop_loss_first_conservative"}
            result.update(status="captured", reason="prospective_frozen_weekly_forecast",
                          plan=plan, plan_sha256=cf.canonical_sha256(plan))
    out["weeks"][wid] = result
    return out


def replay_weekly(plan: Mapping[str, Any], bars: Any, now: datetime) -> dict[str, Any]:
    """Virtual OHLC replay; never use an existing WES fill as baseline entry."""
    start, latest, end = (dt(plan.get(k)) for k in ("entry_target", "entry_latest", "scheduled_exit"))
    if not all((start, latest, end)):
        return {"status": "blocked", "reason": "invalid_frozen_time_window"}
    if plan.get("direction") == "neutral":
        if now >= end:
            return {"status": "resolved", "net_result_percent": 0.0,
                    "exit_reason": "frozen_no_trade", "virtual_fills": 0}
        return {"status": "pending", "reason": "neutral_week_not_yet_closed"}
    if bars is None or getattr(bars, "empty", True):
        return {"status": "pending_market_data", "reason": "no_5m_ohlc"}
    # A timestamp marks the START of its 5-minute bar. Do not use unfinished bars.
    eligible = []
    for ts, row in bars.iterrows():
        observed_at = cf._bar_timestamp(ts)
        if observed_at is not None and observed_at + timedelta(minutes=5) <= now:
            eligible.append((observed_at, row))
    eligible.sort(key=lambda x: x[0])
    if not eligible:
        return {"status": "pending_market_data", "reason": "no_completed_5m_candle"}
    first = next(((t, r) for t, r in eligible if start <= t <= latest), None)
    if first is None:
        return {"status": "pending_market_data", "reason": "missing_5m_entry_window"}
    fill_at, first_row = first
    # Reject a data hole near target; otherwise a late quote could impersonate the first bar.
    if fill_at > start + timedelta(minutes=15):
        return {"status": "pending_market_data", "reason": "first_entry_bar_not_verified"}
    price = cf._row_value(first_row, "Open")
    if price is None or price <= 0:
        return {"status": "pending_market_data", "reason": "entry_open_invalid"}
    distance = plan.get("risk_distance") or {}
    sd, td = finite(distance.get("stop_price_distance")), finite(distance.get("take_price_distance"))
    if sd is None or td is None or sd <= 0 or td <= 0:
        return {"status": "blocked", "reason": "missing_frozen_risk_distance"}
    side = plan["direction"]
    sl = price - sd if side == "long" else price + sd
    tp = price + td if side == "long" else price - td
    cost = cf._cost_percent(price, {"round_trip_cost": plan["round_trip_cost"], "cost_unit": plan["cost_unit"]})
    contract = {"entry_captured_at": fill_at.isoformat(), "entry_price": price,
                "scheduled_exit": end.isoformat(), "direction": side,
                "risk_plan": {"stop_loss_price": sl, "take_profit_price": tp},
                "round_trip_cost_percent": cost,
                "contract_sha256": cf.canonical_sha256({"plan": plan, "fill_at": fill_at.isoformat(), "price": price})}
    # Persist the independently verified simulated entry even while settlement is pending.
    evidence = {"virtual_fill": {"entry_price": price, "entry_at": fill_at.isoformat(),
                                  "rule": plan["virtual_fill_rule"]},
                "contract": contract}
    import pandas as pd
    later = pd.DataFrame([r for _, r in eligible if _ > fill_at],
                         index=[t for t, _ in eligible if t > fill_at])
    settled = cf.replay_from_bars(contract, later, evaluated_at=now)
    return {**evidence, **settled}


def wes_week_result(week: Mapping[str, Any], now: datetime, deadline: datetime | None) -> dict[str, Any]:
    """One normalized WES week, no double-counting repeated monitoring snapshots."""
    item = next((x for x in (week.get("instruments") or [])
                 if isinstance(x, Mapping) and x.get("instrument_id") == SPX), {})
    if not item:
        return {"status": "pending", "reason": "missing_spx_week_state"}
    legs = [x for x in (item.get("position_legs") or []) if isinstance(x, Mapping)]
    if item.get("entry_captured_at") and not item.get("exit_captured_at") and finite(item.get("entry_price")) is not None:
        return {"status": "pending", "reason": "wes_position_open"}
    if len(legs) > 1:
        return {"status": "not_comparable", "reason": "multiple_wes_legs_require_exposure_normalization", "leg_count": len(legs)}
    if legs:
        val = finite(legs[0].get("net_result_percent"))
        if val is None or not legs[0].get("exit_captured_at"):
            return {"status": "pending", "reason": "wes_leg_unsettled"}
        return {"status": "resolved", "net_result_percent": val, "source": "canonical_closed_leg", "leg_count": 1}
    if item.get("exit_captured_at") and finite(item.get("entry_price")) is not None:
        val = finite(item.get("net_result_percent"))
        if val is None:
            val = finite(item.get("result_percent"))
        if val is not None:
            return {"status": "resolved", "net_result_percent": val, "source": "canonical_closed_position", "leg_count": 1}
    if deadline is not None and now >= deadline and finite(item.get("entry_price")) is None and item.get("trade_status") == "no_trade":
        return {"status": "resolved", "net_result_percent": 0.0, "source": "verified_no_trade_week", "leg_count": 0}
    return {"status": "pending", "reason": "wes_week_not_settled"}


def evaluate(state: Mapping[str, Any], weeks: Mapping[str, Any], now: datetime,
             bars_by_week: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out = deepcopy(dict(state))
    for wid, row in (out.get("weeks") or {}).items():
        if row.get("status") != "captured" or not isinstance(row.get("plan"), Mapping):
            continue
        plan = row["plan"]
        previous = row.get("outcome") or {}
        if previous.get("status") == "resolved" and previous.get("wes_comparison", {}).get("status") == "resolved":
            continue
        if plan.get("direction") == "neutral":
            result = replay_weekly(plan, None, now)
        else:
            bars = (bars_by_week or {}).get(wid) if bars_by_week is not None else None
            if bars_by_week is None:
                a, b = dt(plan.get("entry_target")), dt(plan.get("scheduled_exit"))
                if a and b and now >= a:
                    bars = v2.intraday_bars(plan["symbol"], a.astimezone(TZ),
                                            min(now, b + timedelta(hours=3)).astimezone(TZ))
            result = replay_weekly(plan, bars, now)
        # If a virtual fill was already verified, it must never shift on a later download.
        prev_fill = previous.get("virtual_fill")
        new_fill = result.get("virtual_fill")
        if prev_fill and new_fill and prev_fill != new_fill:
            row["outcome"] = {**previous, "status": "blocked", "reason": "virtual_entry_evidence_changed"}
            continue
        if result.get("status") not in {"resolved", "pending", "pending_market_data"}:
            row["outcome"] = {**previous, **result}
            continue
        comparison = wes_week_result(weeks.get(wid) or {}, now, dt(plan.get("scheduled_exit")))
        if result["status"] == "resolved" and comparison.get("status") == "resolved":
            comparison["incremental_weekly_net_percent"] = round(
                comparison["net_result_percent"] - result["net_result_percent"], 8)
        row["outcome"] = {**result, "wes_comparison": comparison}
    return out


def report(state: Mapping[str, Any]) -> dict[str, Any]:
    rows = list((state.get("weeks") or {}).values())
    settled = [x for x in rows if (x.get("outcome") or {}).get("wes_comparison", {}).get("status") == "resolved"
               and (x.get("outcome") or {}).get("status") == "resolved"]
    return {"schema_version": SCHEMA, "execution_authority": False, "automatic_promotion": False,
            "benchmark_definition": "weekly_frozen_forecast_under_v5_governance_not_full_v5_execution",
            "sample": {"weeks_recorded": len(rows), "captured_prospectively": sum(x.get("status") == "captured" for x in rows),
                       "missed_or_ineligible": sum(x.get("status") != "captured" for x in rows),
                       "virtual_entries_verified": sum(bool((x.get("outcome") or {}).get("virtual_fill")) for x in rows),
                       "resolved_baseline_weeks": sum((x.get("outcome") or {}).get("status") == "resolved" for x in rows),
                       "resolved_weekly_comparisons": len(settled)},
            "comparisons": [{"week_id": x["week_id"],
                             "wes_net_percent": x["outcome"]["wes_comparison"]["net_result_percent"],
                             "baseline_net_percent": x["outcome"]["net_result_percent"],
                             "incremental_net_percent": x["outcome"]["wes_comparison"]["incremental_weekly_net_percent"]}
                            for x in settled]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("capture", "evaluate"), required=True)
    p.add_argument("--state", type=Path, default=STATE)
    p.add_argument("--report", type=Path, default=REPORT)
    p.add_argument("--week-id")
    args = p.parse_args()
    now = datetime.now(timezone.utc)
    wid = args.week_id or f"{now.astimezone(TZ).isocalendar().year}-W{now.astimezone(TZ).isocalendar().week:02d}"
    state = load(args.state, empty())
    week = load(WEEKS / f"{wid}.json", {})
    if args.stage == "capture" and week:
        state = capture(state, week, now, load(cf.POLICY_PATH, {}))
    if args.stage == "evaluate":
        years = {k: load(WEEKS / f"{k}.json", {}) for k in state.get("weeks", {})}
        state = evaluate(state, years, now)
    save(args.state, state)
    save(args.report, report(state))
    print(json.dumps({"stage": args.stage, **report(state)["sample"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
