#!/usr/bin/env python3
"""Review active BriefRooms model scenarios during the week."""
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
from investments_thresholds import calculate_dynamic_thresholds


def scenario_units(item: Dict[str, Any], price: float, cfg: Dict[str, Any]) -> Optional[float]:
    entry = safe_float(item.get("entry_price"))
    direction = item.get("direction")
    if entry is None or direction not in {"long", "short"}:
        return None
    inst_id = item.get("instrument_id") or cfg.get("id")
    unit_name = ((item.get("scenario_thresholds") or {}).get("unit") or cfg.get("result_unit") or "")
    raw = price - entry
    if inst_id == "btcusd" or unit_name == "percent":
        move = (raw / entry) * 100 if entry else 0.0
        return move if direction == "long" else -move
    unit = safe_float(cfg.get("pip_size")) or safe_float(cfg.get("point_size")) or 1.0
    return (raw if direction == "long" else -raw) / unit


def thresholds(item: Dict[str, Any], cfg: Dict[str, Any], method: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], bool]:
    saved = item.get("scenario_thresholds") or {}
    upper = safe_float(saved.get("favorable_units"))
    lower = safe_float(saved.get("adverse_units"))
    if upper is not None and lower is not None:
        return upper, lower, False
    dynamic = calculate_dynamic_thresholds(cfg, method)
    item["scenario_thresholds"] = dynamic
    return safe_float(dynamic.get("favorable_units")), safe_float(dynamic.get("adverse_units")), True


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

        upper, lower, threshold_saved = thresholds(item, cfg, method)
        changed = changed or threshold_saved
        unit_label = (item.get("scenario_thresholds") or {}).get("unit") or ("pips" if inst_id == "eurusd" else "points")

        if upper is not None and units >= upper:
            record_model_end(
                item, cfg, price, quote.timestamp, quote.source,
                "upper_dynamic_threshold",
                f"osiągnięty górny dynamiczny próg modelu ({upper} {unit_label})",
                f"upper dynamic model threshold reached ({upper} {unit_label})",
                units,
            )
            changed = True
            continue

        if lower is not None and units <= -lower:
            record_model_end(
                item, cfg, price, quote.timestamp, quote.source,
                "lower_dynamic_threshold",
                f"osiągnięty dolny dynamiczny próg modelu ({lower} {unit_label})",
                f"lower dynamic model threshold reached ({lower} {unit_label})",
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
                f"fresh model signal changed to {fresh_direction} with score {fresh_score}",
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
