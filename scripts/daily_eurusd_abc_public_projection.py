#!/usr/bin/env python3
"""Build sanitized Polish public projection of private EUR/USD A/B/C state.

PR25 adds a bounded public history so users can inspect resolved forward outcomes
and virtual Entry/Exit/TP/SL results per capture. Raw Beliefs, technical payloads,
decision fingerprints, frozen trade plans/hashes and durability internals remain
private. Only explicit safe summaries leave the research artifact.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "eurusd-abc-public-pl-v2"
TRADE_ENGINE_VERSION = "eurusd-daily-abc-v1.3.0"
PUBLIC_HISTORY_LIMIT = 50
HORIZONS = ("30m", "60m", "120m", "240m", "1440m")
HORIZON_LABELS = {"30m":"30m","60m":"1h","120m":"2h","240m":"4h","1440m":"24h"}
ARM_LABELS = {"A":"Tylko techniczny","B":"Tylko Belief","C":"Hybrydowy"}
DISALLOWED_PUBLIC_KEYS = {
    "beliefs", "decision_sha256", "research_boundary", "checkpoint_id",
    "parent_checkpoint_id", "durability_contract", "technical", "belief_context",
    "trade_plan", "trade_plan_sha256", "trade_path", "source_plan_sha256",
}


def _iso_z(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _number(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _arm_summary(arm_id: str, arm: Mapping[str, Any]) -> dict[str, Any]:
    summary = {
        "arm_id": arm_id,
        "label_pl": ARM_LABELS[arm_id],
        "available": bool(arm.get("available")),
        "direction": str(arm.get("direction") or "UNAVAILABLE"),
        "score": _number(arm.get("score"), 2),
        "confidence": _number(arm.get("confidence"), 4),
    }
    # PR25 exposes only the safe calibration geometry for B, never the raw
    # individual Belief records/evidence.
    if arm_id == "B":
        belief = arm.get("belief") if isinstance(arm.get("belief"), Mapping) else {}
        calibration = belief.get("decision_calibration") if isinstance(belief, Mapping) else {}
        if isinstance(calibration, Mapping) and calibration:
            summary["raw_score"] = _number(belief.get("raw_score"), 2)
            summary["calibration"] = {
                "method": calibration.get("method"),
                "raw_long_trigger": _number(calibration.get("raw_long_trigger"), 4),
                "raw_short_trigger": _number(calibration.get("raw_short_trigger"), 4),
                "equivalent_raw_score_long": _number(calibration.get("equivalent_raw_score_long"), 2),
                "equivalent_raw_score_short": _number(calibration.get("equivalent_raw_score_short"), 2),
                "prospective_from_pr": calibration.get("prospective_from_pr"),
            }
    return summary


def _outcome_arm(result: Mapping[str, Any] | None) -> dict[str, Any]:
    result = result or {}
    return {
        "available": bool(result.get("available")),
        "direction": str(result.get("direction") or "UNAVAILABLE"),
        "directional_correct": result.get("directional_correct") if result.get("directional_correct") in {True, False} else None,
        "signed_return_bps": _number(result.get("signed_return_bps"), 4),
    }


def _latest_horizons(capture: Mapping[str, Any]) -> dict[str, Any]:
    source = capture.get("horizons") or {}
    projected: dict[str, Any] = {}
    for key in HORIZONS:
        row = source.get(key) or {}
        outcome = row.get("outcome")
        item: dict[str, Any] = {
            "label": HORIZON_LABELS[key],
            "minutes": int(row.get("minutes") or int(key[:-1])),
            "target_at": row.get("target_at"),
            "status": "RESOLVED" if isinstance(outcome, Mapping) else "PENDING",
            "resolved_at": None, "price": None, "raw_return_bps": None,
            "arms": {arm_id: _outcome_arm(None) for arm_id in ("A","B","C")},
        }
        if isinstance(outcome, Mapping):
            item["resolved_at"] = outcome.get("resolved_at")
            item["price"] = _number(outcome.get("price"), 5)
            item["raw_return_bps"] = _number(outcome.get("raw_return_bps"), 4)
            arm_rows = outcome.get("arms") or {}
            item["arms"] = {
                arm_id: _outcome_arm(arm_rows.get(arm_id) if isinstance(arm_rows, Mapping) else None)
                for arm_id in ("A","B","C")
            }
        projected[key] = item
    return projected


def _metric(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "matured_captures": int(row.get("matured_captures") or 0),
        "available_captures": int(row.get("available_captures") or 0),
        "signals": int(row.get("signals") or 0),
        "decision_rate": _number(row.get("decision_rate"), 6),
        "hit_rate": _number(row.get("hit_rate"), 6),
        "mean_signed_return_bps_signal_only": _number(row.get("mean_signed_return_bps_signal_only"), 4),
        "mean_strategy_return_bps_all_available": _number(row.get("mean_strategy_return_bps_all_available"), 4),
    }


def _comparison(report: Mapping[str, Any]) -> dict[str, Any]:
    performance = report.get("performance") or {}
    return {
        key: {
            "label": HORIZON_LABELS[key],
            "A": _metric(((performance.get("A") or {}).get(key) if isinstance(performance, Mapping) else None)),
            "B": _metric(((performance.get("B") or {}).get(key) if isinstance(performance, Mapping) else None)),
            "C": _metric(((performance.get("C") or {}).get(key) if isinstance(performance, Mapping) else None)),
        }
        for key in HORIZONS
    }


def _virtual_trade_arm(capture: Mapping[str, Any], arm_id: str) -> dict[str, Any]:
    arm = ((capture.get("arms") or {}).get(arm_id) or {})
    plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), Mapping) else {}
    path = capture.get("trade_path") if isinstance(capture.get("trade_path"), Mapping) else {}
    plan_arm = ((plan.get("arms") or {}).get(arm_id) or {}) if isinstance(plan, Mapping) else {}
    path_arm = ((path.get("arms") or {}).get(arm_id) or {}) if isinstance(path, Mapping) else {}
    if not plan_arm:
        return {
            "tracked": False, "direction": str(arm.get("direction") or "UNAVAILABLE"),
            "status": "NOT_TRACKED_PRE_V13", "entry_price": None, "stop_price": None,
            "target_price": None, "mfe_bps": None, "mfe_at": None, "mae_bps": None,
            "mae_at": None, "first_touch": None, "first_touch_at": None,
            "minutes_to_first_touch": None, "exit_reason": None, "exit_at": None,
            "exit_price": None, "realized_bps": None,
        }
    status = str(path_arm.get("status") or plan_arm.get("status") or "UNAVAILABLE")
    terminal = status in {"CLOSED","AMBIGUOUS"}
    return {
        "tracked": str(plan_arm.get("status")) == "TRACKED",
        "direction": str(plan_arm.get("direction") or arm.get("direction") or "UNAVAILABLE"),
        "status": status,
        "entry_price": _number(plan_arm.get("entry_price"), 5),
        "stop_price": _number(plan_arm.get("stop_price"), 5),
        "target_price": _number(plan_arm.get("target_price"), 5),
        "mfe_bps": _number(path_arm.get("mfe_bps"), 4) if terminal else None,
        "mfe_at": path_arm.get("mfe_at") if terminal else None,
        "mae_bps": _number(path_arm.get("mae_bps"), 4) if terminal else None,
        "mae_at": path_arm.get("mae_at") if terminal else None,
        "first_touch": path_arm.get("first_touch"),
        "first_touch_at": path_arm.get("first_touch_at"),
        "minutes_to_first_touch": _number(path_arm.get("minutes_to_first_touch"), 2),
        "exit_reason": path_arm.get("exit_reason"),
        "exit_at": path_arm.get("exit_at"),
        "exit_price": _number(path_arm.get("exit_price"), 5),
        "realized_bps": _number(path_arm.get("realized_bps"), 4),
    }


def _virtual_trade(capture: Mapping[str, Any]) -> dict[str, Any]:
    plan = capture.get("trade_plan") if isinstance(capture.get("trade_plan"), Mapping) else {}
    risk = (plan.get("risk_contract") or {}) if isinstance(plan, Mapping) else {}
    available = bool(plan)
    return {
        "available": available,
        "virtual_only": True,
        "prospective_only": True,
        "historical_backfill": False,
        "entry_basis": "frozen_reference_price",
        "spread_slippage_included": False,
        "signal_generated_at": plan.get("signal_generated_at") if available else capture.get("captured_at"),
        "horizon_end_at": plan.get("horizon_end_at") if available else None,
        "risk": {
            "atr_timeframe": risk.get("atr_timeframe") if available else None,
            "atr_window": int(risk.get("atr_window")) if available and risk.get("atr_window") is not None else None,
            "atr_value": _number(risk.get("atr_value"), 8) if available else None,
            "atr_multiple": _number(risk.get("atr_multiple"), 4) if available else None,
            "risk_floor_percent": _number(risk.get("risk_floor_percent"), 6) if available else None,
            "risk_distance": _number(risk.get("risk_distance"), 8) if available else None,
            "reward_risk": _number(risk.get("reward_risk"), 4) if available else None,
            "position_horizon_minutes": int(risk.get("position_horizon_minutes")) if available and risk.get("position_horizon_minutes") is not None else None,
            "monitor_interval": risk.get("monitor_interval") if available else None,
        },
        "arms": {arm_id: _virtual_trade_arm(capture, arm_id) for arm_id in ("A","B","C")},
    }


def _trade_metric(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "signals": int(row.get("signals") or 0),
        "open_trades": int(row.get("open_trades") or 0),
        "closed_trades": int(row.get("closed_trades") or 0),
        "ambiguous_same_1m_bar": int(row.get("ambiguous_same_1m_bar") or 0),
        "take_profit": int(row.get("take_profit") or 0),
        "stop_loss": int(row.get("stop_loss") or 0),
        "time_exit_24h": int(row.get("time_exit_24h") or 0),
        "win_rate": _number(row.get("win_rate"), 6),
        "mean_realized_bps": _number(row.get("mean_realized_bps"), 4),
        "mean_mfe_bps": _number(row.get("mean_mfe_bps"), 4),
        "mean_mae_bps": _number(row.get("mean_mae_bps"), 4),
        "mean_minutes_to_first_touch": _number(row.get("mean_minutes_to_first_touch"), 2),
    }


def _trade_comparison(report: Mapping[str, Any]) -> dict[str, Any]:
    trade = report.get("trade_path") if isinstance(report.get("trade_path"), Mapping) else {}
    performance = (trade.get("performance") or {}) if isinstance(trade, Mapping) else {}
    return {
        "prospective_from_engine_version": trade.get("prospective_from_engine_version") or TRADE_ENGINE_VERSION,
        "historical_backfill": False, "virtual_only": True,
        "arms": {arm_id: _trade_metric((performance.get(arm_id) or {}) if isinstance(performance, Mapping) else None)
                 for arm_id in ("A","B","C")},
    }


def _history_capture(capture: Mapping[str, Any]) -> dict[str, Any]:
    arms = capture.get("arms") or {}
    return {
        "signal_generated_at": capture.get("captured_at") or capture.get("market_observed_at"),
        "market_observed_at": capture.get("market_observed_at"),
        "reference_price": _number(capture.get("reference_price"), 5),
        "engine_version": str(capture.get("engine_version") or ""),
        "arms": {arm_id: _arm_summary(arm_id, (arms.get(arm_id) or {})) for arm_id in ("A","B","C")},
        "horizons": _latest_horizons(capture),
        "virtual_trade": _virtual_trade(capture),
    }


def build_public_projection(state: Mapping[str, Any], report: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if state.get("mode") != "research_shadow" or report.get("mode") != "research_shadow":
        raise ValueError("public projection accepts research_shadow input only")
    if report.get("decision_influence") is not False:
        raise ValueError("public projection requires zero decision influence")
    governance = report.get("governance") or {}
    if governance.get("active_daily_engine_influence") is not False:
        raise ValueError("public projection requires zero active Daily engine influence")
    captures = state.get("captures") or []
    if not isinstance(captures, list) or not captures:
        raise ValueError("A/B/C state contains no captures")
    latest = captures[-1]
    if not isinstance(latest, Mapping):
        raise ValueError("latest A/B/C capture must be an object")
    boundary = latest.get("research_boundary") or {}
    if boundary.get("decision_influence") is not False or boundary.get("trade_execution") is not False:
        raise ValueError("latest capture violates zero-authority boundary")
    arms = latest.get("arms") or {}
    if set(arms) != {"A","B","C"}:
        raise ValueError("latest capture must contain A/B/C")

    signal_generated_at = latest.get("captured_at") or latest.get("market_observed_at")
    generated_at = _iso_z(now) if now is not None else str(state.get("updated_at") or signal_generated_at or _iso_z())
    history = [_history_capture(capture) for capture in reversed(captures[-PUBLIC_HISTORY_LIMIT:])]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "language": "pl",
        "generated_at": generated_at,
        "engine_version": str(latest.get("engine_version") or report.get("engine_version") or ""),
        "mode": "LIVE_SHADOW",
        "public_boundary": {
            "read_only_projection": True, "decision_influence": False, "trade_execution": False,
            "belief_writeback": False, "raw_belief_state_exposed": False,
            "private_research_state_exposed": False,
        },
        "sample": {
            "captures": len(captures), "history_exposed": len(history),
            "latest_market_observed_at": latest.get("market_observed_at"),
            "latest_signal_generated_at": signal_generated_at,
        },
        "latest": {
            "market_observed_at": latest.get("market_observed_at"),
            "signal_generated_at": signal_generated_at,
            "reference_price": _number(latest.get("reference_price"), 5),
            "arms": {arm_id: _arm_summary(arm_id, arms[arm_id]) for arm_id in ("A","B","C")},
            "horizons": _latest_horizons(latest),
            "virtual_trade": _virtual_trade(latest),
        },
        "comparison": _comparison(report),
        "trade_comparison": _trade_comparison(report),
        "history": history,
    }
    validate_public_projection(payload)
    return payload


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key)); keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value: keys.update(_walk_keys(child))
    return keys


def validate_public_projection(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected public projection schema")
    if payload.get("language") != "pl" or payload.get("mode") != "LIVE_SHADOW":
        raise ValueError("public projection must be PL live-shadow")
    boundary = payload.get("public_boundary") or {}
    required_false = ("decision_influence","trade_execution","belief_writeback","raw_belief_state_exposed","private_research_state_exposed")
    if boundary.get("read_only_projection") is not True or any(boundary.get(key) is not False for key in required_false):
        raise ValueError("invalid public read-only boundary")
    latest = payload.get("latest") or {}
    if not latest.get("signal_generated_at") or set(latest.get("arms") or {}) != {"A","B","C"}:
        raise ValueError("public projection latest contract invalid")
    if tuple((latest.get("horizons") or {}).keys()) != HORIZONS or tuple((payload.get("comparison") or {}).keys()) != HORIZONS:
        raise ValueError("public projection horizon contract mismatch")
    virtual_trade = latest.get("virtual_trade") or {}
    if virtual_trade.get("virtual_only") is not True or virtual_trade.get("historical_backfill") is not False:
        raise ValueError("invalid public virtual-trade boundary")
    if set(virtual_trade.get("arms") or {}) != {"A","B","C"}:
        raise ValueError("public virtual trade must contain A/B/C")
    trade_comparison = payload.get("trade_comparison") or {}
    if trade_comparison.get("virtual_only") is not True or set(trade_comparison.get("arms") or {}) != {"A","B","C"}:
        raise ValueError("public trade comparison contract mismatch")
    history = payload.get("history")
    if not isinstance(history, list) or len(history) > PUBLIC_HISTORY_LIMIT:
        raise ValueError("public history contract invalid")
    for row in history:
        if set((row.get("arms") or {})) != {"A","B","C"} or tuple((row.get("horizons") or {}).keys()) != HORIZONS:
            raise ValueError("public history capture contract invalid")
    leaked = DISALLOWED_PUBLIC_KEYS.intersection(_walk_keys(payload))
    if leaked:
        raise ValueError(f"private research keys leaked into public projection: {sorted(leaked)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sanitized PL EUR/USD A/B/C public projection")
    parser.add_argument("--state")
    parser.add_argument("--report")
    parser.add_argument("--output", required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    if args.validate:
        validate_public_projection(_load(output))
        print("EURUSD_ABC_PUBLIC_PL_OK", output)
        return 0
    if not args.state or not args.report:
        parser.error("--state and --report are required unless --validate is used")
    payload = build_public_projection(_load(Path(args.state)), _load(Path(args.report)))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("EURUSD_ABC_PUBLIC_PL_WRITTEN", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
