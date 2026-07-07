#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
WEEKLY = ROOT / "scripts" / "investments_weekly.py"
THRESHOLDS = ROOT / "scripts" / "investments_thresholds.py"
MONITOR = ROOT / "scripts" / "investments_monitor.py"
RISK = ROOT / "scripts" / "investments_risk_learning.py"


def write_text(path: Path, text: str) -> None:
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="\n")


def update_methodology() -> None:
    data = json.loads(METHOD.read_text(encoding="utf-8"))
    data["method_version"] = "1.4.0"
    data["language_note"] = (
        "This file defines the rule-based method used for weekly educational market scenarios in the Investing room. "
        "Version 1.4.0 adds instrument-specific entry thresholds, neutral/no-trade for weak signals and consistent volatility units. "
        "Do not rewrite past forecasts after results are known."
    )
    data["decision_rules"] = {
        "neutral_label": "neutral/no scenario",
        "max_score_abs": 100,
        "entry_thresholds": {
            "eurusd": {
                "long": 25,
                "short": -25,
                "rule": "Open a weekly EUR/USD scenario only when score is >= +25 or <= -25; otherwise neutral/no-trade."
            },
            "sp500_futures": {
                "long": 25,
                "short": -35,
                "rule": "S&P 500 has an upward structural bias; weak shorts are ignored unless score <= -35."
            },
            "btcusd": {
                "long": 35,
                "short": -35,
                "rule": "BTC/USD requires a stronger signal because volatility and regime shifts are higher."
            }
        }
    }
    model = data.setdefault("volatility_threshold_model", {})
    model.update({
        "enabled": True,
        "model": "instrument_specific_atr14_v1_4_0",
        "price_history_period": "6mo",
        "price_history_interval": "1d",
        "expected_week_move_formula": "ATR14 * sqrt(5)",
        "favorable_multiplier": 0.9,
        "adverse_multiplier": 0.6,
        "save_thresholds_at_first_active_review": True,
        "note": "v1.4.0: EUR/USD thresholds are in pips, S&P 500 futures thresholds are in points, BTC/USD thresholds are in percent."
    })
    model["instrument_volatility_units"] = {
        "eurusd": {"unit": "pips", "model": "ATR14_price_to_pips"},
        "sp500_futures": {"unit": "points", "model": "ATR14_points"},
        "btcusd": {"unit": "percent", "model": "ATR14_percent_of_last_close"}
    }
    model["unit_limits"] = {
        "eurusd": {
            "min_favorable_units": 45,
            "max_favorable_units": 140,
            "min_adverse_units": 30,
            "max_adverse_units": 95
        },
        "sp500_futures": {
            "min_favorable_units": 80,
            "max_favorable_units": 240,
            "min_adverse_units": 50,
            "max_adverse_units": 160
        },
        "btcusd": {
            "min_favorable_units": 6.0,
            "max_favorable_units": 9.0,
            "min_adverse_units": 3.5,
            "max_adverse_units": 5.0
        }
    }
    model["fallback_static_thresholds"] = {
        "eurusd": {"favorable_units": 90, "adverse_units": 60},
        "sp500_futures": {"favorable_units": 140, "adverse_units": 90},
        "btcusd": {"favorable_units": 7.0, "adverse_units": 4.0}
    }
    write_text(METHOD, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def update_weekly() -> None:
    text = WEEKLY.read_text(encoding="utf-8")
    if "def entry_thresholds(" not in text:
        helper = '''\n\ndef entry_thresholds(inst_id: str, method: Dict[str, Any]) -> Tuple[int, int]:\n    rules = method.get("decision_rules", {}) if isinstance(method, dict) else {}\n    per_inst = (rules.get("entry_thresholds", {}) or {}).get(inst_id, {})\n    long_threshold = safe_float(per_inst.get("long"))\n    short_threshold = safe_float(per_inst.get("short"))\n    if long_threshold is None:\n        long_threshold = safe_float(rules.get("bullish_threshold")) or 25\n    if short_threshold is None:\n        raw = safe_float(rules.get("bearish_threshold"))\n        short_threshold = raw if raw is not None else -25\n    return int(long_threshold), int(short_threshold)\n\n\ndef direction_from_score(score: int, inst_id: str, method: Dict[str, Any]) -> str:\n    long_threshold, short_threshold = entry_thresholds(inst_id, method)\n    if score >= long_threshold:\n        return "long"\n    if score <= short_threshold:\n        return "short"\n    return "neutral"\n'''
        text = text.replace('''def clip(value: float, low: float, high: float) -> float:\n    return max(low, min(high, value))\n''', '''def clip(value: float, low: float, high: float) -> float:\n    return max(low, min(high, value))\n''' + helper)
    text = text.replace(
        'direction = "long" if score > 0 else "short" if score < 0 else "neutral"',
        'direction = direction_from_score(score, inst["id"], method)'
    )
    text = text.replace(
        '"Momentum: 5 dni {ret5:+.2f}%, 20 dni {ret20:+.2f}%.",\n            "Zmienność neutralna względem ostatnich tygodni.",',
        '"Momentum: 5 dni {ret5:+.2f}%, 20 dni {ret20:+.2f}%.",\n            f"Próg v1.4.0: long od {entry_thresholds(inst[\"id\"], method)[0]}, short od {entry_thresholds(inst[\"id\"], method)[1]}; słabszy sygnał = neutral/no-trade.",'
    )
    text = text.replace(
        '"Momentum: 5 days {ret5:+.2f}%, 20 days {ret20:+.2f}%.",\n            "Volatility is neutral versus recent weeks.",',
        '"Momentum: 5 days {ret5:+.2f}%, 20 days {ret20:+.2f}%.",\n            f"v1.4.0 threshold: long from {entry_thresholds(inst[\"id\"], method)[0]}, short from {entry_thresholds(inst[\"id\"], method)[1]}; weaker signal = neutral/no-trade.",'
    )
    write_text(WEEKLY, text)


def update_thresholds() -> None:
    text = THRESHOLDS.read_text(encoding="utf-8")
    new_func = r'''def calculate_dynamic_thresholds(inst: Dict[str, Any], method: Dict[str, Any]) -> Dict[str, Any]:
    model = method.get("volatility_threshold_model", {})
    inst_id = inst.get("id") or inst.get("instrument_id")
    unit_cfg = (model.get("instrument_volatility_units", {}) or {}).get(inst_id, {})
    unit_label = unit_cfg.get("unit") or inst.get("result_unit") or ("pips" if inst_id == "eurusd" else "points")
    unit_size = safe_float(inst.get("pip_size")) or safe_float(inst.get("point_size")) or 1.0
    fallback = model.get("fallback_static_thresholds", {}).get(inst_id, {})
    fallback_up = safe_float(fallback.get("favorable_units"))
    fallback_down = safe_float(fallback.get("adverse_units"))

    if not model.get("enabled") or yf is None:
        return {"source": "static_fallback", "model": "static_fallback", "favorable_units": fallback_up, "adverse_units": fallback_down, "unit": unit_label}

    period = str(model.get("price_history_period", "6mo"))
    interval = str(model.get("price_history_interval", "1d"))
    ohlc = download_ohlc_series(str(inst.get("symbol")), period=period, interval=interval)
    closes = ohlc.get("close", [])
    atr = average_true_range(ohlc.get("high", []), ohlc.get("low", []), closes, 14)
    if atr is None:
        return {"source": "static_fallback_no_atr", "model": "static_fallback", "favorable_units": fallback_up, "adverse_units": fallback_down, "unit": unit_label}

    if unit_label == "percent":
        last_close = closes[-1] if closes else None
        atr_units = (atr / last_close) * 100 if last_close else None
        if atr_units is None:
            return {"source": "static_fallback_no_percent_base", "model": "static_fallback", "favorable_units": fallback_up, "adverse_units": fallback_down, "unit": unit_label}
        expected_week_move_units = atr_units * math.sqrt(5)
        price_fields = {"atr14_percent": round(atr_units, 4), "expected_week_move_percent": round(expected_week_move_units, 4)}
    else:
        expected_week_move = atr * math.sqrt(5)
        expected_week_move_units = expected_week_move / unit_size
        price_fields = {"atr14_price": round(atr, 6), "expected_week_move_price": round(expected_week_move, 6)}

    up_multiplier = safe_float(model.get("favorable_multiplier")) or 0.9
    down_multiplier = safe_float(model.get("adverse_multiplier")) or 0.6
    raw_up_units = expected_week_move_units * up_multiplier
    raw_down_units = expected_week_move_units * down_multiplier

    limits = model.get("unit_limits", {}).get(inst_id, {})
    min_up = safe_float(limits.get("min_favorable_units")) or raw_up_units
    max_up = safe_float(limits.get("max_favorable_units")) or raw_up_units
    min_down = safe_float(limits.get("min_adverse_units")) or raw_down_units
    max_down = safe_float(limits.get("max_adverse_units")) or raw_down_units
    up_units = clip(raw_up_units, min_up, max_up)
    down_units = clip(raw_down_units, min_down, max_down)

    out = {
        "source": "dynamic_atr14_v1_4_0",
        "model": "ATR14",
        "period": period,
        "interval": interval,
        "formula": model.get("expected_week_move_formula", "ATR14 * sqrt(5)"),
        "favorable_multiplier": up_multiplier,
        "adverse_multiplier": down_multiplier,
        "favorable_units_raw": round(raw_up_units, 2),
        "adverse_units_raw": round(raw_down_units, 2),
        "favorable_units": round(up_units, 2),
        "adverse_units": round(down_units, 2),
        "unit": unit_label,
        "minmax_applied": bool(round(raw_up_units, 2) != round(up_units, 2) or round(raw_down_units, 2) != round(down_units, 2))
    }
    out.update(price_fields)
    return out
'''
    text = re.sub(r"def calculate_dynamic_thresholds[\s\S]*\Z", new_func, text)
    write_text(THRESHOLDS, text)


def update_monitor() -> None:
    text = MONITOR.read_text(encoding="utf-8")
    new_func = '''def scenario_units(item: Dict[str, Any], price: float, cfg: Dict[str, Any]) -> Optional[float]:\n    entry = safe_float(item.get("entry_price"))\n    direction = item.get("direction")\n    if entry is None or direction not in {"long", "short"}:\n        return None\n    inst_id = item.get("instrument_id") or cfg.get("id")\n    unit_name = ((item.get("scenario_thresholds") or {}).get("unit") or cfg.get("result_unit") or "")\n    raw = price - entry\n    if inst_id == "btcusd" or unit_name == "percent":\n        move = (raw / entry) * 100 if entry else 0.0\n        return move if direction == "long" else -move\n    unit = safe_float(cfg.get("pip_size")) or safe_float(cfg.get("point_size")) or 1.0\n    return (raw if direction == "long" else -raw) / unit\n'''
    text = re.sub(r"def scenario_units[\s\S]*?\n\ndef thresholds", new_func + "\n\ndef thresholds", text)
    write_text(MONITOR, text)


def update_risk() -> None:
    text = RISK.read_text(encoding="utf-8")
    if "def volatility_unit(" not in text:
        helper = '''\n\ndef volatility_unit(inst_id: str, method: Dict[str, Any]) -> str:\n    model = method.get("volatility_threshold_model", {}) if isinstance(method, dict) else {}\n    cfg = (model.get("instrument_volatility_units", {}) or {}).get(inst_id, {})\n    return str(cfg.get("unit") or ("percent" if inst_id == "btcusd" else "pips" if inst_id == "eurusd" else "points"))\n'''
        text = text.replace('''def notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, str]:\n    inst_id = str(item.get("instrument_id") or "")\n    if inst_id == "eurusd":\n        return base.safe_float(item.get("notional_eur")) or base.safe_float(cfg.get("notional_eur")) or 10_000.0, "EUR"\n    return base.safe_float(item.get("notional_usd")) or base.safe_float(cfg.get("notional_usd")) or 10_000.0, "USD"\n''', '''def notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, str]:\n    inst_id = str(item.get("instrument_id") or "")\n    if inst_id == "eurusd":\n        return base.safe_float(item.get("notional_eur")) or base.safe_float(cfg.get("notional_eur")) or 10_000.0, "EUR"\n    return base.safe_float(item.get("notional_usd")) or base.safe_float(cfg.get("notional_usd")) or 10_000.0, "USD"\n''' + helper)
    new_vol = '''def volatility_units(symbol: str, inst_id: str, cfg: Dict[str, Any], method: Dict[str, Any]) -> Tuple[float, float, str]:\n    model = method.get("volatility_threshold_model", {}) if isinstance(method, dict) else {}\n    closes = base.download_close_series(symbol, str(model.get("price_history_period") or "6mo"))\n    mode = volatility_unit(inst_id, method)\n    u_size = unit_size(inst_id, cfg)\n    if len(closes) >= 22:\n        if mode == "percent":\n            diffs = [abs(closes[i] / closes[i - 1] - 1.0) * 100 for i in range(1, len(closes)) if closes[i - 1]]\n        else:\n            diffs = [abs(closes[i] - closes[i - 1]) / u_size for i in range(1, len(closes))]\n        daily_units = sum(diffs[-20:]) / min(20, len(diffs))\n        expected_week = daily_units * math.sqrt(5)\n        fav_mult = base.safe_float(model.get("favorable_multiplier")) or 0.9\n        adv_mult = base.safe_float(model.get("adverse_multiplier")) or 0.6\n        fav, adv = clipped_units(inst_id, method, expected_week * fav_mult, expected_week * adv_mult)\n        return fav, adv, f"dynamic_close_to_close_volatility_20d_{mode}"\n    fallback = (model.get("fallback_static_thresholds", {}) or {}).get(inst_id, {})\n    fav, adv = clipped_units(inst_id, method, base.safe_float(fallback.get("favorable_units")) or 90, base.safe_float(fallback.get("adverse_units")) or 60)\n    return fav, adv, f"fallback_static_thresholds_{mode}"\n'''
    text = re.sub(r"def volatility_units[\s\S]*?\n\ndef risk_plan", new_vol + "\n\ndef risk_plan", text)
    text = text.replace(
        '''    tp_delta = tp_units * u_size\n    sl_delta = sl_units * u_size\n''',
        '''    mode = volatility_unit(inst_id, method)\n    if mode == "percent":\n        tp_delta = entry * tp_units / 100.0\n        sl_delta = entry * sl_units / 100.0\n    else:\n        tp_delta = tp_units * u_size\n        sl_delta = sl_units * u_size\n'''
    )
    text = text.replace(
        '''        "threshold_source": source,\n        "rule_pl": "SL i TP są liczone z ostatniej zmienności i ograniczone limitami metody; poziomy zapisujemy przy pozycji, żeby później porównać plan z wynikiem.",''',
        '''        "threshold_source": source,\n        "volatility_unit": volatility_unit(inst_id, method),\n        "rule_pl": "SL i TP są liczone z ostatniej zmienności i ograniczone limitami metody; dla BTC wartości są procentowe, dla EUR/USD w pipsach, dla S&P 500 w punktach.",'''
    )
    text = text.replace(
        '''        "rule_en": "SL and TP are calculated from recent volatility and clipped by method limits; levels are saved with the position so the plan can be reviewed against the outcome.",''',
        '''        "rule_en": "SL and TP are calculated from recent volatility and clipped by method limits; BTC uses percent, EUR/USD uses pips, S&P 500 uses points.",'''
    )
    write_text(RISK, text)


def main() -> None:
    update_methodology()
    update_weekly()
    update_thresholds()
    update_monitor()
    update_risk()
    print("Applied investments methodology v1.4.0")


if __name__ == "__main__":
    main()
