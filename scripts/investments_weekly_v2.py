#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BriefRooms weekly market model v2.

The script fixes the execution and audit problems of the legacy prototype:
- forecasts are frozen before the trading week;
- only non-neutral signals can receive an entry;
- entries are reconstructed from the first 5-minute bar at/after Monday 08:00
  Europe/Warsaw, never from a Friday/Sunday quote;
- volatility is an actual model input and also defines the saved SL/TP distances;
- SL/TP checks use intraday high/low bars and a conservative stop-first rule when
  both levels appear inside the same bar;
- scheduled closes use the first bar at/after the saved Friday 22:00 target;
- closed historical weeks are not rewritten by this script;
- signal strength is explicitly not presented as a probability of success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import investments_weekly as legacy

ROOT = Path(__file__).resolve().parents[1]
METHOD_PATH = ROOT / "data" / "investments" / "methodology.json"
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
TZ = legacy.TZ
MODEL_VERSION = "2.0.0"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def sf(value: Any) -> Optional[float]:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.tzinfo is None:
            out = out.replace(tzinfo=TZ)
        return out.astimezone(TZ)
    except Exception:
        return None


def current_week_path(now: Optional[datetime] = None) -> Path:
    now = now or legacy.now_local()
    return WEEKLY_DIR / f"{legacy.week_id_from_date(now)}.json"


def _series(df: Any, name: str) -> Any:
    values = df[name]
    if hasattr(values, "columns"):
        values = values.iloc[:, 0]
    return values.dropna()


def download_daily(symbol: str, period: str = "3y") -> Any:
    if legacy.yf is None:
        return None
    try:
        df = legacy.yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        return df.dropna(how="all")
    except Exception:
        return None


def ema(values: List[float], span: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def pct_change(values: List[float], periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] == 0:
        return 0.0
    return values[-1] / values[-periods - 1] - 1.0


def atr14_from_df(df: Any) -> Optional[float]:
    try:
        highs = [float(x) for x in _series(df, "High").tolist()]
        lows = [float(x) for x in _series(df, "Low").tolist()]
        closes = [float(x) for x in _series(df, "Close").tolist()]
    except Exception:
        return None
    n = min(len(highs), len(lows), len(closes))
    if n < 20:
        return None
    highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]
    trs: List[float] = []
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return statistics.fmean(trs[-14:]) if len(trs) >= 14 else None


def realized_vol(closes: List[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - lookback, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(returns) < max(10, lookback // 2):
        return None
    return statistics.stdev(returns) * math.sqrt(252.0)


def validated_override(method: Dict[str, Any], inst_id: str, week_id: str, forecast_at: datetime) -> Tuple[float, float]:
    row = ((method.get("manual_overrides") or {}).get(inst_id) or {})
    if str(row.get("valid_for_week") or "") != week_id:
        return 0.0, 0.0
    set_at = parse_dt(row.get("set_at"))
    if set_at is None or set_at > forecast_at:
        return 0.0, 0.0
    macro = clip(sf(row.get("macro_bias")) or 0.0, -10.0, 10.0)
    event_penalty = clip(abs(sf(row.get("event_risk_penalty")) or 0.0), 0.0, 20.0)
    return macro, event_penalty


def entry_thresholds(method: Dict[str, Any], inst_id: str) -> Tuple[float, float]:
    row = (((method.get("decision_rules") or {}).get("entry_thresholds") or {}).get(inst_id) or {})
    return float(row.get("long", 35)), float(row.get("short", -35))


def risk_limits(method: Dict[str, Any], inst_id: str) -> Dict[str, float]:
    row = (((method.get("risk_model") or {}).get("unit_limits") or {}).get(inst_id) or {})
    return {k: float(v) for k, v in row.items() if sf(v) is not None}


def price_distance_to_units(inst_id: str, distance: float, last: float) -> float:
    if inst_id == "eurusd":
        return distance / 0.0001
    if inst_id == "btcusd":
        return distance / last * 100.0 if last else 0.0
    return distance


def units_to_price_distance(inst_id: str, units: float, last: float) -> float:
    if inst_id == "eurusd":
        return units * 0.0001
    if inst_id == "btcusd":
        return last * units / 100.0
    return units


def model_signal(inst: Dict[str, Any], method: Dict[str, Any], week_id: str, forecast_at: datetime) -> Dict[str, Any]:
    df = download_daily(str(inst["symbol"]))
    if df is None:
        return {"direction": "neutral", "score": 0, "signal_strength": 0.0, "data_quality": "failed", "quality_reason": "daily_history_unavailable", "signals": {}}
    closes = [float(x) for x in _series(df, "Close").tolist()]
    if len(closes) < 260:
        return {"direction": "neutral", "score": 0, "signal_strength": 0.0, "data_quality": "failed", "quality_reason": "fewer_than_260_daily_closes", "signals": {"daily_closes": len(closes)}}

    last = closes[-1]
    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    atr14 = atr14_from_df(df)
    vol20 = realized_vol(closes, 20)
    vol60 = realized_vol(closes, 60)
    if atr14 is None or atr14 <= 0 or vol20 is None or vol60 is None or vol20 <= 0 or vol60 <= 0:
        return {"direction": "neutral", "score": 0, "signal_strength": 0.0, "data_quality": "failed", "quality_reason": "atr_or_volatility_unavailable", "signals": {"daily_closes": len(closes)}}

    ret5 = pct_change(closes, 5)
    ret20 = pct_change(closes, 20)
    daily_vol20 = vol20 / math.sqrt(252.0)
    trend_ema = 20.0 * clip(((ema20 - ema50) / atr14) / 1.5, -1.0, 1.0)
    trend_price = 10.0 * clip(((last - ema20) / atr14), -1.0, 1.0)
    momentum5 = 15.0 * clip((ret5 / max(daily_vol20 * math.sqrt(5.0), 1e-9)) / 1.5, -1.0, 1.0)
    momentum20 = 20.0 * clip((ret20 / max(daily_vol20 * math.sqrt(20.0), 1e-9)) / 1.5, -1.0, 1.0)

    prior = closes[-56:-1]
    low55, high55 = min(prior), max(prior)
    range55 = max(high55 - low55, 1e-12)
    range_position = clip((last - low55) / range55, 0.0, 1.0)
    breakout = 15.0 * (2.0 * range_position - 1.0)

    trend_total = trend_ema + trend_price
    momentum_total = momentum5 + momentum20
    raw_directional = trend_total + momentum_total + breakout
    vol_ratio = vol20 / vol60
    vol_scale = 0.70 if vol_ratio >= 1.60 else 0.82 if vol_ratio >= 1.30 else 0.92 if vol_ratio >= 1.10 else 1.0

    macro_bias, event_penalty = validated_override(method, str(inst["id"]), week_id, forecast_at)
    score = int(round(clip((raw_directional * vol_scale + macro_bias) * (1.0 - event_penalty / 100.0), -100.0, 100.0)))

    groups = [trend_total, momentum_total, breakout]
    positive_agreement = sum(1 for x in groups if x > 3.0)
    negative_agreement = sum(1 for x in groups if x < -3.0)
    long_threshold, short_threshold = entry_thresholds(method, str(inst["id"]))
    if score >= long_threshold and positive_agreement >= 2:
        direction = "long"
    elif score <= short_threshold and negative_agreement >= 2:
        direction = "short"
    else:
        direction = "neutral"

    expected_week_move_price = atr14 * math.sqrt(5.0)
    stop_price_distance = expected_week_move_price * float((method.get("risk_model") or {}).get("stop_atr_week_multiplier", 0.55))
    take_price_distance = expected_week_move_price * float((method.get("risk_model") or {}).get("take_atr_week_multiplier", 0.90))
    limits = risk_limits(method, str(inst["id"]))
    stop_units = price_distance_to_units(str(inst["id"]), stop_price_distance, last)
    take_units = price_distance_to_units(str(inst["id"]), take_price_distance, last)
    if limits:
        stop_units = clip(stop_units, limits.get("min_stop_units", stop_units), limits.get("max_stop_units", stop_units))
        take_units = clip(take_units, limits.get("min_take_units", take_units), limits.get("max_take_units", take_units))
    stop_price_distance = units_to_price_distance(str(inst["id"]), stop_units, last)
    take_price_distance = units_to_price_distance(str(inst["id"]), take_units, last)

    signal_strength = round(min(abs(score) / 100.0, 1.0), 2)
    return {
        "direction": direction,
        "score": score,
        "signal_strength": signal_strength,
        "confidence": signal_strength,
        "confidence_type": "heuristic_signal_strength_not_probability",
        "data_quality": "passed",
        "quality_reason": "at_least_260_closes_and_complete_volatility_inputs",
        "signals": {
            "last_close": round(last, 8), "ema20": round(ema20, 8), "ema50": round(ema50, 8), "atr14": round(atr14, 8),
            "ret5_pct": round(ret5 * 100.0, 4), "ret20_pct": round(ret20 * 100.0, 4),
            "vol20_annualized_pct": round(vol20 * 100.0, 4), "vol60_annualized_pct": round(vol60 * 100.0, 4),
            "volatility_regime_ratio": round(vol_ratio, 4), "volatility_score_scale": vol_scale, "range55_position": round(range_position, 4),
            "components": {"trend_ema": round(trend_ema, 2), "trend_price": round(trend_price, 2), "momentum5": round(momentum5, 2), "momentum20": round(momentum20, 2), "breakout": round(breakout, 2), "macro_bias": round(macro_bias, 2), "event_risk_penalty": round(event_penalty, 2)},
            "agreement": {"positive_groups": positive_agreement, "negative_groups": negative_agreement, "required": 2},
        },
        "risk_distance": {"source": "ATR14_sqrt5_frozen_at_forecast", "stop_price_distance": round(stop_price_distance, 8), "take_price_distance": round(take_price_distance, 8), "stop_units": round(stop_units, 4), "take_units": round(take_units, 4), "reward_to_risk": round(take_price_distance / stop_price_distance, 4) if stop_price_distance else None},
    }


def forecast_hash(data: Dict[str, Any]) -> str:
    frozen = {"week_id": data.get("week_id"), "method_version": data.get("method_version"), "forecast_created_at": data.get("forecast_created_at"), "forecast_for_week_start": data.get("forecast_for_week_start"), "forecast_for_week_end": data.get("forecast_for_week_end"), "instruments": [{"instrument_id": x.get("instrument_id"), "direction": x.get("direction"), "score": x.get("score"), "signals": x.get("signals"), "risk_distance": x.get("risk_distance")} for x in data.get("instruments", [])]}
    raw = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def make_forecast() -> Optional[Path]:
    method = read_json(METHOD_PATH, {})
    now = legacy.now_local()
    week_id, monday, friday = legacy.target_forecast_week(now)
    path = WEEKLY_DIR / f"{week_id}.json"
    existing = read_json(path, {})
    if existing.get("forecast_created_at"):
        return path
    items: List[Dict[str, Any]] = []
    for inst in method.get("instruments", []):
        signal = model_signal(inst, method, week_id, now)
        direction = signal.get("direction", "neutral")
        long_threshold, short_threshold = entry_thresholds(method, str(inst["id"]))
        items.append({
            "instrument_id": inst["id"], "symbol": inst["symbol"], "label_pl": inst["label_pl"], "label_en": inst["label_en"], **signal,
            "entry_thresholds": {"long": long_threshold, "short": short_threshold},
            "entry_price": None, "entry_captured_at": None, "entry_source": None,
            "entry_quality_status": "not_due" if direction in {"long", "short"} else "not_applicable_neutral",
            "trade_status": "planned" if direction in {"long", "short"} else "no_trade",
            "risk_plan": None, "exit_price": None, "exit_captured_at": None, "exit_source": None, "exit_reason": None,
            "result": "no_trade" if direction == "neutral" else None, "result_value": 0.0 if direction == "neutral" else None, "result_percent": 0.0 if direction == "neutral" else None,
            "rationale_pl": [f"Model v{MODEL_VERSION}: {legacy.direction_label(direction, 'pl').lower()}.", f"Score {signal.get('score', 0)}; progi: long {long_threshold:g}, short {short_threshold:g}.", "Siła sygnału jest wskaźnikiem heurystycznym, a nie prawdopodobieństwem powodzenia.", "Zmienność wpływa na score i poziomy ryzyka zapisane przed otwarciem."],
            "rationale_en": [f"Model v{MODEL_VERSION}: {legacy.direction_label(direction, 'en').lower()}.", f"Score {signal.get('score', 0)}; thresholds: long {long_threshold:g}, short {short_threshold:g}.", "Signal strength is heuristic, not a probability of success.", "Volatility affects the score and risk levels frozen before entry."],
        })
    data: Dict[str, Any] = {
        "week_id": week_id, "method_version": MODEL_VERSION, "model_status": "paper_trading_rule_based_not_investment_advice",
        "forecast_created_at": now.isoformat(timespec="seconds"), "forecast_locked_at": now.isoformat(timespec="seconds"),
        "forecast_for_week_start": monday.date().isoformat(), "forecast_for_week_end": friday.date().isoformat(), "timezone": "Europe/Warsaw",
        "market_window": {"entry_target_local": monday.isoformat(timespec="seconds"), "entry_latest_local": (monday + timedelta(hours=2)).isoformat(timespec="seconds"), "exit_target_local": friday.isoformat(timespec="seconds")},
        "execution_assumptions": {"entry": "first available 5-minute bar at or after Monday 08:00 Europe/Warsaw", "scheduled_exit": "first available 5-minute bar at or after Friday 22:00 Europe/Warsaw", "same_bar_sl_tp": "stop_loss_first_conservative", "slippage": "not included in live paper log; validation report includes transaction-cost assumptions"},
        "instruments": items,
    }
    data["forecast_hash"] = forecast_hash(data)
    write_json(path, data)
    return path


def _normalize_index(df: Any) -> Any:
    try:
        idx = df.index
        if getattr(idx, "tz", None) is None:
            df.index = idx.tz_localize("UTC").tz_convert(TZ)
        else:
            df.index = idx.tz_convert(TZ)
    except Exception:
        return df
    return df


def intraday_bars(symbol: str, start: datetime, end: datetime) -> Any:
    if legacy.yf is None:
        return None
    try:
        df = legacy.yf.download(symbol, start=(start - timedelta(days=1)).date().isoformat(), end=(end + timedelta(days=1)).date().isoformat(), interval="5m", progress=False, auto_adjust=False, prepost=True, threads=False)
        if df is None or df.empty:
            return None
        return _normalize_index(df)
    except Exception:
        return None


def first_bar_at_or_after(symbol: str, target: datetime, tolerance: timedelta = timedelta(hours=3)) -> Optional[Dict[str, Any]]:
    df = intraday_bars(symbol, target, target + tolerance)
    if df is None:
        return None
    try:
        df = df[(df.index >= target) & (df.index <= target + tolerance)]
        if df.empty:
            return None
        row = df.iloc[0]
        def rv(name: str) -> Optional[float]:
            value = row[name]
            if hasattr(value, "iloc"):
                value = value.iloc[0]
            return sf(value)
        price = rv("Open") or rv("Close")
        if price is None:
            return None
        return {"price": price, "timestamp": df.index[0].to_pydatetime().astimezone(TZ).isoformat(timespec="seconds"), "source": f"Yahoo Finance:{symbol}:5m:first_bar_at_or_after_target"}
    except Exception:
        return None


def build_risk_plan(item: Dict[str, Any], entry: float) -> Optional[Dict[str, Any]]:
    distance = item.get("risk_distance") if isinstance(item.get("risk_distance"), dict) else {}
    stop_distance = sf(distance.get("stop_price_distance"))
    take_distance = sf(distance.get("take_price_distance"))
    if stop_distance is None or take_distance is None or stop_distance <= 0 or take_distance <= 0:
        return None
    side = str(item.get("direction") or "neutral")
    if side == "long":
        sl, tp = entry - stop_distance, entry + take_distance
    elif side == "short":
        sl, tp = entry + stop_distance, entry - take_distance
    else:
        return None
    return {"model_version": MODEL_VERSION, "created_from_frozen_forecast": True, "generated_at": legacy.now_local().isoformat(timespec="seconds"), "direction": side, "stop_loss_price": round(sl, 8), "take_profit_price": round(tp, 8), "stop_loss_distance": round(stop_distance, 8), "take_profit_distance": round(take_distance, 8), "reward_to_risk": round(take_distance / stop_distance, 4), "same_bar_rule": "stop_loss_first_conservative"}


def entry_time_is_valid(item: Dict[str, Any], target: datetime, latest: datetime) -> bool:
    captured = parse_dt(item.get("entry_captured_at"))
    return captured is not None and target <= captured <= latest and sf(item.get("entry_price")) is not None


def capture_entries(path: Optional[Path] = None) -> bool:
    path = path or current_week_path()
    week = read_json(path, {})
    if not week:
        return False
    target = parse_dt((week.get("market_window") or {}).get("entry_target_local"))
    latest = parse_dt((week.get("market_window") or {}).get("entry_latest_local"))
    if target is None:
        return False
    latest = latest or (target + timedelta(hours=2))
    now = legacy.now_local()
    changed = False
    for item in week.get("instruments", []):
        side = str(item.get("direction") or "neutral")
        if side == "neutral":
            if sf(item.get("entry_price")) is not None:
                item["invalid_entry_audit"] = {"price": item.get("entry_price"), "captured_at": item.get("entry_captured_at"), "reason": "neutral_scenario_must_not_have_entry"}
                item["entry_price"] = None; item["entry_captured_at"] = None; item["entry_source"] = None
                changed = True
            item["entry_quality_status"] = "not_applicable_neutral"; item["trade_status"] = "no_trade"; item["result"] = "no_trade"; item["result_value"] = 0.0; item["result_percent"] = 0.0
            continue
        if now < target:
            item["entry_quality_status"] = "not_due"
            continue
        if entry_time_is_valid(item, target, latest):
            if not item.get("risk_plan") and str(week.get("method_version")) == MODEL_VERSION:
                item["risk_plan"] = build_risk_plan(item, float(item["entry_price"])); changed = True
            item["entry_quality_status"] = "valid_target_window"; item["trade_status"] = "open" if sf(item.get("exit_price")) is None else "closed"
            continue
        previous = None
        if sf(item.get("entry_price")) is not None:
            previous = {"price": item.get("entry_price"), "captured_at": item.get("entry_captured_at"), "source": item.get("entry_source"), "reason": "outside_monday_08_00_to_10_00_window"}
        point = first_bar_at_or_after(str(item.get("symbol") or ""), target)
        if point is None:
            item["entry_quality_status"] = "not_opened_target_price_unavailable"; item["trade_status"] = "not_opened"
            if previous:
                item["invalid_entry_audit"] = previous; item["entry_price"] = None; item["entry_captured_at"] = None; item["entry_source"] = None; changed = True
            continue
        if previous:
            item["invalid_entry_audit"] = previous
        item["entry_price"] = point["price"]; item["entry_captured_at"] = point["timestamp"]; item["entry_source"] = point["source"]
        item["entry_quality_status"] = "valid_reconstructed_target_bar"; item["trade_status"] = "open"
        if str(week.get("method_version")) == MODEL_VERSION:
            item["risk_plan"] = build_risk_plan(item, float(point["price"]))
        changed = True
    if changed:
        week["entry_audit_updated_at"] = now.isoformat(timespec="seconds")
        write_json(path, week)
    return changed


def set_result(item: Dict[str, Any], exit_price: float) -> None:
    entry = sf(item.get("entry_price"))
    if entry is None or entry == 0:
        return
    side = str(item.get("direction") or "neutral")
    if side not in {"long", "short"}:
        item.update({"result": "no_trade", "result_value": 0.0, "result_percent": 0.0}); return
    move = exit_price - entry if side == "long" else entry - exit_price
    pct = move / entry * 100.0
    if str(item.get("instrument_id")) == "eurusd":
        notional = sf(item.get("notional_eur")) or 10000.0; value = move * notional; units = move / 0.0001
    else:
        notional = sf(item.get("notional_usd")) or 10000.0; value = move / entry * notional; units = pct if str(item.get("instrument_id")) == "btcusd" else move
    item["result"] = "profit" if value > 0 else "loss" if value < 0 else "flat"
    item["result_value"] = round(value, 8); item["result_percent"] = round(pct, 4); item["result_units"] = round(units, 8); item["result_currency"] = "USD"


def _row_value(row: Any, name: str) -> Optional[float]:
    try:
        value = row[name]
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return sf(value)
    except Exception:
        return None


def review_open_positions(path: Optional[Path] = None) -> bool:
    path = path or current_week_path()
    week = read_json(path, {})
    if not week:
        return False
    now = legacy.now_local(); changed = False
    for item in week.get("instruments", []):
        if str(item.get("direction")) not in {"long", "short"} or sf(item.get("entry_price")) is None or sf(item.get("exit_price")) is not None:
            continue
        plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
        sl, tp = sf(plan.get("stop_loss_price")), sf(plan.get("take_profit_price"))
        if sl is None or tp is None:
            continue
        start = parse_dt(item.get("last_risk_review_at")) or parse_dt(item.get("entry_captured_at"))
        if start is None or start >= now:
            continue
        df = intraday_bars(str(item.get("symbol") or ""), start, now)
        if df is None:
            continue
        try:
            df = df[(df.index > start) & (df.index <= now)]
        except Exception:
            continue
        side = str(item.get("direction")); hit: Optional[Tuple[str, float, datetime]] = None
        for ts, row in df.iterrows():
            high, low = _row_value(row, "High"), _row_value(row, "Low")
            if high is None or low is None:
                continue
            sl_hit, tp_hit = ((low <= sl, high >= tp) if side == "long" else (high >= sl, low <= tp))
            if sl_hit and tp_hit:
                hit = ("stop_loss", sl, ts.to_pydatetime().astimezone(TZ)); break
            if sl_hit:
                hit = ("stop_loss", sl, ts.to_pydatetime().astimezone(TZ)); break
            if tp_hit:
                hit = ("take_profit", tp, ts.to_pydatetime().astimezone(TZ)); break
        item["last_risk_review_at"] = now.isoformat(timespec="seconds"); changed = True
        if hit:
            reason, level, ts = hit
            item["exit_price"] = level; item["exit_captured_at"] = ts.isoformat(timespec="seconds"); item["exit_source"] = f"Yahoo Finance:{item.get('symbol')}:5m:OHLC_threshold"
            item["exit_reason"] = reason; item["exit_execution_model"] = "planned_level_first_intraday_bar_conservative"; item["risk_status"] = "stop_loss_hit" if reason == "stop_loss" else "take_profit_hit"; item["trade_status"] = "closed"
            set_result(item, level)
    if changed:
        write_json(path, week)
    return changed


def close_due_weeks() -> bool:
    now = legacy.now_local(); changed_any = False
    for path in sorted(WEEKLY_DIR.glob("*.json"))[-8:]:
        week = read_json(path, {}); target = parse_dt((week.get("market_window") or {}).get("exit_target_local"))
        if target is None or now < target:
            continue
        changed = False
        for item in week.get("instruments", []):
            side = str(item.get("direction") or "neutral")
            if side == "neutral":
                if item.get("result") != "no_trade":
                    item.update({"result": "no_trade", "result_value": 0.0, "result_percent": 0.0, "trade_status": "no_trade"}); changed = True
                continue
            if sf(item.get("entry_price")) is None or sf(item.get("exit_price")) is not None:
                continue
            point = first_bar_at_or_after(str(item.get("symbol") or ""), target)
            if point is None:
                item["close_quality_status"] = "target_close_price_unavailable_no_live_fallback"; changed = True; continue
            item["exit_price"] = point["price"]; item["exit_captured_at"] = point["timestamp"]; item["exit_source"] = point["source"]
            item["exit_reason"] = "scheduled_week_close"; item["exit_execution_model"] = "first_5m_bar_at_or_after_frozen_deadline"; item["close_quality_status"] = "valid_target_bar"; item["trade_status"] = "closed"
            set_result(item, float(point["price"])); changed = True
        if changed:
            week["weekly_close_audit"] = {"status": "applied_without_current_price_fallback", "checked_at": now.isoformat(timespec="seconds"), "target": target.isoformat(timespec="seconds")}
            write_json(path, week); changed_any = True
    return changed_any


def auto_mode() -> None:
    legacy.capture_live_prices()
    if legacy.now_local().weekday() == 6:
        make_forecast()
    capture_entries(); review_open_positions(); close_due_weeks()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "forecast", "open", "close", "render"], default="auto")
    args = parser.parse_args()
    if args.mode == "forecast":
        make_forecast(); legacy.capture_live_prices()
    elif args.mode == "open":
        legacy.capture_live_prices(); capture_entries(); review_open_positions()
    elif args.mode == "close":
        legacy.capture_live_prices(); review_open_positions(); close_due_weeks()
    elif args.mode == "render":
        legacy.capture_live_prices()
    else:
        auto_mode()


if __name__ == "__main__":
    main()
