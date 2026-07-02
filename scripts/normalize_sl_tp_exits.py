#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        return None
    return None


def load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def direction(item: Dict[str, Any]) -> str:
    raw = str(item.get("direction") or item.get("effective_direction") or "")
    if raw in {"long", "short"}:
        return raw
    score = safe_float(item.get("score"))
    return "short" if score is not None and score < 0 else "long"


def hit_at(item: Dict[str, Any], price: float) -> Optional[Tuple[str, float]]:
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    sl = safe_float(plan.get("stop_loss_price"))
    tp = safe_float(plan.get("take_profit_price"))
    if sl is None or tp is None:
        return None
    side = direction(item)
    if side == "long":
        if price <= sl:
            return "stop_loss", sl
        if price >= tp:
            return "take_profit", tp
    else:
        if price >= sl:
            return "stop_loss", sl
        if price <= tp:
            return "take_profit", tp
    return None


def set_result(item: Dict[str, Any], exit_price: float) -> None:
    entry = safe_float(item.get("entry_price"))
    if entry is None or entry == 0:
        return
    side = direction(item)
    inst = str(item.get("instrument_id") or "")
    move = (exit_price - entry) if side == "long" else (entry - exit_price)
    if inst == "eurusd":
        notional = safe_float(item.get("notional_eur")) or 10000.0
        value = move * notional
        units = move / 0.0001
    else:
        notional = safe_float(item.get("notional_usd")) or 10000.0
        value = (move / entry) * notional
        units = move
    pct = (move / entry) * 100.0
    item["result"] = "gain" if value > 0 else "loss" if value < 0 else "flat"
    item["result_value"] = round(value, 8)
    item["result_percent"] = round(pct, 4)
    item["result_units"] = round(units, 8)
    item["result_currency"] = "USD"


def normalize_item(item: Dict[str, Any]) -> bool:
    entry = safe_float(item.get("entry_price"))
    if entry is None:
        return False
    check_price = safe_float(item.get("exit_observed_price")) or safe_float(item.get("exit_price"))
    if check_price is None:
        return False
    hit = hit_at(item, check_price)
    if hit is None:
        return False
    reason, planned = hit
    current_exit = safe_float(item.get("exit_price"))
    already_planned = item.get("exit_execution_model") == "planned_sl_tp_level" and current_exit is not None and abs(current_exit - planned) < 1e-8
    if already_planned:
        return False
    if current_exit is not None and abs(current_exit - planned) > 1e-8:
        item["exit_observed_price"] = current_exit
    item["exit_price"] = planned
    item["exit_reason"] = reason
    item["risk_exit_type"] = reason
    item["risk_status"] = "take_profit_hit" if reason == "take_profit" else "stop_loss_hit"
    item["exit_execution_model"] = "planned_sl_tp_level"
    review = item.get("risk_review") if isinstance(item.get("risk_review"), dict) else {}
    if reason == "stop_loss":
        review["observation_pl"] = "Cena naruszyła stop loss. Wynik modelowy liczony jest po zapisanym poziomie SL, a późniejsza cena pozostaje tylko ceną obserwowaną."
        review["observation_en"] = "Price breached stop loss. Model result is calculated at the saved SL level; the later price is only the observed price."
    else:
        review["observation_pl"] = "Cena naruszyła take profit. Wynik modelowy liczony jest po zapisanym poziomie TP, a późniejsza cena pozostaje tylko ceną obserwowaną."
        review["observation_en"] = "Price breached take profit. Model result is calculated at the saved TP level; the later price is only the observed price."
    item["risk_review"] = review
    set_result(item, planned)
    return True


def main() -> None:
    changed_files = 0
    changed_items = 0
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        data = load(path)
        changed = False
        for item in data.get("instruments", []):
            if normalize_item(item):
                changed = True
                changed_items += 1
        if changed:
            save(path, data)
            changed_files += 1
    print(f"Normalized SL/TP exits: {changed_items} items in {changed_files} files")


if __name__ == "__main__":
    main()
