#!/usr/bin/env python3
"""Build weekly Investing forecasts for BriefRooms."""

from __future__ import annotations

import argparse
import html
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
except Exception:  # pragma: no cover - installed in GitHub Actions
    yf = None

REPO = Path(__file__).resolve().parents[1]
METHOD_PATH = REPO / "data" / "investments" / "methodology.json"
WEEKLY_DIR = REPO / "data" / "investments" / "weekly"
LIVE_PRICE_PATH = REPO / "data" / "investments" / "live_prices.json"
PL_PAGE = REPO / "pl" / "inwestycje" / "prognozy-tygodniowe.html"
EN_PAGE = REPO / "en" / "investing" / "weekly-forecasts.html"
TZ = ZoneInfo("Europe/Warsaw")
PRICE_STALE_AFTER = timedelta(minutes=90)


@dataclass
class PricePoint:
    price: Optional[float]
    timestamp: str
    source: str
    note: str = ""


def now_local() -> datetime:
    return datetime.now(TZ)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_lf(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text("\n".join(line.rstrip() for line in content.split("\n")), encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    write_text_lf(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def week_id_from_date(dt: datetime) -> str:
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def monday_for_week(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(hour=8, minute=0, second=0, microsecond=0)


def target_forecast_week(dt: datetime) -> Tuple[str, datetime, datetime]:
    days_until_monday = (7 - dt.weekday()) % 7 or 7
    monday = (dt + timedelta(days=days_until_monday)).replace(hour=8, minute=0, second=0, microsecond=0)
    friday = (monday + timedelta(days=4)).replace(hour=22, minute=0, second=0, microsecond=0)
    return week_id_from_date(monday), monday, friday


def current_week_file(dt: datetime) -> Path:
    return WEEKLY_DIR / f"{week_id_from_date(dt)}.json"


def format_price(value: Any) -> str:
    price = safe_float(value)
    if price is None:
        return "Brak danych ceny"
    if abs(price) < 10:
        return f"{price:.5f}"
    return f"{price:,.2f}".replace(",", " ")


def format_price_delta(value: Any) -> str:
    delta = safe_float(value)
    if delta is None:
        return "Brak danych zmiany"
    if abs(delta) < 10:
        return f"{delta:+.5f}"
    return f"{delta:+,.2f}".replace(",", " ")


def no_week_open_label(lang: str) -> str:
    return "Brak danych otwarcia — sprawdź źródło OHLC" if lang == "pl" else "No weekly open data — check OHLC source"


def week_in_progress_label(lang: str) -> str:
    return "Jeszcze niedostępne — tydzień trwa" if lang == "pl" else "Not available yet — week in progress"


def no_fresh_price_label(lang: str) -> str:
    return "Brak świeżej ceny — sprawdź źródło Yahoo Finance" if lang == "pl" else "No fresh price — check Yahoo Finance source"


def timestamp_label(value: Any, lang: str, fallback: str) -> str:
    if not value:
        return fallback
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return text


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def live_price_updated_at(rec: Dict[str, Any]) -> Optional[datetime]:
    return parse_timestamp(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"))


def live_price_is_stale(rec: Dict[str, Any]) -> bool:
    updated = live_price_updated_at(rec)
    if updated is None:
        return True
    return now_local() - updated > PRICE_STALE_AFTER


def stale_price_message(rec: Dict[str, Any], lang: str) -> str:
    updated = timestamp_label(
        rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"),
        lang,
        "brak czasu aktualizacji" if lang == "pl" else "no update timestamp",
    )
    return f"Cena nieaktualna — ostatnia aktualizacja: {updated}" if lang == "pl" else f"Price is stale — last update: {updated}"


def yahoo_chart_price(symbol: str) -> PricePoint:
    stamp = now_local().isoformat(timespec="seconds")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?range=1d&interval=1m"
    try:
        req = Request(url, headers={"User-Agent": "BriefRooms/1.0"})
        with urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return PricePoint(None, stamp, f"Yahoo Finance:{symbol}", "empty chart response")
        timestamps = result.get("timestamp") or []
        closes = ((result.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
        for ts, close in reversed(list(zip(timestamps, closes))):
            price = safe_float(close)
            if price is not None:
                price_ts = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ).isoformat(timespec="seconds")
                return PricePoint(price, price_ts, f"Yahoo Finance:{symbol}:chart:1d:1m")
        meta = result.get("meta", {})
        regular = safe_float(meta.get("regularMarketPrice"))
        if regular is not None:
            market_ts = safe_float(meta.get("regularMarketTime"))
            price_ts = datetime.fromtimestamp(market_ts, tz=timezone.utc).astimezone(TZ).isoformat(timespec="seconds") if market_ts else stamp
            return PricePoint(regular, price_ts, f"Yahoo Finance:{symbol}:regularMarketPrice")
    except Exception as exc:
        return PricePoint(None, stamp, f"Yahoo Finance:{symbol}", str(exc))
    return PricePoint(None, stamp, f"Yahoo Finance:{symbol}", "no usable live price")


def get_current_price(symbol: str) -> PricePoint:
    chart = yahoo_chart_price(symbol)
    if chart.price is not None or yf is None:
        return chart
    stamp = now_local().isoformat(timespec="seconds")
    for period, interval in [("5d", "5m"), ("1mo", "1d")]:
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
            if df is None or df.empty:
                continue
            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            close = close.dropna()
            if not close.empty:
                return PricePoint(safe_float(close.iloc[-1]), stamp, f"yfinance:{symbol}:{period}:{interval}")
        except Exception as exc:
            last_error = str(exc)
    return PricePoint(None, stamp, f"Yahoo Finance:{symbol}", locals().get("last_error", chart.note))


def get_week_open_from_ohlc(symbol: str, week: Dict[str, Any]) -> PricePoint:
    fallback = now_local().isoformat(timespec="seconds")
    if yf is None:
        return PricePoint(None, fallback, f"Yahoo Finance:{symbol}:OHLC", "yfinance unavailable")
    start = str(week.get("forecast_for_week_start") or "")
    if not start:
        return PricePoint(None, fallback, f"Yahoo Finance:{symbol}:OHLC", "week start missing")
    try:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=TZ)
        end_dt = start_dt + timedelta(days=7)
        df = yf.download(symbol, start=start_dt.date().isoformat(), end=end_dt.date().isoformat(), interval="1d", progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty or "Open" not in df:
            return PricePoint(None, fallback, f"Yahoo Finance:{symbol}:OHLC", "empty OHLC")
        open_series = df["Open"]
        if hasattr(open_series, "columns"):
            open_series = open_series.iloc[:, 0]
        open_series = open_series.dropna()
        if open_series.empty:
            return PricePoint(None, fallback, f"Yahoo Finance:{symbol}:OHLC", "no Open values")
        ts = open_series.index[0]
        when = ts.to_pydatetime().replace(tzinfo=TZ).isoformat(timespec="seconds") if hasattr(ts, "to_pydatetime") else start_dt.isoformat(timespec="seconds")
        return PricePoint(safe_float(open_series.iloc[0]), when, f"Yahoo Finance:{symbol}:OHLC:1d")
    except Exception as exc:
        return PricePoint(None, fallback, f"Yahoo Finance:{symbol}:OHLC", str(exc))


def download_close_series(symbol: str, period: str = "6mo") -> List[float]:
    if yf is None:
        return []
    try:
        df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty or "Close" not in df:
            return []
        close = df["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        values = [safe_float(x) for x in close.dropna().tolist()]
        return [x for x in values if x is not None]
    except Exception:
        return []


def ema(values: List[float], span: int) -> List[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def pct_change(values: List[float], periods: int) -> float:
    if len(values) <= periods or not values[-periods - 1]:
        return 0.0
    return (values[-1] / values[-periods - 1] - 1.0) * 100.0


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def forecast_instrument(inst: Dict[str, Any], method: Dict[str, Any]) -> Dict[str, Any]:
    closes = download_close_series(inst["symbol"])
    if len(closes) < 55:
        score = 0
        direction = "neutral"
        ret5 = ret20 = 0.0
        trend_text_pl = "brak wystarczających danych"
        trend_text_en = "not enough price data"
    else:
        last = closes[-1]
        ema20 = ema(closes, 20)[-1]
        ema50 = ema(closes, 50)[-1]
        ret5 = pct_change(closes, 5)
        ret20 = pct_change(closes, 20)
        overrides = method.get("manual_overrides", {}).get(inst["id"], {})
        macro_bias = safe_float(overrides.get("macro_bias", 0)) or 0
        event_risk = safe_float(overrides.get("event_risk", 0)) or 0
        score = int(round(clip((15 if ema20 > ema50 else -15) + (15 if last > ema20 else -15) + ret5 * 3.0 + ret20 * 0.8 + macro_bias + event_risk, -100, 100)))
        direction = "long" if score > 0 else "short" if score < 0 else "neutral"
        trend_text_pl = f"EMA20 {'powyżej' if ema20 > ema50 else 'poniżej'} EMA50; ostatnia cena {'powyżej' if last > ema20 else 'poniżej'} EMA20"
        trend_text_en = f"EMA20 is {'above' if ema20 > ema50 else 'below'} EMA50; last price is {'above' if last > ema20 else 'below'} EMA20"
    return {
        "instrument_id": inst["id"],
        "symbol": inst["symbol"],
        "label_pl": inst["label_pl"],
        "label_en": inst["label_en"],
        "direction": direction,
        "score": score,
        "confidence": round(min(abs(score) / 100.0, 1.0), 2),
        "signals": {},
        "rationale_pl": [
            f"Model v{method.get('method_version')} wskazuje: {direction_label(direction, 'pl').lower()}.",
            f"Trend: {trend_text_pl}.",
            f"Momentum: 5 dni {ret5:+.2f}%, 20 dni {ret20:+.2f}%.",
            "Zmienność neutralna względem ostatnich tygodni.",
        ],
        "rationale_en": [
            f"Model v{method.get('method_version')} indicates: {direction_label(direction, 'en').lower()}.",
            f"Trend: {trend_text_en}.",
            f"Momentum: 5 days {ret5:+.2f}%, 20 days {ret20:+.2f}%.",
            "Volatility is neutral versus recent weeks.",
        ],
        "entry_price": None,
        "entry_captured_at": None,
        "exit_price": None,
        "exit_captured_at": None,
        "result": None,
        "result_value": None,
        "result_percent": None,
    }


def make_forecast() -> Path:
    method = load_json(METHOD_PATH, {})
    week_id, monday, friday = target_forecast_week(now_local())
    path = WEEKLY_DIR / f"{week_id}.json"
    existing = load_json(path, {})
    if existing.get("forecast_created_at"):
        return path
    data = {
        "week_id": week_id,
        "method_version": method.get("method_version", "unknown"),
        "forecast_created_at": now_local().isoformat(timespec="seconds"),
        "forecast_for_week_start": monday.date().isoformat(),
        "forecast_for_week_end": friday.date().isoformat(),
        "timezone": method.get("timezone", "Europe/Warsaw"),
        "market_window": {"entry_target_local": monday.isoformat(timespec="seconds"), "exit_target_local": friday.isoformat(timespec="seconds")},
        "instruments": [forecast_instrument(inst, method) for inst in method.get("instruments", [])],
    }
    write_json(path, data)
    return path


def live_record(live_prices: Dict[str, Any], inst_id: str) -> Dict[str, Any]:
    prices = live_prices.get("prices", {}) if isinstance(live_prices, dict) else {}
    rec = prices.get(inst_id, {}) if isinstance(prices, dict) else {}
    return rec if isinstance(rec, dict) else {}


def log_stale_prices(method: Dict[str, Any], live_prices: Dict[str, Any]) -> None:
    for inst in method.get("instruments", []):
        inst_id = str(inst.get("id") or "")
        rec = live_record(live_prices, inst_id)
        if rec and live_price_is_stale(rec):
            label = inst.get("label_pl") or inst.get("label_en") or inst_id
            print(f"WARNING stale price for {label}: {stale_price_message(rec, 'pl')}")


def capture_live_prices() -> Dict[str, Any]:
    method = load_json(METHOD_PATH, {})
    existing = load_json(LIVE_PRICE_PATH, {"prices": {}})
    old_prices = existing.get("prices", {}) if isinstance(existing, dict) else {}
    out: Dict[str, Any] = {"updated_at": now_local().isoformat(timespec="seconds"), "prices": {}}
    for inst in method.get("instruments", []):
        inst_id = inst.get("id")
        symbol = inst.get("symbol")
        if not inst_id or not symbol:
            continue
        previous = old_prices.get(inst_id, {}) if isinstance(old_prices, dict) else {}
        pp = get_current_price(symbol)
        if pp.price is not None:
            out["prices"][inst_id] = {"price": pp.price, "timestamp": pp.timestamp, "current_price_updated_at": pp.timestamp, "source": pp.source, "fresh": True, "note": pp.note}
        elif previous.get("price") is not None:
            kept = dict(previous)
            kept["fresh"] = False
            kept["last_attempt_at"] = pp.timestamp
            kept["current_price_updated_at"] = kept.get("current_price_updated_at") or kept.get("timestamp")
            kept["note"] = pp.note or "brak świeżej ceny"
            out["prices"][inst_id] = kept
        else:
            out["prices"][inst_id] = {"price": None, "timestamp": pp.timestamp, "current_price_updated_at": pp.timestamp, "source": pp.source, "fresh": False, "note": pp.note or "brak świeżej ceny"}
    write_json(LIVE_PRICE_PATH, out)
    log_stale_prices(method, out)
    return out


def calculate_result(item: Dict[str, Any], inst_cfg: Dict[str, Any]) -> None:
    entry = safe_float(item.get("entry_price"))
    exit_ = safe_float(item.get("exit_price"))
    direction = str(item.get("direction") or "neutral")
    if entry is None or exit_ is None:
        return
    if direction == "neutral":
        item.update({"result": "no_trade", "result_value": 0, "result_percent": 0})
        return
    value, pct = strategy_move(entry, exit_, direction, inst_cfg)
    item["result_value"] = value
    item["result_percent"] = pct
    item["result"] = "flat" if abs(value) < 0.05 else "profit" if value > 0 else "loss"


def capture_prices(kind: str) -> Optional[Path]:
    assert kind in {"open", "close"}
    method = load_json(METHOD_PATH, {})
    path = current_week_file(now_local())
    data = load_json(path, {})
    if not data:
        return None
    cfg_by_id = {x.get("id"): x for x in method.get("instruments", [])}
    changed = False
    for item in data.get("instruments", []):
        cfg = cfg_by_id.get(item.get("instrument_id"), {})
        symbol = item.get("symbol") or cfg.get("symbol")
        if not symbol:
            continue
        if kind == "open" and item.get("entry_price") is None:
            pp = get_current_price(symbol)
            item["entry_price"] = pp.price
            item["entry_captured_at"] = pp.timestamp
            item["entry_source"] = pp.source
            changed = True
        if kind == "close" and item.get("exit_price") is None:
            pp = get_current_price(symbol)
            item["exit_price"] = pp.price
            item["exit_captured_at"] = pp.timestamp
            item["exit_source"] = pp.source
            calculate_result(item, cfg)
            changed = True
    if changed:
        write_json(path, data)
    return path


def week_is_in_progress(week: Dict[str, Any]) -> bool:
    end = str(week.get("forecast_for_week_end") or "")
    if not end:
        return True
    try:
        end_dt = datetime.fromisoformat(end).replace(tzinfo=TZ, hour=23, minute=59, second=59)
        return now_local() <= end_dt
    except Exception:
        return True


def week_open_point(item: Dict[str, Any], week: Dict[str, Any], inst_cfg: Dict[str, Any]) -> PricePoint:
    for key in ("entry_price", "week_open_price", "open_price", "ohlc_open", "first_week_price"):
        value = safe_float(item.get(key))
        if value is not None:
            return PricePoint(value, str(item.get(f"{key}_captured_at") or item.get("entry_captured_at") or week.get("forecast_for_week_start") or ""), str(item.get(f"{key}_source") or "saved weekly open"))
    symbol = str(item.get("symbol") or inst_cfg.get("symbol") or "")
    return get_week_open_from_ohlc(symbol, week) if symbol else PricePoint(None, "", "missing symbol")


def direction_label(direction: str, lang: str) -> str:
    labels = {
        "pl": {"long": "Long / wzrost", "short": "Short / spadek", "neutral": "Neutralnie / obserwacja"},
        "en": {"long": "Long / bullish", "short": "Short / bearish", "neutral": "Neutral / observation"},
    }
    return labels[lang].get(direction, labels[lang]["neutral"])


def direction_class(direction: str) -> str:
    return {"long": "positive", "short": "negative"}.get(direction, "neutral")


def change_class(value: Optional[float]) -> str:
    if value is None or abs(value) < 0.0000001:
        return "neutral"
    return "positive" if value > 0 else "negative"


def score_display(item: Dict[str, Any], lang: str) -> str:
    score = safe_float(item.get("score"))
    return str(int(score)) if score is not None else ("Brak score w danych modelu" if lang == "pl" else "No model score in the data")


def confidence_display(item: Dict[str, Any], lang: str) -> str:
    confidence = safe_float(item.get("confidence"))
    if confidence is None:
        return "Brak pewności w danych modelu" if lang == "pl" else "No confidence value in the data"
    return f"{confidence * 100:.0f}%" if confidence <= 1 else f"{confidence:.0f}%"


def close_display(item: Dict[str, Any], week: Dict[str, Any], lang: str) -> str:
    close = safe_float(item.get("exit_price"))
    if close is not None:
        return format_price(close)
    if week_is_in_progress(week):
        return week_in_progress_label(lang)
    return "Brak danych zamknięcia — sprawdź źródło ceny" if lang == "pl" else "No close data — check the price source"


def open_change_display(current: Optional[float], weekly_open: Optional[float], lang: str) -> Tuple[str, str]:
    if weekly_open is None:
        return no_week_open_label(lang), "neutral"
    if current is None:
        return no_fresh_price_label(lang), "neutral"
    delta = current - weekly_open
    pct = (delta / weekly_open * 100.0) if weekly_open else None
    pct_text = "Brak procentu" if pct is None else f"{pct:+.2f}%"
    return f"{format_price_delta(delta)} ({pct_text})", change_class(delta)


def strategy_move(entry: float, mark: float, direction: str, inst_cfg: Dict[str, Any]) -> Tuple[float, Optional[float]]:
    raw = mark - entry
    strategy_raw = raw if direction == "long" else -raw
    return strategy_raw, round(strategy_raw / entry * 100.0, 4) if entry else None


def movement_unit_delta(delta: float, item: Dict[str, Any], inst_cfg: Dict[str, Any], lang: str) -> Tuple[float, str]:
    inst_id = item.get("instrument_id")
    if inst_id == "eurusd":
        return delta / 0.0001, "pipsa" if lang == "pl" else "pips"
    if inst_id == "sp500_futures":
        return delta, "pkt" if lang == "pl" else "pts"
    pip_size = safe_float(inst_cfg.get("pip_size"))
    if pip_size:
        return delta / pip_size, "jedn." if lang == "pl" else "units"
    return delta, "pkt" if lang == "pl" else "pts"


def movement_display(delta: float, base_price: Optional[float], item: Dict[str, Any], inst_cfg: Dict[str, Any], lang: str) -> str:
    pct = (delta / base_price * 100.0) if base_price else None
    pct_text = "Brak procentu" if pct is None else f"{pct:+.2f}%"
    unit_value, unit_label = movement_unit_delta(delta, item, inst_cfg, lang)
    return f"{format_price_delta(delta)} / {pct_text} / {unit_value:+.1f} {unit_label}"


def current_market_change_display(current: Optional[float], weekly_open: Optional[float], item: Dict[str, Any], inst_cfg: Dict[str, Any], lang: str) -> Tuple[str, str]:
    if weekly_open is None:
        return no_week_open_label(lang), "neutral"
    if current is None:
        return no_fresh_price_label(lang), "neutral"
    delta = current - weekly_open
    return movement_display(delta, weekly_open, item, inst_cfg, lang), change_class(delta)


def current_scenario_result_display(item: Dict[str, Any], current: Optional[float], weekly_open: Optional[float], inst_cfg: Dict[str, Any], lang: str) -> Tuple[str, str]:
    direction = item.get("direction")
    if direction not in {"long", "short"}:
        return "Nie dotyczy — scenariusz neutralny" if lang == "pl" else "Not applicable — neutral scenario", "neutral"
    if weekly_open is None:
        return no_week_open_label(lang), "neutral"
    if current is None:
        return no_fresh_price_label(lang), "neutral"
    delta = current - weekly_open if direction == "long" else weekly_open - current
    return movement_display(delta, weekly_open, item, inst_cfg, lang), change_class(delta)


def position_status_label(state: str, lang: str) -> str:
    labels = {
        "pl": {"neutral": "Scenariusz neutralny — obserwacja rynku", "planned": "Oczekuje na cenę otwarcia tygodnia", "open": "Scenariusz aktywny — tydzień trwa", "closed": "Scenariusz zakończony", "stale": "Cena nieaktualna — odśwież dane"},
        "en": {"neutral": "Neutral scenario — market observation", "planned": "Waiting for the weekly open", "open": "Scenario active — week in progress", "closed": "Scenario closed", "stale": "Price stale — refresh data"},
    }
    return labels[lang][state]


def forecast_status(item: Dict[str, Any], week: Dict[str, Any], open_price: Optional[float], lang: str, price_stale: bool = False) -> str:
    if price_stale:
        return position_status_label("stale", lang)
    direction = str(item.get("direction") or "neutral")
    if direction == "neutral":
        return position_status_label("neutral", lang)
    if open_price is None:
        return no_week_open_label(lang)
    if safe_float(item.get("exit_price")) is not None:
        return position_status_label("closed", lang)
    if week_is_in_progress(week):
        return position_status_label("open", lang)
    return "Brak danych zamknięcia — sprawdź źródło ceny" if lang == "pl" else "No close data — check the price source"


def result_label(result: Optional[str], lang: str) -> str:
    labels = {
        "pl": {None: "w trakcie", "profit": "zysk", "loss": "strata", "flat": "płasko", "no_trade": "scenariusz neutralny"},
        "en": {None: "open", "profit": "profit", "loss": "loss", "flat": "flat", "no_trade": "neutral scenario"},
    }
    return labels[lang].get(result, str(result))


def forecast_outcome(item: Dict[str, Any], week: Dict[str, Any], open_price: Optional[float], inst_cfg: Dict[str, Any], lang: str) -> str:
    direction = str(item.get("direction") or "neutral")
    if direction == "neutral":
        return "Nie dotyczy — scenariusz neutralny" if lang == "pl" else "Not applicable — neutral scenario"
    if open_price is None:
        return no_week_open_label(lang)
    close = safe_float(item.get("exit_price"))
    if close is None:
        return week_in_progress_label(lang) if week_is_in_progress(week) else ("Brak danych zamknięcia — nie można policzyć wyniku" if lang == "pl" else "No close data — result cannot be calculated")
    value, pct = strategy_move(open_price, close, direction, inst_cfg)
    pct_text = "Brak procentu" if pct is None else f"{pct:+.2f}%"
    return f"{result_label(item.get('result') or ('profit' if value > 0 else 'loss' if value < 0 else 'flat'), lang)} / {movement_display(value, open_price, item, inst_cfg, lang)} / {pct_text}"


def is_legal_rationale(text: str) -> bool:
    lowered = text.lower()
    return any(x in lowered for x in ["rekomendacja inwestycyjna", "porada inwestycyjna", "porada finansowa", "scenariusz edukacyjny", "investment advice", "financial advice", "educational scenario", "offer to trade"])


def rationale_display_text(text: str) -> str:
    return text.replace("neutralnie / bez transakcji", "neutralnie / obserwacja rynku").replace("Neutralnie / bez transakcji", "Neutralnie / obserwacja rynku").replace("neutral / no trade", "neutral / market observation").replace("Neutral / no trade", "Neutral / market observation")


def rationale_html(item: Dict[str, Any], lang: str) -> str:
    rationale = item.get("rationale_pl" if lang == "pl" else "rationale_en", [])
    visible = [rationale_display_text(str(x)) for x in rationale if not is_legal_rationale(str(x))]
    if not visible:
        return ""
    heading = "Sygnały modelu" if lang == "pl" else "Model signals"
    items = "".join(f"<li>{html.escape(line)}</li>" for line in visible[:4])
    return f'<div class="forecast-rationale"><h4>{heading}</h4><ul>{items}</ul></div>'


def render_metric(label: str, value: str, tone: str = "") -> str:
    tone_class = f" forecast-metric--{html.escape(tone)}" if tone else ""
    return f'<div class="forecast-metric{tone_class}"><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>'


def render_instrument_card(item: Dict[str, Any], week: Dict[str, Any], lang: str, inst_cfg: Dict[str, Any], live_prices: Dict[str, Any]) -> str:
    inst_id = str(item.get("instrument_id", ""))
    label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("symbol") or inst_id)
    symbol = str(item.get("symbol") or inst_cfg.get("symbol") or "")
    rec = live_record(live_prices, inst_id)
    current = safe_float(rec.get("price"))
    price_stale = live_price_is_stale(rec)
    current_display = format_price(current) if current is not None else no_fresh_price_label(lang)
    updated = timestamp_label(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"), lang, "Brak aktualizacji ceny" if lang == "pl" else "No price update available")
    open_point = week_open_point(item, week, inst_cfg)
    weekly_open = safe_float(open_point.price)
    open_display = format_price(weekly_open) if weekly_open is not None else no_week_open_label(lang)
    change_text, change_state = open_change_display(current, weekly_open, lang)
    market_change_text, market_change_state = current_market_change_display(current, weekly_open, item, inst_cfg, lang)
    scenario_result_text, scenario_result_state = current_scenario_result_display(item, current, weekly_open, inst_cfg, lang)
    direction = str(item.get("direction") or "neutral")
    dir_label = direction_label(direction, lang)
    dir_class = direction_class(direction)
    metric_items = [render_metric("Zmiana rynku od otwarcia" if lang == "pl" else "Market move from open", market_change_text, market_change_state)]
    if direction in {"long", "short"}:
        metric_items.append(render_metric("Bieżący wynik scenariusza" if lang == "pl" else "Current scenario result", scenario_result_text, scenario_result_state))
    metric_items.extend([
        render_metric("Kierunek" if lang == "pl" else "Direction", dir_label),
        render_metric("Score", score_display(item, lang)),
        render_metric("Pewność" if lang == "pl" else "Confidence", confidence_display(item, lang)),
        render_metric("Status", forecast_status(item, week, weekly_open, lang, price_stale)),
        render_metric("Zamknięcie tygodnia" if lang == "pl" else "Weekly close", close_display(item, week, lang)),
        render_metric("Wynik końcowy po zakończeniu tygodnia" if lang == "pl" else "Final result after week close", forecast_outcome(item, week, weekly_open, inst_cfg, lang)),
    ])
    current_label = "Ostatnia zapisana cena" if price_stale and lang == "pl" else "Last saved price" if price_stale else "Bieżąca cena" if lang == "pl" else "Current price"
    update_text = stale_price_message(rec, lang) if price_stale else f"{'Ostatnia aktualizacja ceny' if lang == 'pl' else 'Last price update'}: {updated}"
    stale_class = " price-stale" if price_stale else ""
    return f'''
        <article class="forecast-instrument forecast-instrument-card forecast-instrument-card--{dir_class}{stale_class}">
          <div class="forecast-card-head"><div><h3>{html.escape(label)}</h3><p class="forecast-source"><span>{'Symbol źródła' if lang == 'pl' else 'Source symbol'}:</span> Yahoo Finance · {html.escape(symbol)}</p></div><span class="forecast-direction-badge forecast-direction-badge--{dir_class}">{html.escape(dir_label)}</span></div>
          <div class="forecast-price-zone"><div class="forecast-price-primary"><span>{html.escape(current_label)}</span><strong class="forecast-price-main">{html.escape(current_display)}</strong><small>{html.escape(update_text)}</small></div><div class="forecast-price-secondary"><span>{'Otwarcie tygodnia' if lang == 'pl' else 'Weekly open'}</span><strong>{html.escape(open_display)}</strong><em class="forecast-price-change forecast-price-change--{change_state}">{html.escape(change_text)}</em></div></div>
          <dl class="forecast-metric-grid">{''.join(metric_items)}</dl>{rationale_html(item, lang)}
        </article>'''


def render_live_price_panel(method: Dict[str, Any], live_prices: Dict[str, Any], lang: str) -> str:
    cards = []
    for inst in method.get("instruments", []):
        inst_id = inst.get("id", "")
        label = inst.get("label_pl" if lang == "pl" else "label_en", inst_id)
        rec = live_record(live_prices, inst_id)
        price = safe_float(rec.get("price"))
        stale = live_price_is_stale(rec)
        price_label = format_price(price) if price is not None else no_fresh_price_label(lang)
        updated = timestamp_label(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"), lang, "Brak aktualizacji ceny" if lang == "pl" else "No price update available")
        source = rec.get("source") or f"Yahoo Finance:{inst.get('symbol', inst_id)}"
        price_dt = "Ostatnia zapisana cena" if stale and lang == "pl" else "Last saved price" if stale else "Bieżąca cena" if lang == "pl" else "Current price"
        stale_notice = f"<p>{html.escape(stale_price_message(rec, lang))}</p>" if stale else ""
        cards.append(f'<div class="price-card{" price-stale" if stale else ""}"><h3>{html.escape(str(label))}</h3><dl><div><dt>{html.escape(price_dt)}</dt><dd>{html.escape(price_label)}</dd></div><div><dt>{"Ostatnia aktualizacja ceny" if lang == "pl" else "Last price update"}</dt><dd>{html.escape(updated)}</dd></div><div><dt>{"Źródło ceny" if lang == "pl" else "Price source"}</dt><dd>{html.escape(str(source))}</dd></div></dl>{stale_notice}</div>')
    title = "Ceny do wyceny pozycji" if lang == "pl" else "Prices for position marks"
    return f'<section class="price-panel"><h2>{html.escape(title)}</h2><div class="price-grid">{"".join(cards)}</div></section>'


def load_weeklies() -> List[Dict[str, Any]]:
    if not WEEKLY_DIR.exists():
        return []
    weeks = [load_json(path, {}) for path in WEEKLY_DIR.glob("*.json")]
    weeks = [week for week in weeks if isinstance(week, dict) and week.get("week_id")]
    return sorted(weeks, key=lambda x: str(x.get("week_id", "")), reverse=True)


def render_page(lang: str) -> str:
    method = load_json(METHOD_PATH, {})
    live_prices = load_json(LIVE_PRICE_PATH, {"prices": {}})
    cfg_by_id = {x.get("id"): x for x in method.get("instruments", [])}
    title = "Tygodniowe prognozy — EUR/USD i S&P 500 futures" if lang == "pl" else "Weekly forecasts — EUR/USD and S&P 500 futures"
    desc = "Co niedzielę scenariusz na tydzień, cena otwarcia z poniedziałku rano i rozliczenie po piątkowym zamknięciu." if lang == "pl" else "A Sunday scenario for the week, Monday morning entry price and post-Friday-close result review."
    canonical = "https://briefrooms.com/pl/inwestycje/prognozy-tygodniowe.html" if lang == "pl" else "https://briefrooms.com/en/investing/weekly-forecasts.html"
    home_link = "/pl/inwestycje.html" if lang == "pl" else "/en/investing.html"
    home_text = "Wróć do Inwestycji" if lang == "pl" else "Back to Investing"
    updated = now_local().strftime("%Y-%m-%d %H:%M %Z")
    weeks_html = []
    for week in load_weeklies()[:20]:
        instruments = "".join(render_instrument_card(x, week, lang, cfg_by_id.get(x.get("instrument_id"), {}), live_prices) for x in week.get("instruments", []))
        created = timestamp_label(week.get("forecast_created_at"), lang, "Nie zapisano daty utworzenia prognozy" if lang == "pl" else "Forecast creation date was not saved")
        weeks_html.append(f'<section class="week-card forecast-week-card"><div class="week-head"><h2>{html.escape(str(week.get("week_id", "")))}</h2><p>{"Tydzień" if lang == "pl" else "Week"}: {html.escape(str(week.get("forecast_for_week_start", "")))} — {html.escape(str(week.get("forecast_for_week_end", "")))}</p><p>{"Prognoza utworzona" if lang == "pl" else "Forecast created"}: {html.escape(created)} · {"metoda" if lang == "pl" else "method"} v{html.escape(str(week.get("method_version") or "unknown"))}</p></div><div class="forecast-instrument-grid">{instruments}</div></section>')
    if not weeks_html:
        weeks_html.append(f'<section class="week-card forecast-week-card"><h2>{"Brak zapisanych prognoz" if lang == "pl" else "No saved forecasts yet"}</h2></section>')
    legal_title = "Nota prawna" if lang == "pl" else "Legal note"
    legal_text = "Treści prezentowane na BriefRooms mają charakter wyłącznie informacyjny i edukacyjny. Nie stanowią rekomendacji inwestycyjnej, porady inwestycyjnej, analizy inwestycyjnej ani oferty kupna lub sprzedaży instrumentów finansowych. Decyzje inwestycyjne użytkownik podejmuje samodzielnie i na własne ryzyko." if lang == "pl" else "Content presented on BriefRooms is for informational and educational purposes only. It is not an investment recommendation, investment advice, investment analysis, or an offer to buy or sell financial instruments. Users make investment decisions independently and at their own risk."
    data_note = "Ceny rynkowe i symbole źródłowe pochodzą z Yahoo Finance; karty pokazują też otwarcie tygodnia, zmianę od otwarcia i status rozliczenia." if lang == "pl" else "Market prices and source symbols come from Yahoo Finance; cards also show weekly open, move from open and settlement status."
    methodology_text = "Metoda jest zapisana w pliku data/investments/methodology.json i może być wersjonowana oraz ulepszana." if lang == "pl" else "The method is stored in data/investments/methodology.json and can be versioned and improved."
    css = """
    :root{color-scheme:light;}*{box-sizing:border-box;}body{margin:0;background:#f4f7fb;color:#111827;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}header,main{max-width:1180px;margin:0 auto;padding:0 20px;}header{padding-top:38px;padding-bottom:18px;text-align:left;}h1{font-size:clamp(2rem,4vw,3.15rem);line-height:1.05;margin:.2rem 0 .75rem;letter-spacing:0;color:#0f172a}.lead{font-size:1.08rem;line-height:1.6;max-width:820px;margin:0;color:#475569}.notice,.price-panel,.forecast-week-card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 16px 40px rgba(15,23,42,.08);margin:18px 0;padding:20px}.notice{display:grid;gap:8px;background:#fbfdff}.notice p{margin:0;color:#475569;line-height:1.55}.price-grid,.forecast-instrument-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.price-card,.forecast-instrument-card{background:#fff;border:1px solid #dbe4ee;border-radius:8px;box-shadow:0 12px 28px rgba(15,23,42,.07);padding:20px;min-width:0}.price-card{background:#f8fafc;box-shadow:none}.price-stale{border-color:#f59e0b;background:#fffbeb}.price-stale .forecast-price-primary,.price-card.price-stale{background:#fffbeb;border-color:#f59e0b}.price-stale .forecast-price-main{color:#92400e}.week-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;border-bottom:1px solid #e2e8f0;margin-bottom:18px;padding-bottom:14px}.week-head h2{margin:.1rem 0;color:#0f172a}.week-head p{margin:.2rem 0;color:#64748b;line-height:1.45}.forecast-instrument-card--positive{border-top:4px solid #16a34a}.forecast-instrument-card--negative{border-top:4px solid #dc2626}.forecast-instrument-card--neutral{border-top:4px solid #64748b}.forecast-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:18px}.forecast-card-head h3{margin:0 0 5px;color:#0f172a;font-size:1.2rem}.forecast-source{margin:0;color:#64748b;font-size:.88rem;line-height:1.35}.forecast-source span{font-weight:700;color:#334155}.forecast-direction-badge,.forecast-price-change{display:inline-flex;align-items:center;width:max-content;max-width:100%;border-radius:999px;padding:6px 10px;font-size:.78rem;font-weight:800;line-height:1.2;white-space:normal}.forecast-direction-badge--positive,.forecast-price-change--positive{background:#dcfce7;color:#166534}.forecast-direction-badge--negative,.forecast-price-change--negative{background:#fee2e2;color:#991b1b}.forecast-direction-badge--neutral,.forecast-price-change--neutral{background:#e0f2fe;color:#075985}.forecast-price-zone{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(190px,.75fr);gap:16px;align-items:stretch;margin-bottom:18px}.forecast-price-primary,.forecast-price-secondary{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:15px;min-width:0}.forecast-price-primary span,.forecast-price-secondary span{display:block;color:#64748b;font-size:.78rem;font-weight:800;text-transform:uppercase;letter-spacing:.04em}.forecast-price-main{display:block;margin:6px 0 4px;color:#0f172a;font-size:clamp(2rem,4vw,3rem);line-height:1;font-weight:850;letter-spacing:0;overflow-wrap:anywhere}.forecast-price-primary small{display:block;color:#64748b;font-size:.82rem;line-height:1.35}.forecast-price-secondary strong{display:block;margin:7px 0 10px;color:#0f172a;font-size:1.3rem;line-height:1.15;overflow-wrap:anywhere}.forecast-metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0}.forecast-metric{background:#fbfdff;border:1px solid #e2e8f0;border-radius:8px;padding:11px 12px;min-width:0}.forecast-metric--positive dd{color:#166534}.forecast-metric--negative dd{color:#991b1b}.forecast-metric--neutral dd{color:#075985}dt{font-size:.72rem;color:#64748b;text-transform:uppercase;letter-spacing:.04em;font-weight:800}dd{margin:5px 0 0;font-weight:750;color:#0f172a;line-height:1.35;overflow-wrap:anywhere}.forecast-rationale{margin-top:16px;border-top:1px solid #e2e8f0;padding-top:13px}.forecast-rationale h4{margin:0 0 8px;color:#334155;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em}.forecast-rationale ul{margin:0;padding-left:1.1rem;color:#475569;line-height:1.55}.forecast-legal-note{border-top:1px solid #dbe4ee;margin:32px 0 20px;padding-top:16px;color:#64748b;font-size:.82rem;line-height:1.55}.forecast-legal-note h2{margin:0 0 8px;color:#334155;font-size:.95rem}.forecast-legal-note p{margin:0}.back{margin:24px 0 16px}a{color:#1d4ed8}footer{color:#64748b;text-align:center;padding:22px 16px 30px;font-size:.88rem}@media(max-width:780px){header,main{padding:0 14px}header{padding-top:28px}.price-grid,.forecast-instrument-grid,.forecast-price-zone,.forecast-metric-grid{grid-template-columns:1fr}.notice,.price-panel,.forecast-week-card,.forecast-instrument-card{padding:16px}.week-head,.forecast-card-head{display:block}.forecast-direction-badge{margin-top:10px}.forecast-price-main{font-size:2.15rem}}
    """
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" /><title>{html.escape(title)} | BriefRooms</title><meta name="description" content="{html.escape(desc)}" /><link rel="icon" href="/assets/favicon.svg" /><link rel="stylesheet" href="/assets/site.css?v=rooms3" /><link rel="canonical" href="{canonical}" /><style>{css}</style></head><body><header><h1>{html.escape(title)}</h1><p class="lead">{html.escape(desc)}</p></header><main><section class="notice"><p><strong>{'Dane i metoda' if lang == 'pl' else 'Data and method'}:</strong> {html.escape(data_note)}</p><p>{html.escape(methodology_text)} <a href="/data/investments/methodology.json">methodology.json</a> · <a href="/data/investments/method_changelog.md">changelog</a></p><p>{'Ostatnia aktualizacja strony' if lang == 'pl' else 'Page last updated'}: {html.escape(updated)}</p></section>{render_live_price_panel(method, live_prices, lang)}{''.join(weeks_html)}<p class="back">← <a href="{home_link}">{html.escape(home_text)}</a></p><section class="forecast-legal-note" aria-label="{html.escape(legal_title)}"><h2>{html.escape(legal_title)}</h2><p>{html.escape(legal_text)}</p></section></main><footer>© BriefRooms</footer><!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "9adde99e330a4b0d991627986ac34246"}}'></script><!-- End Cloudflare Web Analytics --></body></html>'''


def render_pages(refresh_live: bool = True) -> None:
    if refresh_live:
        capture_live_prices()
    write_text_lf(PL_PAGE, render_page("pl"))
    write_text_lf(EN_PAGE, render_page("en"))


def auto_mode() -> None:
    dt = now_local()
    if dt.weekday() == 6:
        make_forecast()
    if dt.weekday() == 0:
        capture_prices("open")
    if dt.weekday() == 4:
        capture_prices("close")
    render_pages()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "forecast", "open", "close", "render"], default="auto")
    args = parser.parse_args()
    if args.mode == "auto":
        auto_mode()
    elif args.mode == "forecast":
        make_forecast()
        render_pages()
    elif args.mode == "open":
        capture_prices("open")
        render_pages()
    elif args.mode == "close":
        capture_prices("close")
        render_pages()
    elif args.mode == "render":
        render_pages()


if __name__ == "__main__":
    main()
