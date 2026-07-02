#!/usr/bin/env python3
"""Risk plan, intraweek review and learning journal for BriefRooms weekly positions.

Educational model only. It creates stop loss / take profit levels, reviews open
positions, records exits when SL/TP is reached during a scheduled review, and
stores post-trade lessons so the method can be improved without rewriting old
forecasts.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import investments_weekly as base

LEARNING_DIR = base.REPO / "data" / "investments" / "learning"
LESSONS_PATH = LEARNING_DIR / "lessons.json"
RISK_MODEL_VERSION = "risk-learning-1.0"


def direction(item: Dict[str, Any]) -> str:
    raw = str(item.get("direction") or "neutral")
    if raw in {"long", "short"}:
        return raw
    score = base.safe_float(item.get("score"))
    return "short" if score is not None and score < 0 else "long"


def unit_size(inst_id: str, cfg: Dict[str, Any]) -> float:
    if inst_id == "eurusd":
        return base.safe_float(cfg.get("pip_size")) or 0.0001
    return base.safe_float(cfg.get("point_size")) or 1.0


def notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, str]:
    inst_id = str(item.get("instrument_id") or "")
    if inst_id == "eurusd":
        return base.safe_float(item.get("notional_eur")) or base.safe_float(cfg.get("notional_eur")) or 10_000.0, "EUR"
    return base.safe_float(item.get("notional_usd")) or base.safe_float(cfg.get("notional_usd")) or 10_000.0, "USD"


def pnl_usd(inst_id: str, entry: float, mark: float, side: str, item: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    move = (mark - entry) if side == "long" else (entry - mark)
    n, _ = notional(item, cfg)
    if inst_id == "eurusd":
        return move * n
    pct = move / entry if entry else 0.0
    return pct * n


def money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f} USD".replace(",", " ")


def clipped_units(inst_id: str, method: Dict[str, Any], favorable: float, adverse: float) -> Tuple[float, float]:
    model = method.get("volatility_threshold_model", {}) if isinstance(method, dict) else {}
    limits = (model.get("unit_limits", {}) or {}).get(inst_id, {})
    fallback = (model.get("fallback_static_thresholds", {}) or {}).get(inst_id, {})
    fav = base.safe_float(favorable) or base.safe_float(fallback.get("favorable_units")) or 90.0
    adv = base.safe_float(adverse) or base.safe_float(fallback.get("adverse_units")) or 60.0
    fav_min = base.safe_float(limits.get("min_favorable_units"))
    fav_max = base.safe_float(limits.get("max_favorable_units"))
    adv_min = base.safe_float(limits.get("min_adverse_units"))
    adv_max = base.safe_float(limits.get("max_adverse_units"))
    if fav_min is not None and fav_max is not None:
        fav = base.clip(fav, fav_min, fav_max)
    if adv_min is not None and adv_max is not None:
        adv = base.clip(adv, adv_min, adv_max)
    return round(fav, 2), round(adv, 2)


def volatility_units(symbol: str, inst_id: str, cfg: Dict[str, Any], method: Dict[str, Any]) -> Tuple[float, float, str]:
    model = method.get("volatility_threshold_model", {}) if isinstance(method, dict) else {}
    closes = base.download_close_series(symbol, str(model.get("price_history_period") or "6mo"))
    u_size = unit_size(inst_id, cfg)
    if len(closes) >= 22:
        diffs = [abs(closes[i] - closes[i - 1]) / u_size for i in range(1, len(closes))]
        daily_units = sum(diffs[-20:]) / min(20, len(diffs))
        expected_week = daily_units * math.sqrt(5)
        fav_mult = base.safe_float(model.get("favorable_multiplier")) or 0.9
        adv_mult = base.safe_float(model.get("adverse_multiplier")) or 0.6
        fav, adv = clipped_units(inst_id, method, expected_week * fav_mult, expected_week * adv_mult)
        return fav, adv, "dynamic_close_to_close_volatility_20d"
    fallback = (model.get("fallback_static_thresholds", {}) or {}).get(inst_id, {})
    fav, adv = clipped_units(inst_id, method, base.safe_float(fallback.get("favorable_units")) or 90, base.safe_float(fallback.get("adverse_units")) or 60)
    return fav, adv, "fallback_static_thresholds"


def risk_plan(item: Dict[str, Any], cfg: Dict[str, Any], method: Dict[str, Any], entry: float) -> Dict[str, Any]:
    inst_id = str(item.get("instrument_id") or "")
    side = direction(item)
    u_size = unit_size(inst_id, cfg)
    symbol = str(item.get("symbol") or cfg.get("symbol") or "")
    tp_units, sl_units, source = volatility_units(symbol, inst_id, cfg, method)
    tp_delta = tp_units * u_size
    sl_delta = sl_units * u_size
    if side == "long":
        tp_price, sl_price = entry + tp_delta, entry - sl_delta
    else:
        tp_price, sl_price = entry - tp_delta, entry + sl_delta
    risk = abs(pnl_usd(inst_id, entry, sl_price, side, item, cfg))
    reward = abs(pnl_usd(inst_id, entry, tp_price, side, item, cfg))
    return {
        "model_version": RISK_MODEL_VERSION,
        "generated_at": base.now_local().isoformat(timespec="seconds"),
        "direction": side,
        "stop_loss_price": round(sl_price, 5 if inst_id == "eurusd" else 2),
        "take_profit_price": round(tp_price, 5 if inst_id == "eurusd" else 2),
        "stop_loss_units": sl_units,
        "take_profit_units": tp_units,
        "risk_usd": round(risk, 2),
        "potential_reward_usd": round(reward, 2),
        "reward_to_risk": round(reward / risk, 2) if risk else None,
        "threshold_source": source,
        "rule_pl": "SL i TP są liczone z ostatniej zmienności i ograniczone limitami metody; poziomy zapisujemy przy pozycji, żeby później porównać plan z wynikiem.",
        "rule_en": "SL and TP are calculated from recent volatility and clipped by method limits; levels are saved with the position so the plan can be reviewed against the outcome.",
    }


def hit_status(item: Dict[str, Any], current: Optional[float]) -> Tuple[str, str, str]:
    if current is None:
        return "open", "Brak świeżej ceny do oceny SL/TP.", "No fresh price to evaluate SL/TP."
    plan = item.get("risk_plan") or {}
    sl = base.safe_float(plan.get("stop_loss_price"))
    tp = base.safe_float(plan.get("take_profit_price"))
    side = direction(item)
    if sl is None or tp is None:
        return "open", "Brak zapisanego planu SL/TP.", "No saved SL/TP plan."
    if side == "long":
        if current <= sl:
            return "stop_loss_hit", "Cena zeszła do strefy stop loss — scenariusz uznany za błędny.", "Price reached the stop-loss zone — scenario marked wrong."
        if current >= tp:
            return "take_profit_hit", "Cena dotarła do take profit — scenariusz zrealizował zakładany ruch.", "Price reached take profit — scenario delivered the planned move."
    else:
        if current >= sl:
            return "stop_loss_hit", "Cena wzrosła do strefy stop loss — scenariusz uznany za błędny.", "Price rose to the stop-loss zone — scenario marked wrong."
        if current <= tp:
            return "take_profit_hit", "Cena dotarła do take profit — scenariusz zrealizował zakładany ruch.", "Price reached take profit — scenario delivered the planned move."
    return "open", "Pozycja jest otwarta; analizujemy, czy cena zbliża się do SL albo TP.", "Position remains open; price is reviewed versus SL and TP."


def review_item(week: Dict[str, Any], item: Dict[str, Any], cfg: Dict[str, Any], method: Dict[str, Any], live_prices: Dict[str, Any]) -> bool:
    inst_id = str(item.get("instrument_id") or "")
    entry = base.safe_float(item.get("entry_price"))
    changed = False
    if entry is None:
        item["risk_status"] = "pending_entry"
        return True
    if not isinstance(item.get("risk_plan"), dict) or item.get("risk_plan", {}).get("model_version") != RISK_MODEL_VERSION:
        item["risk_plan"] = risk_plan(item, cfg, method, entry)
        changed = True
    rec = base.live_record(live_prices, inst_id)
    current = base.safe_float(rec.get("price"))
    status, note_pl, note_en = hit_status(item, current)
    previous = item.get("risk_status")
    item["risk_status"] = "closed_week" if base.safe_float(item.get("exit_price")) is not None and item.get("exit_reason") not in {"take_profit", "stop_loss"} else status
    item["risk_review"] = {
        "last_review_at": base.now_local().isoformat(timespec="seconds"),
        "last_review_price": current,
        "last_review_source": rec.get("source") or "Yahoo Finance",
        "live_pnl_usd": round(pnl_usd(inst_id, entry, current, direction(item), item, cfg), 2) if current is not None else None,
        "observation_pl": note_pl,
        "observation_en": note_en,
    }
    if status in {"take_profit_hit", "stop_loss_hit"} and base.safe_float(item.get("exit_price")) is None and current is not None:
        item["exit_price"] = current
        item["exit_captured_at"] = rec.get("current_price_updated_at") or rec.get("timestamp") or base.now_local().isoformat(timespec="seconds")
        item["exit_source"] = rec.get("source") or "Yahoo Finance"
        item["exit_reason"] = "take_profit" if status == "take_profit_hit" else "stop_loss"
        item["risk_exit_type"] = item["exit_reason"]
        try:
            base.calculate_result(item, cfg)
        except Exception:
            pass
        changed = True
    return changed or previous != item.get("risk_status")


def lesson_for(item: Dict[str, Any]) -> Tuple[str, str]:
    reason = str(item.get("exit_reason") or "weekly_close")
    value = base.safe_float(item.get("result_value"))
    if reason == "stop_loss":
        return (
            "Błąd scenariusza: cena doszła do stop loss. W kolejnych tygodniach trzeba sprawdzić, czy sygnał kierunku nie był zbyt słaby albo czy SL nie był ustawiony zbyt blisko aktualnej zmienności.",
            "Scenario error: price reached stop loss. In future weeks, check whether the direction signal was too weak or whether SL was too tight for current volatility.",
        )
    if reason == "take_profit":
        return (
            "Sukces scenariusza: cena doszła do take profit. Warto zapisać, które sygnały trendu i momentum potwierdziły ruch, aby nie zmieniać działającej reguły bez powodu.",
            "Scenario success: price reached take profit. Record which trend and momentum signals confirmed the move before changing a working rule.",
        )
    if value is not None and value < 0:
        return (
            "Strata po zamknięciu tygodnia: kierunek nie dał przewagi do piątku. Wniosek: sprawdzić, czy model nie trzymał pozycji zbyt długo mimo pogorszenia momentum w trakcie tygodnia.",
            "Weekly-close loss: direction did not provide an edge by Friday. Lesson: check whether the model held the position too long despite deteriorating momentum during the week.",
        )
    if value is not None and value > 0:
        return (
            "Zysk po zamknięciu tygodnia: kierunek był zgodny z ruchem rynku. Wniosek: porównać wynik z TP, żeby ocenić, czy realizacja zysku w trakcie tygodnia byłaby lepsza od piątkowego zamknięcia.",
            "Weekly-close profit: direction matched the market move. Lesson: compare the result with TP to judge whether intraweek profit-taking would have been better than Friday close.",
        )
    return ("Pozycja zakończona płasko; wniosek: sprawdzić, czy przy niskiej zmienności model powinien redukować wielkość oczekiwanego ruchu.", "Flat result; lesson: check whether the model should reduce expected move size in low-volatility conditions.")


def update_lessons(weeks: List[Dict[str, Any]]) -> None:
    journal = base.load_json(LESSONS_PATH, {"model_version": RISK_MODEL_VERSION, "updated_at": "", "records": [], "summary": {}})
    records = journal.get("records", []) if isinstance(journal.get("records"), list) else []
    seen = {str(r.get("key")) for r in records if isinstance(r, dict)}
    for week in weeks:
        week_id = str(week.get("week_id") or "")
        for item in week.get("instruments", []):
            if base.safe_float(item.get("exit_price")) is None:
                continue
            inst_id = str(item.get("instrument_id") or "")
            key = f"{week_id}:{inst_id}:{item.get('exit_reason') or 'weekly_close'}"
            if key in seen:
                continue
            lesson_pl, lesson_en = lesson_for(item)
            records.append({
                "key": key,
                "week_id": week_id,
                "instrument_id": inst_id,
                "direction": direction(item),
                "entry_price": item.get("entry_price"),
                "exit_price": item.get("exit_price"),
                "exit_reason": item.get("exit_reason") or "weekly_close",
                "result_value_usd": item.get("result_value"),
                "result_percent": item.get("result_percent"),
                "lesson_pl": lesson_pl,
                "lesson_en": lesson_en,
                "recorded_at": base.now_local().isoformat(timespec="seconds"),
            })
            seen.add(key)
    summary: Dict[str, Any] = {}
    for rec in records:
        inst = str(rec.get("instrument_id") or "")
        if not inst:
            continue
        s = summary.setdefault(inst, {"closed": 0, "wins": 0, "losses": 0, "stop_losses": 0, "take_profits": 0, "total_usd": 0.0})
        val = base.safe_float(rec.get("result_value_usd")) or 0.0
        s["closed"] += 1
        s["total_usd"] = round(s["total_usd"] + val, 2)
        if val > 0:
            s["wins"] += 1
        elif val < 0:
            s["losses"] += 1
        if rec.get("exit_reason") == "stop_loss":
            s["stop_losses"] += 1
        if rec.get("exit_reason") == "take_profit":
            s["take_profits"] += 1
    for inst, s in summary.items():
        closed = s.get("closed") or 0
        s["win_rate"] = round((s.get("wins", 0) / closed) * 100, 1) if closed else 0
        s["lesson_pl"] = "Za mało danych do wniosku." if closed < 3 else ("Za dużo stop loss — sprawdzić filtry wejścia i szerokość SL." if s.get("stop_losses", 0) >= max(2, closed // 2) else "Kontynuować pomiar; nie zmieniać metody po jednym tygodniu.")
        s["lesson_en"] = "Not enough data for a stable conclusion." if closed < 3 else ("Too many stop losses — review entry filters and SL width." if s.get("stop_losses", 0) >= max(2, closed // 2) else "Continue measuring; do not change the method after one week.")
    journal.update({"model_version": RISK_MODEL_VERSION, "updated_at": base.now_local().isoformat(timespec="seconds"), "records": records[-200:], "summary": summary})
    base.write_json(LESSONS_PATH, journal)


def review_all() -> None:
    method = base.load_json(base.METHOD_PATH, {})
    live_prices = base.load_json(base.LIVE_PRICE_PATH, {"prices": {}})
    cfg = {x.get("id"): x for x in method.get("instruments", [])}
    weeks = base.load_weeklies()
    changed_paths = set()
    for week in weeks:
        path = base.WEEKLY_DIR / f"{week.get('week_id')}.json"
        changed = False
        for item in week.get("instruments", []):
            changed = review_item(week, item, cfg.get(item.get("instrument_id"), {}), method, live_prices) or changed
        if changed:
            base.write_json(path, week)
            changed_paths.add(str(path))
    update_lessons(weeks)
    print(f"Risk review complete. Updated weekly files: {len(changed_paths)}. Lessons: {LESSONS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["review"], default="review")
    parser.parse_args()
    review_all()


if __name__ == "__main__":
    main()
