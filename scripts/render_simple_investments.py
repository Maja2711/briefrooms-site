#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
PL_PAGE = ROOT / "pl" / "inwestycje" / "prognozy-tygodniowe.html"
EN_PAGE = ROOT / "en" / "investing" / "weekly-forecasts.html"


def sf(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_week() -> Dict[str, Any]:
    files = sorted(WEEKLY_DIR.glob("*.json"), reverse=True)
    return load(files[0], {}) if files else {}


def fmt_price(x: Any, inst: str = "") -> str:
    v = sf(x)
    if v is None:
        return "—"
    if inst == "eurusd":
        return f"{v:.5f}"
    return f"{v:,.2f}".replace(",", " ")


def direction(item: Dict[str, Any]) -> str:
    raw = str(item.get("direction") or item.get("effective_direction") or "")
    if raw in {"long", "short"}:
        return raw
    score = sf(item.get("score"))
    return "short" if score is not None and score < 0 else "long"


def unit_label(inst: str, lang: str) -> str:
    if inst == "eurusd":
        return "pipsów" if lang == "pl" else "pips"
    if inst == "sp500_futures":
        return "pkt" if lang == "pl" else "pts"
    return "USD"


def calc_units(item: Dict[str, Any], exit_price: Optional[float]) -> Optional[float]:
    entry = sf(item.get("entry_price"))
    if entry is None or exit_price is None:
        return None
    side = direction(item)
    inst = str(item.get("instrument_id") or "")
    move = (exit_price - entry) if side == "long" else (entry - exit_price)
    if inst == "eurusd":
        return move / 0.0001
    if inst == "sp500_futures":
        return move
    return move


def calc_value(item: Dict[str, Any], exit_price: Optional[float]) -> Optional[float]:
    stored = sf(item.get("result_value")) if exit_price is not None else None
    if stored is not None:
        return stored
    entry = sf(item.get("entry_price"))
    if entry is None or exit_price is None:
        return None
    side = direction(item)
    inst = str(item.get("instrument_id") or "")
    move = (exit_price - entry) if side == "long" else (entry - exit_price)
    if inst == "eurusd":
        return move * (sf(item.get("notional_eur")) or 10000.0)
    return (move / entry) * (sf(item.get("notional_usd")) or 10000.0)


def result_text(item: Dict[str, Any], lang: str) -> Tuple[str, str]:
    inst = str(item.get("instrument_id") or "")
    close = sf(item.get("exit_price"))
    if close is None:
        return ("Tydzień trwa" if lang == "pl" else "Week in progress"), "neutral"
    value = calc_value(item, close)
    units = calc_units(item, close)
    entry = sf(item.get("entry_price"))
    pct = sf(item.get("result_percent"))
    if pct is None and entry:
        side = direction(item)
        move = (close - entry) if side == "long" else (entry - close)
        pct = move / entry * 100.0
    tone = "positive" if (value or 0) > 0 else "negative" if (value or 0) < 0 else "neutral"
    parts = []
    if value is not None:
        parts.append(f"{value:+.2f} USD")
    if units is not None:
        parts.append(f"{units:+.1f} {unit_label(inst, lang)}")
    if pct is not None:
        parts.append(f"{pct:+.2f}%")
    return " · ".join(parts) if parts else "—", tone


def status_text(item: Dict[str, Any], lang: str) -> str:
    reason = str(item.get("exit_reason") or "")
    if reason == "stop_loss":
        return "Stop loss osiągnięty" if lang == "pl" else "Stop loss reached"
    if reason == "take_profit":
        return "Take profit osiągnięty" if lang == "pl" else "Take profit reached"
    if sf(item.get("exit_price")) is not None:
        return "Zamknięta" if lang == "pl" else "Closed"
    if item.get("risk_status") == "pending_entry":
        return "Czekamy na otwarcie" if lang == "pl" else "Waiting for open"
    return "Tydzień trwa" if lang == "pl" else "Week in progress"


def metric(label: str, value: str, tone: str = "", big: bool = False) -> str:
    cls = "metric" + (" big" if big else "") + (f" {tone}" if tone else "")
    return f"<div class=\"{cls}\"><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>"


def card(item: Dict[str, Any], lang: str) -> str:
    inst = str(item.get("instrument_id") or "")
    label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("symbol") or inst)
    dirn = direction(item)
    dir_class = "positive" if dirn == "long" else "negative"
    tag = dirn.upper()
    close = sf(item.get("exit_price"))
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    now_price = sf(item.get("exit_observed_price")) or close or sf(item.get("entry_price"))
    current_label = "Cena teraz" if lang == "pl" else "Price now"
    open_label = "Cena otwarcia" if lang == "pl" else "Open"
    close_label = "Cena zamknięcia" if lang == "pl" else "Close"
    week_open = "Tydzień trwa" if lang == "pl" else "Week in progress"
    res, tone = result_text(item, lang)
    close_txt = fmt_price(close, inst) if close is not None else week_open
    notional = "10 000 EUR" if inst == "eurusd" and lang == "pl" else "10,000 EUR" if inst == "eurusd" else "10 000 USD" if lang == "pl" else "10,000 USD"
    body = "".join([
        metric(open_label, fmt_price(item.get("entry_price"), inst)),
        metric(close_label, close_txt),
        metric("Stop loss", fmt_price(plan.get("stop_loss_price"), inst)),
        metric("Take profit", fmt_price(plan.get("take_profit_price"), inst)),
        metric("Zysk / strata teraz" if lang == "pl" else "Profit / loss now", res, tone, True),
        metric("Wynik po zamknięciu" if lang == "pl" else "Final result", res, tone, True),
        metric("Nominał pozycji" if lang == "pl" else "Notional", notional),
        metric("Status", status_text(item, lang)),
    ])
    return f"""
<article class="position {dir_class}"><div class="head"><div><p>{'Pozycja edukacyjna' if lang == 'pl' else 'Educational position'}</p><h3>{html.escape(label)}</h3><small>Yahoo Finance · {html.escape(str(item.get('symbol') or ''))}</small></div><span class="tag {'long' if dirn == 'long' else 'short'}">{tag}</span></div><div class="price-now"><span class="label">{current_label}</span><strong>{html.escape(fmt_price(now_price, inst))}</strong></div><dl class="grid">{body}</dl></article>
"""


def render(lang: str) -> str:
    week = latest_week()
    items = week.get("instruments", []) if isinstance(week, dict) else []
    closed_items = [x for x in items if sf(x.get("exit_price")) is not None]
    values = [calc_value(x, sf(x.get("exit_price"))) for x in closed_items]
    values = [v for v in values if v is not None]
    total = sum(values)
    wins = sum(1 for v in values if v > 0)
    losses = sum(1 for v in values if v < 0)
    win_rate = wins / len(values) * 100 if values else 0
    total_text = f"{total:+.2f} USD"
    total_tone = "positive" if total > 0 else "negative" if total < 0 else "neutral"
    week_id = html.escape(str(week.get("week_id") or ""))
    start = html.escape(str(week.get("forecast_for_week_start") or ""))
    end = html.escape(str(week.get("forecast_for_week_end") or ""))
    if lang == "pl":
        title = "Otwarte pozycje tygodniowe"
        desc = "Prosty widok pozycji tygodniowych. Zaawansowane analizy działają wewnętrznie i nie są pokazywane na stronie publicznej. Historia publiczna startuje od bieżącego tygodnia."
        nav = '<a href="/pl/aktualnosci.html">Aktualności</a><a href="/pl/geopolityka.html">Geopolityka</a><a href="/pl/zdrowie.html">Zdrowie</a><a href="/pl/nauka.html">Nauka</a><a href="/pl/inwestycje.html">Inwestycje</a><a href="/pl/o-projekcie.html">O nas</a>'
        home = "/pl/"; switch = "/en/investing/weekly-forecasts.html"; switch_label = "EN"
        summary = "Podsumowanie bieżącego tygodnia"
        total_label = "Łączny wynik zamkniętych pozycji w tym tygodniu"
        wl = "Zyskowne / stratne pozycje"
        current = "Pozycje tygodnia"
        history = "Historia od bieżącego tygodnia"
        table_head = "<tr><th>Tydzień</th><th>Instrument</th><th>Pozycja</th><th>Otwarcie</th><th>Zamknięcie</th><th>Powód</th><th>Wynik</th></tr>"
        back = '<a class="back" href="/pl/inwestycje.html">← Wróć do pokoju Inwestycje</a>'
        legal = "Treści mają charakter edukacyjny i analityczny. To nie jest rekomendacja inwestycyjna ani porada finansowa."
    else:
        title = "Open weekly positions"
        desc = "Simple weekly-position view. Advanced analysis runs internally and is not shown on the public page. Public history starts from the current week."
        nav = '<a href="/en/news.html">News</a><a href="/en/geopolitics.html">Geopolitics</a><a href="/en/health.html">Health</a><a href="/en/science.html">Science</a><a href="/en/investing.html">Investing</a>'
        home = "/en/"; switch = "/pl/inwestycje/prognozy-tygodniowe.html"; switch_label = "PL"
        summary = "Current week summary"
        total_label = "Total closed-position result this week"
        wl = "Profitable / losing positions"
        current = "Weekly positions"
        history = "History from current week"
        table_head = "<tr><th>Week</th><th>Instrument</th><th>Position</th><th>Open</th><th>Close</th><th>Reason</th><th>Result</th></tr>"
        back = '<a class="back" href="/en/investing.html">← Back to Investing room</a>'
        legal = "Content is educational and analytical. It is not investment advice or financial advice."
    cards = "".join(card(x, lang) for x in items)
    rows = []
    for item in items:
        close = sf(item.get("exit_price"))
        res, tone = result_text(item, lang)
        inst = str(item.get("instrument_id") or "")
        label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("symbol") or inst)
        reason = str(item.get("exit_reason") or ("w trakcie" if lang == "pl" else "open"))
        rows.append(f"<tr><td>{week_id}</td><td>{html.escape(label)}</td><td>{direction(item).upper()}</td><td>{fmt_price(item.get('entry_price'), inst)}</td><td>{fmt_price(close, inst) if close is not None else ('w trakcie' if lang == 'pl' else 'open')}</td><td>{html.escape(reason)}</td><td class=\"{tone}\">{html.escape(res)}</td></tr>")
    css = """
:root{--bg:#050b12;--line:rgba(255,255,255,.13);--txt:#eef7ff;--muted:#9fb2c8;--cyan:#38d6c9;--green:#52e38b;--red:#ff4d6d;--amber:#ffbf3f;--glass:linear-gradient(180deg,rgba(255,255,255,.105),rgba(255,255,255,.04))}*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;color:var(--txt);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:radial-gradient(900px 520px at 12% -8%,rgba(56,214,201,.18),transparent 58%),linear-gradient(180deg,#050b12 0%,#071321 42%,#081523 100%);min-height:100vh}a{color:inherit;text-decoration:none}header,main{max-width:1180px;margin:0 auto;padding:0 24px}header{padding-top:24px}.top{display:flex;align-items:center;justify-content:space-between;gap:18px;padding-bottom:18px;border-bottom:1px solid var(--line)}.brand{display:flex;align-items:center;gap:16px}.logo{font-size:26px;font-weight:900;letter-spacing:-.035em}.trust-pill{font-size:11px;color:var(--cyan);border:1px solid rgba(56,214,201,.28);background:rgba(56,214,201,.08);padding:7px 12px;border-radius:999px}.nav{display:flex;gap:20px;color:#d9e7f5;font-size:14px}.icon-btn{border:1px solid var(--line);background:rgba(255,255,255,.05);border-radius:13px;min-height:38px;padding:8px 13px;font-weight:800}.hero{max-width:1180px;margin:28px auto 22px;padding:0 24px}.hero-card,.panel,.week{background:var(--glass);border:1px solid rgba(255,255,255,.12);border-radius:24px;box-shadow:0 22px 58px rgba(0,0,0,.26),inset 0 1px 0 rgba(255,255,255,.08);padding:22px}.hero-card{border-radius:30px;padding:28px}.pill{display:inline-flex;padding:8px 12px;border-radius:999px;background:rgba(56,214,201,.10);border:1px solid rgba(56,214,201,.28);color:#9ffff6;font-weight:900;font-size:12px}h1{font-size:clamp(2.1rem,5vw,4.1rem);letter-spacing:-.06em;line-height:1;margin:18px 0 12px;background:linear-gradient(90deg,#fff,#dff7ff 52%,#ffe38b);-webkit-background-clip:text;background-clip:text;color:transparent}.lead{color:#bfd0e0;line-height:1.55;max-width:880px;font-size:18px}.panel,.week{margin:20px 0}.summary-top{display:grid;grid-template-columns:1fr 260px;gap:18px}.summary-kpi,.position{background:rgba(3,10,18,.50);border:1px solid rgba(255,255,255,.12);border-radius:20px;padding:18px}.summary-kpi span,.label{display:block;color:#8fa4b8;font-size:.75rem;font-weight:900;text-transform:uppercase;letter-spacing:.05em}.summary-kpi strong{display:block;font-size:1.9rem;margin-top:6px}.negative{color:var(--red)}.positive{color:var(--green)}.neutral{color:var(--amber)}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.position.positive{border-top:6px solid var(--green)}.position.negative{border-top:6px solid var(--red)}.head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:16px}.head p{margin:0 0 5px;color:var(--cyan);font-size:.75rem;text-transform:uppercase;font-weight:900}.head h3{margin:0;font-size:1.35rem}.tag{border-radius:999px;padding:9px 12px;background:rgba(255,255,255,.06);font-weight:900}.tag.short{color:#ff8aa0}.tag.long{color:#9bffca}.price-now{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.10);border-radius:16px;padding:14px;margin-bottom:12px}.price-now strong{display:block;font-size:2rem;margin-top:4px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.10);border-radius:16px;padding:13px}.metric dd{margin:6px 0 0;font-weight:850;color:#f6fcff;overflow-wrap:anywhere}.metric.big{grid-column:span 2;box-shadow:inset 0 0 0 1px rgba(56,214,201,.22)}.metric.big dd{font-size:1.12rem;line-height:1.35}.history{margin-top:18px;border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:12px;background:rgba(255,255,255,.035)}.history summary{cursor:pointer;font-weight:900;color:#9ffff6}.history-scroll{overflow:auto;margin-top:12px}table{width:100%;border-collapse:collapse;min-width:780px}th,td{text-align:left;border-bottom:1px solid rgba(255,255,255,.10);padding:10px 8px;font-size:.92rem}th{color:#8fa4b8;text-transform:uppercase;font-size:.72rem}.legal{border-top:1px solid rgba(255,255,255,.10);margin-top:28px;padding-top:18px;color:#8fa4b8;line-height:1.5}.back{display:inline-block;margin:24px 0;color:#9ffff6}footer{text-align:center;color:#8fa4b8;padding:24px}@media(max-width:980px){.nav{display:none}.summary-top,.cards{grid-template-columns:1fr}.grid{grid-template-columns:1fr}.metric.big{grid-column:auto}}
"""
    return f"""<!doctype html>
<html lang=\"{lang}\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)} | BriefRooms</title><meta name=\"description\" content=\"{html.escape(desc)}\"><link rel=\"icon\" href=\"/assets/favicon.svg\"><style>{css}</style></head><body><header><div class=\"top\"><a class=\"brand\" href=\"{home}\"><span class=\"logo\">BRIEFROOMS</span><span class=\"trust-pill\">AI-assisted</span></a><nav class=\"nav\">{nav}</nav><a class=\"icon-btn\" href=\"{switch}\">{switch_label}</a></div></header><section class=\"hero\"><div class=\"hero-card\"><span class=\"pill\">EUR/USD · S&P 500 · BTC/USD</span><h1>{html.escape(title)}</h1><p class=\"lead\">{html.escape(desc)}</p></div></section><main><section class=\"panel\"><h2>{html.escape(summary)}</h2><div class=\"summary-top\"><div class=\"summary-kpi\"><span>{html.escape(total_label)}</span><strong class=\"{total_tone}\">{html.escape(total_text)}</strong></div><div class=\"summary-kpi\"><span>{html.escape(wl)}</span><strong>{wins} / {losses}</strong><small>{win_rate:.0f}%</small></div></div></section><section class=\"week\"><h2>{week_id}</h2><p>{start} — {end}</p><h3>{html.escape(current)}</h3><div class=\"cards\">{cards}</div></section><section class=\"panel\"><details class=\"history\" open><summary>{html.escape(history)}</summary><div class=\"history-scroll\"><table>{table_head}<tbody>{''.join(rows)}</tbody></table></div></details></section>{back}<p class=\"legal\">{html.escape(legal)}</p></main><footer>BriefRooms</footer></body></html>\n"""


def main() -> None:
    PL_PAGE.parent.mkdir(parents=True, exist_ok=True)
    EN_PAGE.parent.mkdir(parents=True, exist_ok=True)
    PL_PAGE.write_text(render("pl"), encoding="utf-8")
    EN_PAGE.write_text(render("en"), encoding="utf-8")
    print("Rendered simple investment pages from current week only")


if __name__ == "__main__":
    main()
