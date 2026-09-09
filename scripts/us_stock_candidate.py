#!/usr/bin/env python3
"""US Stock Trading candidate publisher without a mandatory daily transaction.

The existing US ranking/evidence engine remains the source of candidate quality.
This adapter changes only the trading contract: score below the configured target
is CASH/NO_TRADE, a qualified setup has no fixed holding deadline, and the actual
position is owned by stock_trading_portfolio.py (up to three US positions).
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from typing import Any

try:
    from scripts import us_daily_stock as us
except ModuleNotFoundError:
    import us_daily_stock as us


def transform(payload: dict[str, Any], *, config: dict[str, Any], now) -> dict[str, Any]:
    result = deepcopy(payload)
    if result.get("decision") != "TRADE":
        return result
    selection = result.get("selection") or {}
    score = float(selection.get("score") or 0.0)
    target = float(config["target_score"])
    if score < target:
        cash = us.base_payload(
            now,
            config,
            "NO_TRADE",
            f"Best reviewed US candidate scored {score:.2f}, below the {target:.0f} Stock Trading admission threshold. CASH remains valid.",
        )
        cash["locked"] = True
        cash["candidate_watch"] = {
            "symbol": selection.get("symbol"),
            "score": score,
            "required_score": target,
            "reason": "entry_score_below_threshold",
        }
        cash["data_quality"] = result.get("data_quality") or {}
        cash["metrics"] = result.get("metrics") or us.metric_summary()
        return cash

    entry = float((selection.get("market_snapshot") or {}).get("last") or selection.get("reference_price") or 0.0)
    stop = float(selection.get("stop") or 0.0)
    if entry <= 0 or not stop < entry:
        raise us.PublicationError("Qualified US Stock Trading setup has invalid entry/SL geometry.")
    selection["risk_percent"] = round((entry - stop) / entry, 6)
    selection["selection_mode"] = "MODEL_QUALIFIED_STOCK_TRADING"
    selection["holding_policy"] = "OPEN_ENDED_MODEL_CONTROLLED"
    selection["valid_until"] = None
    selection["time_stop"] = None
    selection["early_exit"] = "SL/TP are reviewed continuously; daily model review may recalculate SL/TP or close the position immediately when the thesis deteriorates."
    result["selection"] = selection
    result["reason"] = (
        f"Qualified US Stock Trading candidate: score {score:.2f} met the {target:.0f} threshold. "
        "No fixed holding deadline applies; the canonical portfolio controls the exit."
    )
    result["position_action"] = "CANDIDATE_FOR_PORTFOLIO"
    result.setdefault("methodology", {}).update({
        "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
        "fixed_holding_deadline": None,
        "max_open_positions_us": 3,
        "forced_trade_allowed": False,
        "daily_sl_tp_review": True,
        "immediate_model_exit_allowed": True,
    })
    return result


def validate(payload: dict[str, Any], *, config: dict[str, Any], now) -> None:
    us.validate_payload(payload, require_today=True, now=now)
    if payload.get("decision") != "TRADE":
        return
    selection = payload.get("selection") or {}
    if float(selection.get("score") or 0.0) < float(config["target_score"]):
        raise us.PublicationError("US Stock Trading cannot publish a below-threshold trade.")
    text = f"{selection.get('selection_mode','')} {(selection.get('review') or {}).get('mode','')}".upper()
    if "MANDATORY" in text or "FORCED" in text:
        raise us.PublicationError("US Stock Trading cannot publish a forced candidate.")
    if selection.get("valid_until") is not None or selection.get("time_stop") is not None:
        raise us.PublicationError("US Stock Trading candidate cannot carry a fixed holding deadline.")
    if selection.get("holding_policy") != "OPEN_ENDED_MODEL_CONTROLLED":
        raise us.PublicationError("US Stock Trading holding policy is not open-ended/model-controlled.")
    if float(selection.get("risk_percent") or 0) <= 0:
        raise us.PublicationError("US Stock Trading candidate is missing risk_percent.")


def publish(payload: dict[str, Any], *, now) -> None:
    config = us.load_config()
    validate(payload, config=config, now=now)
    us.atomic_json(us.PUBLIC_PATH, payload)
    us.atomic_json(us.METRICS_PATH, payload.get("metrics") or {})
    if payload.get("decision") in {"TRADE", "NO_TRADE"}:
        us.atomic_json(us.HISTORY_DIR / f"{payload['date']}.json", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "validate"], default="auto")
    args = parser.parse_args()
    now = us.now_ny()
    config = us.load_config()
    if args.mode == "validate":
        payload = us.load_json(us.PUBLIC_PATH)
        validate(payload, config=config, now=now)
        print("US_STOCK_CANDIDATE_OK", payload.get("decision"), (payload.get("selection") or {}).get("symbol"))
        return 0
    try:
        payload = transform(us.generate(now), config=config, now=now)
    except Exception as exc:
        payload = us.base_payload(now, config, "DATA_ERROR", f"US Stock Trading candidate generation stopped: {type(exc).__name__}.")
        payload["data_quality"] = {"status": "failed", "error": str(exc)[:500]}
    publish(payload, now=now)
    print(json.dumps({"decision": payload.get("decision"), "symbol": (payload.get("selection") or {}).get("symbol"), "score": (payload.get("selection") or {}).get("score")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
