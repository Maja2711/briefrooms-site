#!/usr/bin/env python3
"""Review active BriefRooms model scenarios during the week.

This writes an educational model log only. It does not provide personal financial advice.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from investments_weekly import (
    METHOD_PATH,
    current_week_file,
    now_local,
    load_json,
    write_json,
    get_current_price,
    safe_float,
    forecast_instrument,
    calculate_result,
    render_pages,
)


def scenario_units(item: Dict[str, Any], price: float, cfg: Dict[str, Any]) -> Optional[float]:
    entry = safe_float(item.get("entry_price"))
    direction = item.get("direction")
    if entry is None or direction not in {"long", "short"}:
        return None
    unit = safe_float(cfg.get("pip_size")) or safe_float(cfg.get("point_size")) or 1.0
    raw = price - entry
    return (raw if direction == "long" else -raw) / unit


def thresholds(inst_id: str, review: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    cfg = review.get("thresholds", {}).get(inst_id, {})
    positive = safe_float(cfg.get("favorable_pips")) or safe_float(cfg.get("favorable_points"))
    negative = safe_float(cfg.get("adverse_pips")) or safe_float(cfg.get("adverse_points"))
    return positive, negative


def record_model_end(
    item: Dict[str, Any],
    cfg: Dict[str, Any],
    price: float,
    timestamp: str,
    source: str,
    key: str,
    reason_pl: str,
    reason_en: str,
    units: float,
) -> None:
    item["exit_price"] = price
    item["exit_captured_at"] = timestamp
    item["exit_source"] = source
    item["scenario_status"] = "ended_intraweek"
    item["scenario_end_reason"] = key
    item["scenario_end_reason_pl"] = reason_pl
    item["scenario_end_reason_en"] = reason_en
    item["scenario_end_units"] = round(units, 2)
    item["scenario_reviewed_at"] = now_local().isoformat(timespec="seconds")
    item.setdefault("rationale_pl", []).insert(0, f"Scenariusz modelowy zakończony w trakcie tygodnia: {reason_pl}. Cena: {price}. Czas: {timestamp}.")
    item.setdefault("rationale_en", []).insert(0, f"Model scenario ended during the week: {reason_en}. Price: {price}. Time: {timestamp}.")
    calculate_result(item, cfg)


def main() -> None:
    dt = now_local()
    if dt.weekday() not in {1, 2, 3, 4}:
        return

    method = load_json(METHOD_PATH, {})
    review = method.get("intraweek_scenario_review", {})
    if not review.get("enabled"):
        return

    path = current_week_file(dt)
    data = load_json(path, {})
    if not data:
        return

    cfg_by_id = {x["id"]: x for x in method.get("instruments", [])}
    changed = False

    for item in data.get("instruments", []):
        inst_id = item.get("instrument_id")
        cfg = cfg_by_id.get(inst_id)
        if not cfg:
            continue
        if item.get("direction") == "neutral":
            continue
        if item.get("entry_price") is None or item.get("exit_price") is not None:
            continue

        quote = get_current_price(item.get("symbol") or cfg.get("symbol"))
        price = safe_float(quote.price)
        if price is None:
            continue
        units = scenario_units(item, price, cfg)
        if units is None:
            continue

        positive, negative = thresholds(str(inst_id), review)
        if positive is not None and units >= positive:
            record_model_end(
                item, cfg, price, quote.timestamp, quote.source,
                "positive_threshold",
                "osiągnięty dodatni próg scenariusza modelowego",
                "positive model-scenario threshold reached",
                units,
            )
            changed = True
            continue

        if negative is not None and units <= -negative:
            record_model_end(
                item, cfg, price, quote.timestamp, quote.source,
                "negative_threshold",
                "osiągnięty ujemny próg scenariusza modelowego",
                "negative model-scenario threshold reached",
                units,
            )
            changed = True
            continue

        fresh = forecast_instrument(cfg, method)
        fresh_direction = fresh.get("direction")
        fresh_score = int(safe_float(fresh.get("score")) or 0)
        min_score = int(review.get("model_reversal_min_abs_score", 25))
        if fresh_direction in {"long", "short"} and fresh_direction != item.get("direction") and abs(fresh_score) >= min_score:
            record_model_end(
                item, cfg, price, quote.timestamp, quote.source,
                "model_reversal",
                f"świeży sygnał modelu odwrócił kierunek na {fresh_direction} przy score {fresh_score}",
                f"fresh model signal reversed to {fresh_direction} with score {fresh_score}",
                units,
            )
            item["fresh_reversal_direction"] = fresh_direction
            item["fresh_reversal_score"] = fresh_score
            changed = True

    if changed:
        write_json(path, data)
        render_pages()


if __name__ == "__main__":
    main()
