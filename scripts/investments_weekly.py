#!/usr/bin/env python3
"""Build weekly Investing forecasts for BriefRooms.

Modes:
- auto: choose action based on Europe/Warsaw day/time
- forecast: create the next weekly forecast file
- open: capture current-week entry prices
- close: capture current-week exit prices and calculate results
- render: rebuild PL/EN HTML pages from saved JSON files

The model rules live in data/investments/methodology.json so the method can be reviewed,
modified and versioned without rewriting old forecasts.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
except Exception:  # pragma: no cover - GitHub Action installs it
    yf = None

REPO = Path(__file__).resolve().parents[1]
METHOD_PATH = REPO / "data" / "investments" / "methodology.json"
WEEKLY_DIR = REPO / "data" / "investments" / "weekly"
PL_PAGE = REPO / "pl" / "inwestycje" / "prognozy-tygodniowe.html"
EN_PAGE = REPO / "en" / "investing" / "weekly-forecasts.html"
TZ = ZoneInfo("Europe/Warsaw")
CLOUDFLARE_WEB_ANALYTICS = (
    "<!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' "
    "data-cf-beacon='{\"token\": \"9adde99e330a4b0d991627986ac34246\"}'></script><!-- End Cloudflare Web Analytics -->"
)


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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inject_cloudflare_analytics(html_text: str) -> str:
    if "static.cloudflareinsights.com/beacon.min.js" in html_text:
        return html_text
    body_close = html_text.lower().rfind("</body>")
    if body_close != -1:
        return html_text[:body_close] + CLOUDFLARE_WEB_ANALYTICS + "\n" + html_text[body_close:]
    return html_text.rstrip() + "\n" + CLOUDFLARE_WEB_ANALYTICS + "\n</body>\n</html>"


def week_id_from_date(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def monday_for_week(dt: datetime) -> datetime:
    return dt - timedelta(days=dt.weekday())


def target_forecast_week(dt: datetime) -> Tuple[str, datetime, datetime]:
    """Return ISO week id, Monday and Friday for the next tradable week."""
    days_until_monday = (7 - dt.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    monday = (dt + timedelta(days=days_until_monday)).replace(hour=8, minute=0, second=0, microsecond=0)
    friday = (monday + timedelta(days=4)).replace(hour=22, minute=0, second=0, microsecond=0)
    return week_id_from_date(monday), monday, friday


def current_week_file(dt: datetime) -> Path:
    return WEEKLY_DIR / f"{week_id_from_date(dt)}.json"


def forecast_week_file(dt: datetime) -> Path:
    week_id, _, _ = target_forecast_week(dt)
    return WEEKLY_DIR / f"{week_id}.json"


def safe_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
        if math.isfinite(f):
            return f
    except Exception:
        return None
    return None


def download_close_series(symbol: str, period: str = "6mo", interval: str = "1d") -> List[float]:
    if yf is None:
        return []
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return []
        # yfinance can return either single-level or multi-level columns.
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            if "Close" in df.columns.get_level_values(0):
                close_obj = df["Close"]
                if hasattr(close_obj, "columns"):
                    close = close_obj.iloc[:, 0]
                else:
                    close = close_obj
            else:
                return []
        else:
            if "Close" not in df.columns:
                return []
            close = df["Close"]
        values = [safe_float(x) for x in close.dropna().tolist()]
        return [x for x in values if x is not None]
    except Exception:
        return []


def get_current_price(symbol: str) -> PricePoint:
    stamp = now_local().isoformat(timespec="seconds")
    if yf is None:
        return PricePoint(None, stamp, "yfinance", "yfinance unavailable")
    # Prefer a recent intraday quote; fall back to latest daily close.
    for period, interval in [("5d", "5m"), ("1mo", "1d")]:
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
            if df is None or df.empty:
                continue
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                close_obj = df["Close"] if "Close" in df.columns.get_level_values(0) else None
                if close_obj is None:
                    continue
                close = close_obj.iloc[:, 0] if hasattr(close_obj, "columns") else close_obj
            else:
                if "Close" not in df.columns:
                    continue
                close = df["Close"]
            close = close.dropna()
            if not close.empty:
                return PricePoint(safe_float(close.iloc[-1]), stamp, f"yfinance:{symbol}:{period}:{interval}")
        except Exception as exc:
            last_error = str(exc)
    return PricePoint(None, stamp, "yfinance", locals().get("last_error", "no price"))


def ema(values: List[float], span: int) -> List[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def pct_change(values: List[float], periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] == 0:
        return 0.0
    return (values[-1] / values[-periods - 1] - 1.0) * 100.0


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def daily_returns(values: List[float]) -> List[float]:
    out: List[float] = []
    for a, b in zip(values, values[1:]):
        if a:
            out.append((b / a - 1.0) * 100.0)
    return out


def forecast_instrument(inst: Dict[str, Any], method: Dict[str, Any]) -> Dict[str, Any]:
    symbol = inst["symbol"]
    closes = download_close_series(symbol)
    if len(closes) < 55:
        return {
            "instrument_id": inst["id"],
            "symbol": symbol,
            "label_pl": inst["label_pl"],
            "label_en": inst["label_en"],
            "direction": "neutral",
            "score": 0,
            "confidence": 0,
            "signals": {},
            "rationale_pl": ["Brak wystarczających danych cenowych do wygenerowania scenariusza."],
            "rationale_en": ["Not enough price data to generate a scenario."],
            "entry_price": None,
            "entry_captured_at": None,
            "exit_price": None,
            "exit_captured_at": None,
            "result": None,
            "result_value": None,
            "result_percent": None,
        }

    last = closes[-1]
    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    ret5 = pct_change(closes, 5)
    ret20 = pct_change(closes, 20)
    rets = daily_returns(closes)
    vol20 = stdev(rets[-20:]) if len(rets) >= 20 else 0
    vol60 = stdev(rets[-60:]) if len(rets) >= 60 else vol20

    trend_score = 0
    trend_score += 15 if ema20 > ema50 else -15
    trend_score += 15 if last > ema20 else -15

    momentum_score = clip(ret5 * 3.0 + ret20 * 0.8, -25, 25)

    overrides = method.get("manual_overrides", {}).get(inst["id"], {})
    macro_bias = safe_float(overrides.get("macro_bias", 0)) or 0
    event_risk = safe_float(overrides.get("event_risk", 0)) or 0

    raw_score = trend_score + momentum_score + macro_bias + event_risk
    vol_multiplier = 1.0
    vol_note_pl = "Zmienność neutralna względem ostatnich tygodni."
    vol_note_en = "Volatility is neutral versus recent weeks."
    if vol60 and vol20 > vol60 * 1.25:
        vol_multiplier = 0.82
        vol_note_pl = "Podwyższona zmienność obniża pewność scenariusza."
        vol_note_en = "Elevated volatility reduces scenario confidence."
    elif vol60 and vol20 < vol60 * 0.75:
        vol_multiplier = 1.05
        vol_note_pl = "Niższa zmienność lekko podnosi wiarygodność sygnału trendowego."
        vol_note_en = "Lower volatility slightly supports the trend signal."

    score = int(round(clip(raw_score * vol_multiplier, -100, 100)))
    rules = method.get("decision_rules", {})
    bullish = int(rules.get("bullish_threshold", 20))
    bearish = int(rules.get("bearish_threshold", -20))
    if score >= bullish:
        direction = "long"
    elif score <= bearish:
        direction = "short"
    else:
        direction = "neutral"

    confidence = round(min(abs(score) / 100.0, 1.0), 2)

    direction_pl = {"long": "scenariusz wzrostowy", "short": "scenariusz spadkowy", "neutral": "neutralnie / bez transakcji"}[direction]
    direction_en = {"long": "bullish scenario", "short": "bearish scenario", "neutral": "neutral / no trade"}[direction]

    rationale_pl = [
        f"Model v{method.get('method_version')} wskazuje: {direction_pl}.",
        f"Trend: EMA20 {'powyżej' if ema20 > ema50 else 'poniżej'} EMA50; ostatnia cena {'powyżej' if last > ema20 else 'poniżej'} EMA20.",
        f"Momentum: 5 dni {ret5:+.2f}%, 20 dni {ret20:+.2f}%.",
        vol_note_pl,
        "To jest scenariusz edukacyjny, nie rekomendacja inwestycyjna.",
    ]
    rationale_en = [
        f"Model v{method.get('method_version')} indicates: {direction_en}.",
        f"Trend: EMA20 is {'above' if ema20 > ema50 else 'below'} EMA50; last price is {'above' if last > ema20 else 'below'} EMA20.",
        f"Momentum: 5 days {ret5:+.2f}%, 20 days {ret20:+.2f}%.",
        vol_note_en,
        "This is an educational scenario, not investment advice.",
    ]

    return {
        "instrument_id": inst["id"],
        "symbol": symbol,
        "label_pl": inst["label_pl"],
        "label_en": inst["label_en"],
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "signals": {
            "last_close": round(last, 6),
            "ema20": round(ema20, 6),
            "ema50": round(ema50, 6),
            "ret5_pct": round(ret5, 4),
            "ret20_pct": round(ret20, 4),
            "vol20_daily_pct": round(vol20, 4),
            "vol60_daily_pct": round(vol60, 4),
            "trend_score": round(trend_score, 2),
            "momentum_score": round(momentum_score, 2),
            "macro_bias": macro_bias,
            "event_risk": event_risk,
        },
        "rationale_pl": rationale_pl,
        "rationale_en": rationale_en,
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
    dt = now_local()
    week_id, monday, friday = target_forecast_week(dt)
    path = WEEKLY_DIR / f"{week_id}.json"
    existing = load_json(path, {})
    if existing.get("forecast_created_at"):
        return path

    instruments = [forecast_instrument(inst, method) for inst in method.get("instruments", [])]
    data = {
        "week_id": week_id,
        "method_version": method.get("method_version", "unknown"),
        "forecast_created_at": dt.isoformat(timespec="seconds"),
        "forecast_for_week_start": monday.date().isoformat(),
        "forecast_for_week_end": friday.date().isoformat(),
        "timezone": method.get("timezone", "Europe/Warsaw"),
        "market_window": {
            "entry_target_local": monday.isoformat(timespec="seconds"),
            "exit_target_local": friday.isoformat(timespec="seconds"),
        },
        "instruments": instruments,
    }
    write_json(path, data)
    return path


def calculate_result(item: Dict[str, Any], inst_cfg: Dict[str, Any]) -> None:
    entry = safe_float(item.get("entry_price"))
    exit_ = safe_float(item.get("exit_price"))
    direction = item.get("direction")
    if entry is None or exit_ is None:
        return
    if direction == "neutral":
        item["result"] = "no_trade"
        item["result_value"] = 0
        item["result_percent"] = 0
        return
    unit_size = safe_float(inst_cfg.get("pip_size")) or safe_float(inst_cfg.get("point_size")) or 1.0
    raw = exit_ - entry
    strategy_raw = raw if direction == "long" else -raw
    item["result_value"] = round(strategy_raw / unit_size, 2)
    item["result_percent"] = round((strategy_raw / entry) * 100.0, 4) if entry else None
    if abs(strategy_raw) < unit_size * 0.05:
        item["result"] = "flat"
    elif strategy_raw > 0:
        item["result"] = "profit"
    else:
        item["result"] = "loss"


def capture_prices(kind: str) -> Optional[Path]:
    assert kind in {"open", "close"}
    method = load_json(METHOD_PATH, {})
    dt = now_local()
    path = current_week_file(dt)
    data = load_json(path, {})
    if not data:
        # If no forecast exists, create a minimal neutral file so the audit trail is explicit.
        week_id = week_id_from_date(dt)
        monday = monday_for_week(dt).replace(hour=8, minute=0, second=0, microsecond=0)
        friday = (monday + timedelta(days=4)).replace(hour=22, minute=0, second=0, microsecond=0)
        data = {
            "week_id": week_id,
            "method_version": method.get("method_version", "unknown"),
            "forecast_created_at": None,
            "forecast_for_week_start": monday.date().isoformat(),
            "forecast_for_week_end": friday.date().isoformat(),
            "timezone": method.get("timezone", "Europe/Warsaw"),
            "market_window": {"entry_target_local": monday.isoformat(timespec="seconds"), "exit_target_local": friday.isoformat(timespec="seconds")},
            "instruments": [],
        }
        for inst in method.get("instruments", []):
            data["instruments"].append({
                "instrument_id": inst["id"],
                "symbol": inst["symbol"],
                "label_pl": inst["label_pl"],
                "label_en": inst["label_en"],
                "direction": "neutral",
                "score": 0,
                "confidence": 0,
                "signals": {},
                "rationale_pl": ["Brak prognozy niedzielnej — zapis techniczny ceny dla archiwum."],
                "rationale_en": ["No Sunday forecast — technical price capture for the archive."],
                "entry_price": None,
                "entry_captured_at": None,
                "exit_price": None,
                "exit_captured_at": None,
                "result": None,
                "result_value": None,
                "result_percent": None,
            })

    cfg_by_id = {x["id"]: x for x in method.get("instruments", [])}
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
            if pp.note:
                item["entry_note"] = pp.note
            changed = True
        elif kind == "close" and item.get("exit_price") is None:
            pp = get_current_price(symbol)
            item["exit_price"] = pp.price
            item["exit_captured_at"] = pp.timestamp
            item["exit_source"] = pp.source
            if pp.note:
                item["exit_note"] = pp.note
            calculate_result(item, cfg)
            changed = True
    if changed:
        write_json(path, data)
    return path


def format_price(value: Any) -> str:
    f = safe_float(value)
    if f is None:
        return "—"
    if abs(f) < 10:
        return f"{f:.5f}"
    return f"{f:,.2f}".replace(",", " ")


def direction_label(direction: str, lang: str) -> str:
    labels = {
        "pl": {"long": "Long / wzrost", "short": "Short / spadek", "neutral": "Neutralnie / bez transakcji"},
        "en": {"long": "Long / bullish", "short": "Short / bearish", "neutral": "Neutral / no trade"},
    }
    return labels[lang].get(direction, direction)


def result_label(result: Optional[str], lang: str) -> str:
    labels = {
        "pl": {None: "w trakcie", "profit": "zysk", "loss": "strata", "flat": "płasko", "no_trade": "brak transakcji"},
        "en": {None: "open", "profit": "profit", "loss": "loss", "flat": "flat", "no_trade": "no trade"},
    }
    return labels[lang].get(result, str(result))


def load_weeklies() -> List[Dict[str, Any]]:
    if not WEEKLY_DIR.exists():
        return []
    items = []
    for path in sorted(WEEKLY_DIR.glob("*.json"), reverse=True):
        try:
            items.append(load_json(path, {}))
        except Exception:
            continue
    return items


def render_instrument_card(item: Dict[str, Any], lang: str) -> str:
    label = html.escape(item.get("label_pl" if lang == "pl" else "label_en", item.get("symbol", "")))
    direction = html.escape(direction_label(item.get("direction", "neutral"), lang))
    score = html.escape(str(item.get("score", "—")))
    conf = item.get("confidence")
    conf_txt = "—" if conf is None else f"{float(conf) * 100:.0f}%"
    result = html.escape(result_label(item.get("result"), lang))
    unit = "pips" if item.get("instrument_id") == "eurusd" else ("pkt" if lang == "pl" else "pts")
    res_val = item.get("result_value")
    res_txt = "—" if res_val is None else f"{res_val:+.2f} {unit}"
    pct = item.get("result_percent")
    pct_txt = "—" if pct is None else f"{pct:+.2f}%"
    rationale = item.get("rationale_pl" if lang == "pl" else "rationale_en", [])
    rationale_html = "".join(f"<li>{html.escape(str(x))}</li>" for x in rationale[:5])
    return f"""
        <article class=\"forecast-instrument\">
          <h3>{label}</h3>
          <dl class=\"metrics\">
            <div><dt>{'Kierunek' if lang == 'pl' else 'Direction'}</dt><dd>{direction}</dd></div>
            <div><dt>Score</dt><dd>{score}</dd></div>
            <div><dt>{'Pewność' if lang == 'pl' else 'Confidence'}</dt><dd>{html.escape(conf_txt)}</dd></div>
            <div><dt>{'Otwarcie' if lang == 'pl' else 'Entry'}</dt><dd>{format_price(item.get('entry_price'))}</dd></div>
            <div><dt>{'Zamknięcie' if lang == 'pl' else 'Exit'}</dt><dd>{format_price(item.get('exit_price'))}</dd></div>
            <div><dt>{'Wynik' if lang == 'pl' else 'Result'}</dt><dd>{result} / {html.escape(res_txt)} / {html.escape(pct_txt)}</dd></div>
          </dl>
          <ul class=\"rationale\">{rationale_html}</ul>
        </article>
    """


def render_page(lang: str) -> str:
    assert lang in {"pl", "en"}
    weeks = load_weeklies()
    title = "Tygodniowe prognozy — EUR/USD i S&P 500 futures" if lang == "pl" else "Weekly forecasts — EUR/USD and S&P 500 futures"
    desc = (
        "Co niedzielę scenariusz na tydzień, cena otwarcia z poniedziałku rano i rozliczenie po piątkowym zamknięciu."
        if lang == "pl"
        else "A Sunday scenario for the week, Monday morning entry price and post-Friday-close result review."
    )
    canonical = "https://briefrooms.com/pl/inwestycje/prognozy-tygodniowe.html" if lang == "pl" else "https://briefrooms.com/en/investing/weekly-forecasts.html"
    home_link = "/pl/inwestycje.html" if lang == "pl" else "/en/investing.html"
    home_text = "Wróć do Inwestycji" if lang == "pl" else "Back to Investing"
    updated = now_local().strftime("%Y-%m-%d %H:%M %Z")

    if weeks:
        sections = []
        for week in weeks[:20]:
            week_id = html.escape(week.get("week_id", ""))
            start = html.escape(str(week.get("forecast_for_week_start", "")))
            end = html.escape(str(week.get("forecast_for_week_end", "")))
            created = html.escape(str(week.get("forecast_created_at") or "—"))
            method = html.escape(str(week.get("method_version", "—")))
            instruments = "".join(render_instrument_card(x, lang) for x in week.get("instruments", []))
            sections.append(f"""
      <section class=\"week-card\">
        <div class=\"week-head\">
          <h2>{week_id}</h2>
          <p>{'Tydzień' if lang == 'pl' else 'Week'}: {start} — {end}</p>
          <p>{'Prognoza utworzona' if lang == 'pl' else 'Forecast created'}: {created} · {'metoda' if lang == 'pl' else 'method'} v{method}</p>
        </div>
        {instruments}
      </section>
            """)
        body_sections = "\n".join(sections)
    else:
        body_sections = f"""
      <section class=\"week-card\">
        <h2>{'Brak zapisanych prognoz' if lang == 'pl' else 'No saved forecasts yet'}</h2>
        <p>{'Pierwsza automatyczna prognoza pojawi się po najbliższym niedzielnym uruchomieniu workflow.' if lang == 'pl' else 'The first automatic forecast will appear after the next Sunday workflow run.'}</p>
      </section>
        """

    disclaimer = (
        "To jest analiza edukacyjna i dziennik skuteczności modelu. Nie jest to rekomendacja inwestycyjna, porada finansowa ani oferta zawarcia transakcji."
        if lang == "pl"
        else "This is educational market analysis and a model-performance log. It is not investment advice, financial advice or an offer to trade."
    )
    methodology_text = (
        "Metoda jest zapisana w pliku data/investments/methodology.json i może być wersjonowana oraz ulepszana."
        if lang == "pl"
        else "The method is stored in data/investments/methodology.json and can be versioned and improved."
    )

    page = f"""<!doctype html>
<html lang=\"{lang}\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>{html.escape(title)} | BriefRooms</title>
  <meta name=\"description\" content=\"{html.escape(desc)}\" />
  <link rel=\"icon\" href=\"/assets/favicon.svg\" />
  <link rel=\"stylesheet\" href=\"/assets/site.css?v=rooms3\" />
  <link rel=\"canonical\" href=\"{canonical}\" />
  <style>
    body{{background:#f5eee5;color:#111827;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}}
    header,main{{max-width:1080px;margin:0 auto;padding:0 16px;}}
    header{{padding-top:32px;padding-bottom:12px;text-align:center;}}
    h1{{font-size:clamp(2rem,4vw,3rem);margin:.2rem 0 .6rem;}}
    .lead{{font-size:1.06rem;line-height:1.55;max-width:850px;margin:0 auto;color:#1f2937;}}
    .notice,.week-card{{background:#fff;border:1px solid rgba(15,23,42,.08);border-radius:18px;box-shadow:0 12px 30px rgba(15,23,42,.10);margin:16px 0;padding:18px 20px;}}
    .notice strong{{color:#92400e;}}
    .week-head{{border-bottom:1px solid #e5e7eb;margin-bottom:14px;padding-bottom:10px;}}
    .week-head h2{{margin:.1rem 0 .25rem;}}
    .week-head p{{margin:.2rem 0;color:#4b5563;}}
    .forecast-instrument{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:16px;margin:12px 0;padding:14px 16px;}}
    .forecast-instrument h3{{margin:.1rem 0 .7rem;color:#1d4ed8;}}
    .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 12px;}}
    .metrics div{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:10px;}}
    dt{{font-size:.78rem;color:#6b7280;text-transform:uppercase;letter-spacing:.04em;}}
    dd{{margin:4px 0 0;font-weight:700;color:#111827;}}
    .rationale{{margin:.4rem 0 0;padding-left:1.2rem;line-height:1.55;}}
    .back{{margin:24px 0 44px;}}
    a{{color:#1d4ed8;}}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <p class=\"lead\">{html.escape(desc)}</p>
  </header>
  <main>
    <section class=\"notice\">
      <p><strong>{'Uwaga' if lang == 'pl' else 'Note'}:</strong> {html.escape(disclaimer)}</p>
      <p>{html.escape(methodology_text)} <a href=\"/data/investments/methodology.json\">methodology.json</a> · <a href=\"/data/investments/method_changelog.md\">changelog</a></p>
      <p>{'Ostatnia aktualizacja strony' if lang == 'pl' else 'Page last updated'}: {html.escape(updated)}</p>
    </section>
{body_sections}
    <p class=\"back\">← <a href=\"{home_link}\">{html.escape(home_text)}</a></p>
  </main>
  <footer>© BriefRooms</footer>
</body>
</html>
"""
    return inject_cloudflare_analytics(page)


def render_pages() -> None:
    PL_PAGE.parent.mkdir(parents=True, exist_ok=True)
    EN_PAGE.parent.mkdir(parents=True, exist_ok=True)
    PL_PAGE.write_text(render_page("pl"), encoding="utf-8")
    EN_PAGE.write_text(render_page("en"), encoding="utf-8")


def auto_mode() -> None:
    dt = now_local()
    # Sunday: create next-week forecast.
    if dt.weekday() == 6:
        make_forecast()
    # Monday morning captures entry. Workflow runs twice around DST; first successful write wins.
    if dt.weekday() == 0:
        capture_prices("open")
    # Friday evening captures exit. Workflow runs twice around DST; first successful write wins.
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
