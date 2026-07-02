#!/usr/bin/env python3
"""Friendly public view for BriefRooms Investing, with risk plan and lessons."""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

import investments_weekly as base
import investments_risk_learning as risk

EURUSD_NOTIONAL = 10_000.0
SP500_NOTIONAL = 10_000.0
BTC_NOTIONAL = 10_000.0


def effective_direction(item: Dict[str, Any]) -> str:
    return risk.direction(item)


def eurusd_notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    return base.safe_float(item.get("notional_eur")) or base.safe_float(cfg.get("notional_eur")) or EURUSD_NOTIONAL


def sp500_notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    return base.safe_float(item.get("notional_usd")) or base.safe_float(cfg.get("notional_usd")) or SP500_NOTIONAL


def btc_notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    return base.safe_float(item.get("notional_usd")) or base.safe_float(cfg.get("notional_usd")) or BTC_NOTIONAL


def money(value: Optional[float], currency: str = "USD") -> str:
    return "—" if value is None else f"{value:+,.2f} {currency}".replace(",", " ")


def trade_metrics(item: Dict[str, Any], mark: Optional[float], cfg: Dict[str, Any]) -> Dict[str, Any]:
    entry = base.safe_float(item.get("entry_price"))
    direction = effective_direction(item)
    if entry is None or mark is None:
        return {"available": False}
    move, pct = base.strategy_move(entry, mark, direction, cfg)
    inst_id = item.get("instrument_id")
    if inst_id == "eurusd":
        notional = eurusd_notional(item, cfg)
        return {"available": True, "value": move * notional, "currency": "USD", "units": move / 0.0001, "unit_pl": "pipsów", "unit_en": "pips", "pct": pct, "notional": notional, "notional_currency": "EUR"}
    if inst_id == "sp500_futures":
        notional = sp500_notional(item, cfg)
        return {"available": True, "value": ((pct or 0.0) / 100.0) * notional, "currency": "USD", "units": move, "unit_pl": "pkt", "unit_en": "pts", "pct": pct, "notional": notional, "notional_currency": "USD"}
    if inst_id == "btcusd":
        notional = btc_notional(item, cfg)
        return {"available": True, "value": ((pct or 0.0) / 100.0) * notional, "currency": "USD", "units": move, "unit_pl": "USD ruchu BTC", "unit_en": "BTC move USD", "pct": pct, "notional": notional, "notional_currency": "USD"}
    return {"available": True, "value": move, "currency": None, "units": move, "unit_pl": "pkt", "unit_en": "pts", "pct": pct, "notional": None, "notional_currency": None}


def calculate_result(item: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    res = trade_metrics(item, base.safe_float(item.get("exit_price")), cfg)
    if not res.get("available"):
        return
    value = base.safe_float(res.get("value"))
    item.update({"result_value": value, "result_percent": res.get("pct"), "result_units": res.get("units"), "result": "flat" if value is not None and abs(value) < 0.05 else "profit" if value is not None and value > 0 else "loss", "effective_direction": effective_direction(item)})
    if item.get("instrument_id") == "eurusd":
        item.update({"notional_eur": res.get("notional"), "result_currency": "USD"})
    if item.get("instrument_id") in {"sp500_futures", "btcusd"}:
        item.update({"notional_usd": res.get("notional"), "result_currency": "USD"})


def dir_text(item: Dict[str, Any], lang: str) -> str:
    direction = effective_direction(item)
    inst_id = item.get("instrument_id")
    if inst_id == "eurusd":
        labels = {"pl": {"long": "Kup EUR / sprzedaj USD", "short": "Sprzedaj EUR / kup USD"}, "en": {"long": "Buy EUR / sell USD", "short": "Sell EUR / buy USD"}}
    elif inst_id == "btcusd":
        labels = {"pl": {"long": "Kup BTC / sprzedaj USD", "short": "Sprzedaj BTC / kup USD"}, "en": {"long": "Buy BTC / sell USD", "short": "Sell BTC / buy USD"}}
    else:
        labels = {"pl": {"long": "Pozycja na wzrost", "short": "Pozycja na spadek"}, "en": {"long": "Upside position", "short": "Downside position"}}
    return labels[lang][direction]


def direction_label_short(item: Dict[str, Any], lang: str) -> str:
    return "LONG" if effective_direction(item) == "long" else "SHORT"


def tone(value: Optional[float]) -> str:
    return "neutral" if value is None or abs(value) < 0.000001 else "positive" if value > 0 else "negative"


def pnl_text(item: Dict[str, Any], mark: Optional[float], cfg: Dict[str, Any], lang: str) -> Tuple[str, str]:
    res = trade_metrics(item, mark, cfg)
    if not res.get("available"):
        return ("Czekamy na cenę otwarcia" if base.safe_float(item.get("entry_price")) is None and lang == "pl" else "Waiting for opening price" if base.safe_float(item.get("entry_price")) is None else "Brak świeżej ceny" if lang == "pl" else "No fresh price"), "neutral"
    units = base.safe_float(res.get("units")) or 0.0
    pct = base.safe_float(res.get("pct")) or 0.0
    unit = res.get("unit_pl" if lang == "pl" else "unit_en")
    value = base.safe_float(res.get("value"))
    if res.get("currency"):
        return f"{money(value, str(res.get('currency')))} · {units:+,.1f} {unit} · {pct:+.2f}%".replace(",", " "), tone(value)
    return f"{units:+.1f} {unit} · {pct:+.2f}%", tone(value)


def market_move(current: Optional[float], open_price: Optional[float], item: Dict[str, Any], lang: str) -> Tuple[str, str]:
    if open_price is None:
        return ("Czekamy na cenę otwarcia" if lang == "pl" else "Waiting for opening price"), "neutral"
    if current is None:
        return ("Brak świeżej ceny" if lang == "pl" else "No fresh price"), "neutral"
    delta = current - open_price
    pct = delta / open_price * 100 if open_price else 0.0
    if item.get("instrument_id") == "eurusd":
        text = f"{delta:+.5f} · {delta / 0.0001:+.1f} pipsów · {pct:+.2f}%" if lang == "pl" else f"{delta:+.5f} · {delta / 0.0001:+.1f} pips · {pct:+.2f}%"
    elif item.get("instrument_id") == "btcusd":
        text = f"{delta:+,.2f} USD · {pct:+.2f}%".replace(",", " ")
    else:
        text = f"{delta:+.2f} pkt · {pct:+.2f}%" if lang == "pl" else f"{delta:+.2f} pts · {pct:+.2f}%"
    return text, tone(delta)


def metric(label: str, value: str, state: str = "", big: bool = False) -> str:
    cls = "metric" + (f" {state}" if state else "") + (" big" if big else "")
    return f"<div class='{cls}'><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>"


def position_notional(item: Dict[str, Any], cfg: Dict[str, Any], lang: str) -> str:
    if item.get("instrument_id") == "eurusd":
        return f"{eurusd_notional(item, cfg):,.0f} EUR".replace(",", " ")
    if item.get("instrument_id") == "sp500_futures":
        return f"{sp500_notional(item, cfg):,.0f} USD".replace(",", " ")
    if item.get("instrument_id") == "btcusd":
        return f"{btc_notional(item, cfg):,.0f} USD".replace(",", " ")
    return "—"


def fmt_price(value: Any) -> str:
    return base.format_price(value) if base.safe_float(value) is not None else "—"


def risk_status_text(item: Dict[str, Any], lang: str) -> str:
    status = str(item.get("risk_status") or "open")
    labels = {
        "pl": {"pending_entry": "Czekamy na otwarcie", "open": "Otwarta — trwa analiza SL/TP", "take_profit_hit": "Take profit osiągnięty", "stop_loss_hit": "Stop loss osiągnięty", "closed_week": "Zamknięta tygodniowo"},
        "en": {"pending_entry": "Waiting for open", "open": "Open — SL/TP review active", "take_profit_hit": "Take profit reached", "stop_loss_hit": "Stop loss reached", "closed_week": "Closed at week end"},
    }
    return labels[lang].get(status, status)


def lesson_text(item: Dict[str, Any], lang: str) -> str:
    review = item.get("risk_review") if isinstance(item.get("risk_review"), dict) else {}
    txt = review.get("observation_pl" if lang == "pl" else "observation_en")
    if txt:
        return str(txt)
    return "Pozycja będzie oceniona przy kolejnej aktualizacji ceny." if lang == "pl" else "Position will be reviewed on the next price update."


def render_instrument_card(item: Dict[str, Any], week: Dict[str, Any], lang: str, cfg: Dict[str, Any], live_prices: Dict[str, Any]) -> str:
    inst_id = str(item.get("instrument_id") or "")
    label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("symbol") or inst_id)
    rec = base.live_record(live_prices, inst_id)
    current = base.safe_float(rec.get("price"))
    open_point = base.week_open_point(item, week, cfg)
    open_price = base.safe_float(open_point.price)
    close_price = base.safe_float(item.get("exit_price"))
    open_txt = fmt_price(open_price) if open_price is not None else ("Czekamy na otwarcie" if lang == "pl" else "Waiting for open")
    close_txt = fmt_price(close_price) if close_price is not None else ("Tydzień trwa — zamknięcie później" if base.week_is_in_progress(week) and lang == "pl" else "Week in progress — close later" if base.week_is_in_progress(week) else "Brak ceny zamknięcia" if lang == "pl" else "No close price")
    live_txt, live_state = pnl_text(item, current, cfg, lang)
    final_txt, final_state = pnl_text(item, close_price, cfg, lang) if close_price is not None else (close_txt, "neutral")
    move_txt, move_state = market_move(current, open_price, item, lang)
    nominal = position_notional(item, cfg, lang)
    direction = effective_direction(item)
    dir_class = {"long": "positive", "short": "negative"}.get(direction, "neutral")
    current_txt = fmt_price(current) if current is not None else ("Brak świeżej ceny" if lang == "pl" else "No fresh price")
    updated = base.timestamp_label(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"), lang, "brak aktualizacji" if lang == "pl" else "no update")
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    risk_usd = base.safe_float(plan.get("risk_usd"))
    reward_usd = base.safe_float(plan.get("potential_reward_usd"))
    rrr = base.safe_float(plan.get("reward_to_risk"))
    items = [
        metric("Cena otwarcia pozycji" if lang == "pl" else "Position opening price", open_txt),
        metric("Cena zamknięcia pozycji" if lang == "pl" else "Position closing price", close_txt),
        metric("Stop loss" if lang == "pl" else "Stop loss", fmt_price(plan.get("stop_loss_price"))),
        metric("Take profit" if lang == "pl" else "Take profit", fmt_price(plan.get("take_profit_price"))),
        metric("Ryzyko do SL" if lang == "pl" else "Risk to SL", money(risk_usd), "negative" if risk_usd else "neutral"),
        metric("Potencjał do TP" if lang == "pl" else "Potential to TP", money(reward_usd), "positive" if reward_usd else "neutral"),
        metric("Relacja zysk/ryzyko" if lang == "pl" else "Reward/risk", "—" if rrr is None else f"{rrr:.2f}"),
        metric("Zysk / strata teraz" if lang == "pl" else "Profit / loss now", live_txt, live_state, True),
        metric("Wynik po zamknięciu" if lang == "pl" else "Final result", final_txt, final_state, True),
        metric("Nominał pozycji" if lang == "pl" else "Position notional", nominal),
        metric("Ruch rynku od otwarcia" if lang == "pl" else "Market move from open", move_txt, move_state),
        metric("Status" if lang == "pl" else "Status", risk_status_text(item, lang)),
    ]
    return f"""
    <article class='instrument {dir_class}'><div class='head'><div><p>{'Pozycja edukacyjna' if lang == 'pl' else 'Educational position'}</p><h3>{html.escape(label)}</h3><small>Yahoo Finance · {html.escape(str(item.get('symbol') or cfg.get('symbol') or ''))}</small></div><b>{html.escape(direction_label_short(item, lang))}</b></div><div class='price'><div><span>{'Cena teraz' if lang == 'pl' else 'Price now'}</span><strong>{html.escape(current_txt)}</strong><small>{'Aktualizacja' if lang == 'pl' else 'Updated'}: {html.escape(updated)}</small></div><div><span>{'Start pozycji' if lang == 'pl' else 'Position start'}</span><strong>{html.escape(open_txt)}</strong><small>{'Zamknięcie' if lang == 'pl' else 'Close'}: {html.escape(close_txt)}</small></div></div><dl class='grid'>{''.join(items)}</dl><div class='lesson'><b>{'Wniosek z analizy' if lang == 'pl' else 'Review note'}</b><p>{html.escape(lesson_text(item, lang))}</p></div></article>"""


def render_live_price_panel(method: Dict[str, Any], live_prices: Dict[str, Any], lang: str) -> str:
    cards = []
    for inst in method.get("instruments", []):
        rec = base.live_record(live_prices, inst.get("id", ""))
        price = base.safe_float(rec.get("price"))
        label = inst.get("label_pl" if lang == "pl" else "label_en", inst.get("id", ""))
        cards.append(f"<div class='price-card'><h3>{html.escape(str(label))}</h3><p>{html.escape(base.format_price(price) if price is not None else ('Brak świeżej ceny' if lang == 'pl' else 'No fresh price'))}</p></div>")
    return f"<section class='panel live-panel'><div class='section-title'><h2>{'Aktualne ceny rynkowe' if lang == 'pl' else 'Current market prices'}</h2><p>{'Ceny używane do bieżącego wyniku pozycji.' if lang == 'pl' else 'Prices used for current position P/L.'}</p></div><div class='prices'>{''.join(cards)}</div></section>"


def closed_history_rows(weeks: List[Dict[str, Any]], cfg: Dict[str, Dict[str, Any]], lang: str) -> Tuple[str, float, int, int, int]:
    rows: List[str] = []
    total = 0.0
    wins = losses = closed = 0
    for week in weeks:
        week_id = str(week.get("week_id", ""))
        for item in week.get("instruments", []):
            inst_cfg = cfg.get(item.get("instrument_id"), {})
            close_price = base.safe_float(item.get("exit_price"))
            if close_price is None:
                result_text, state, value = ("w trakcie" if lang == "pl" else "open"), "neutral", None
            else:
                res = trade_metrics(item, close_price, inst_cfg)
                value = base.safe_float(res.get("value")) if res.get("available") else None
                state = tone(value)
                result_text = money(value, str(res.get("currency") or "USD")) if value is not None else "—"
                closed += 1
                if value is not None:
                    total += value
                    if value > 0:
                        wins += 1
                    elif value < 0:
                        losses += 1
            label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("instrument_id") or "")
            open_txt = fmt_price(item.get("entry_price")) if base.safe_float(item.get("entry_price")) is not None else "—"
            close_txt = fmt_price(close_price) if close_price is not None else ("w trakcie" if lang == "pl" else "open")
            reason = str(item.get("exit_reason") or ("zamknięcie tygodnia" if lang == "pl" else "week close"))
            rows.append(f"<tr><td>{html.escape(week_id)}</td><td>{html.escape(label)}</td><td>{html.escape(direction_label_short(item, lang))}</td><td>{html.escape(open_txt)}</td><td>{html.escape(close_txt)}</td><td>{html.escape(reason)}</td><td class='{state}'>{html.escape(result_text)}</td></tr>")
    return "".join(rows), total, wins, losses, closed


def render_learning_panel(lang: str) -> str:
    journal = base.load_json(risk.LESSONS_PATH, {"summary": {}, "records": []})
    rows = []
    for inst, data in (journal.get("summary", {}) or {}).items():
        label = inst.upper().replace("SP500_FUTURES", "S&P 500").replace("EURUSD", "EUR/USD").replace("BTCUSD", "BTC/USD")
        lesson = data.get("lesson_pl" if lang == "pl" else "lesson_en") or "—"
        rows.append(f"<div class='summary-card'><dt>{html.escape(label)}</dt><dd>{html.escape(str(data.get('win_rate', 0)))}%</dd><p>{html.escape(str(lesson))}</p></div>")
    if not rows:
        rows.append(f"<div class='summary-card'><dt>{'Wnioski' if lang == 'pl' else 'Lessons'}</dt><dd>—</dd><p>{'Wnioski pojawią się po zamknięciu pierwszych pozycji.' if lang == 'pl' else 'Lessons will appear after the first positions are closed.'}</p></div>")
    return f"<section class='summary panel'><h2>{'Wnioski modelu' if lang == 'pl' else 'Model lessons'}</h2><div class='summary-current'>{''.join(rows)}</div></section>"


def render_summary_panel(weeks: List[Dict[str, Any]], cfg: Dict[str, Dict[str, Any]], live_prices: Dict[str, Any], lang: str) -> str:
    history_rows, total, wins, losses, closed = closed_history_rows(weeks, cfg, lang)
    latest = weeks[0] if weeks else {}
    active_cards = []
    for item in latest.get("instruments", []):
        inst_cfg = cfg.get(item.get("instrument_id"), {})
        rec = base.live_record(live_prices, str(item.get("instrument_id") or ""))
        current = base.safe_float(rec.get("price"))
        txt, state = pnl_text(item, current, inst_cfg, lang)
        label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("instrument_id") or "")
        active_cards.append(f"<div class='summary-card'><dt>{html.escape(label)}</dt><dd class='{ 'positive' if effective_direction(item) == 'long' else 'negative' }'>{html.escape(direction_label_short(item, lang))}</dd><p class='{state}'>{html.escape(txt)}</p></div>")
    win_rate = (wins / closed * 100.0) if closed else 0.0
    table_head = "<tr><th>Tydzień</th><th>Instrument</th><th>Pozycja</th><th>Otwarcie</th><th>Zamknięcie</th><th>Powód</th><th>Wynik</th></tr>" if lang == "pl" else "<tr><th>Week</th><th>Instrument</th><th>Position</th><th>Open</th><th>Close</th><th>Reason</th><th>Result</th></tr>"
    return f"""
    <section class='summary panel'><h2>{'Podsumowanie' if lang == 'pl' else 'Summary'}</h2><div class='summary-top'><div class='summary-kpi'><span>{'Łączny wynik zamkniętych pozycji' if lang == 'pl' else 'Total closed-position result'}</span><strong class='{tone(total)}'>{html.escape(money(total, 'USD'))}</strong></div><div class='summary-kpi'><span>{'Zyskowne / stratne pozycje' if lang == 'pl' else 'Profitable / losing positions'}</span><strong>{wins} / {losses}</strong><small>{win_rate:.0f}% {'skuteczności' if lang == 'pl' else 'win rate'}</small></div></div><h3>{'Aktualne pozycje' if lang == 'pl' else 'Current positions'}</h3><div class='summary-current'>{''.join(active_cards)}</div><details class='history'><summary>{'Historia wszystkich pozycji' if lang == 'pl' else 'All position history'}</summary><div class='history-scroll'><table>{table_head}<tbody>{history_rows}</tbody></table></div></details></section>
    """


def css() -> str:
    return """
:root{--bg:#050b12;--line:rgba(255,255,255,.13);--txt:#eef7ff;--muted:#9fb2c8;--cyan:#38d6c9;--amber:#ffbf3f;--green:#52e38b;--red:#ff4d6d;--violet:#b46cff;--shadow:0 28px 90px rgba(0,0,0,.46);--glass:linear-gradient(180deg,rgba(255,255,255,.115),rgba(255,255,255,.045))}*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;color:var(--txt);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;min-height:100vh;overflow-x:hidden;background:radial-gradient(900px 520px at 12% -8%,rgba(56,214,201,.20),transparent 58%),radial-gradient(820px 540px at 82% 3%,rgba(255,191,63,.14),transparent 54%),linear-gradient(180deg,#050b12 0%,#071321 42%,#081523 100%)}a{color:inherit}header,main{max-width:1280px;margin:0 auto;padding:0 24px}header{padding-top:24px}.top{display:flex;align-items:center;justify-content:space-between;gap:18px;padding-bottom:18px;border-bottom:1px solid var(--line)}.brand{display:flex;align-items:center;gap:16px;text-decoration:none}.logo{font-size:26px;line-height:1;font-weight:900;letter-spacing:-.035em}.trust-pill{font-size:11px;color:var(--cyan);border:1px solid rgba(56,214,201,.28);background:rgba(56,214,201,.08);padding:7px 12px;border-radius:999px}.nav{display:flex;align-items:center;gap:20px;color:#d9e7f5;font-size:14px}.nav a{text-decoration:none;opacity:.88}.icon-btn{border:1px solid var(--line);background:rgba(255,255,255,.05);border-radius:13px;min-height:38px;padding:0 13px;display:inline-flex;align-items:center;text-decoration:none;font-weight:800}.hero{max-width:1280px;margin:28px auto 24px;padding:28px 24px}.hero-card,.panel,.week{background:var(--glass);border:1px solid rgba(255,255,255,.12);border-radius:24px;box-shadow:0 22px 58px rgba(0,0,0,.30),inset 0 1px 0 rgba(255,255,255,.08);padding:22px;backdrop-filter:blur(18px) saturate(145%)}.hero-card{border-radius:32px;padding:28px}.pill{display:inline-flex;padding:8px 12px;border-radius:999px;background:rgba(56,214,201,.10);border:1px solid rgba(56,214,201,.28);color:#9ffff6;font-weight:900;font-size:12px}h1{font-size:clamp(2.2rem,5vw,4.6rem);letter-spacing:-.065em;line-height:.98;margin:18px 0 12px;background:linear-gradient(90deg,#fff,#dff7ff 45%,#ffd66f 74%,#9bffca);-webkit-background-clip:text;background-clip:text;color:transparent}.lead{color:#bfd0e0;line-height:1.6;max-width:900px;font-size:18px}.panel,.week{margin:20px 0}.section-title{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:16px}.section-title h2,.summary h2,.week h2{margin:0;color:#f6fcff}.section-title p,.notice p,.week p{color:#9fb2c8;margin:6px 0 0}.prices,.cards,.summary-current{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.price-card,.instrument,.summary-card,.summary-kpi{background:rgba(3,10,18,.50);border:1px solid rgba(255,255,255,.12);border-radius:20px;padding:18px}.instrument.positive{border-top:6px solid var(--green)}.instrument.negative{border-top:6px solid var(--red)}.instrument.neutral{border-top:6px solid var(--amber)}.head,.price,.summary-top{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:start}.head p{margin:0 0 6px;color:var(--cyan);font-size:.75rem;text-transform:uppercase;font-weight:900}.head h3{margin:0;color:#f6fcff}.head small{color:#8ea3b8}.head b{border-radius:999px;padding:9px 12px;background:rgba(56,214,201,.10);color:#9ffff6}.negative .head b{background:rgba(255,77,109,.12);color:#ff8aa0}.price{grid-template-columns:1.2fr .8fr;margin:18px 0}.price>div,.metric,.lesson{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.10);border-radius:16px;padding:13px}.summary h3{margin:18px 0 10px;color:#f6fcff}.summary-kpi strong{display:block;font-size:1.65rem;margin-top:6px}.summary-card dt{color:#8fa4b8;font-size:.72rem;font-weight:900;text-transform:uppercase}.summary-card dd{margin:6px 0;font-weight:900}.summary-card p{font-weight:700;margin:8px 0 0;color:#bfd0e0;line-height:1.45}.price span,dt{display:block;color:#8fa4b8;font-size:.72rem;font-weight:900;text-transform:uppercase;letter-spacing:.05em}.price strong{display:block;font-size:clamp(1.5rem,3vw,2.7rem);margin:6px 0;color:#f6fcff}.price small{color:#9fb2c8}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0}.metric.big{box-shadow:inset 0 0 0 1px rgba(56,214,201,.24)}.positive,.metric.positive dd{color:#52e38b}.negative,.metric.negative dd{color:#ff4d6d}.neutral,.metric.neutral dd{color:#ffbf3f}dd{margin:6px 0 0;font-weight:850;overflow-wrap:anywhere;color:#f6fcff}.lesson{margin-top:12px}.lesson b{color:#9ffff6}.lesson p{margin:7px 0 0;color:#bfd0e0;line-height:1.45}.history{margin-top:18px;border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:12px;background:rgba(255,255,255,.035)}.history summary{cursor:pointer;font-weight:900;color:#9ffff6}.history-scroll{overflow:auto;margin-top:12px}table{width:100%;border-collapse:collapse;min-width:860px}th,td{text-align:left;border-bottom:1px solid rgba(255,255,255,.10);padding:10px 8px;font-size:.92rem}th{color:#8fa4b8;text-transform:uppercase;font-size:.72rem;letter-spacing:.05em}.notice,.legal{color:#8fa4b8;line-height:1.5}.back{display:inline-block;margin:24px 0}.legal{border-top:1px solid rgba(255,255,255,.10);margin-top:28px;padding-top:18px}footer{text-align:center;color:#8fa4b8;padding:24px}@media(max-width:980px){.prices,.cards,.summary-current{grid-template-columns:1fr}.grid,.price,.summary-top{grid-template-columns:1fr}.nav{display:none}}
"""


def render_page(lang: str) -> str:
    method = base.load_json(base.METHOD_PATH, {})
    live_prices = base.load_json(base.LIVE_PRICE_PATH, {"prices": {}})
    cfg = {x.get("id"): x for x in method.get("instruments", [])}
    weeks = base.load_weeklies()[:20]
    title = "Otwarte pozycje tygodniowe" if lang == "pl" else "Open weekly positions"
    desc = "Spójny widok pozycji tygodniowych: EUR/USD 10 000 EUR, S&P 500 futures 10 000 USD i BTC/USD 10 000 USD. Pokazujemy kierunek, otwarcie, SL, TP, bieżący wynik, zamknięcie i historię." if lang == "pl" else "Consistent weekly-position view: EUR/USD EUR 10,000, S&P 500 futures USD 10,000 and BTC/USD USD 10,000. Includes direction, open, SL, TP, current result, close and history."
    week_sections = []
    for week in weeks:
        cards = "".join(render_instrument_card(x, week, lang, cfg.get(x.get("instrument_id"), {}), live_prices) for x in week.get("instruments", []))
        week_sections.append(f"<section class='week'><h2>{html.escape(str(week.get('week_id','')))}</h2><p>{html.escape(str(week.get('forecast_for_week_start','')))} — {html.escape(str(week.get('forecast_for_week_end','')))}</p><div class='cards'>{cards}</div></section>")
    legal = "Treści mają charakter informacyjny i edukacyjny. Nie są rekomendacją inwestycyjną ani poradą finansową." if lang == "pl" else "Content is informational and educational. It is not investment advice or a financial recommendation."
    home = "/pl/inwestycje.html" if lang == "pl" else "/en/investing.html"
    back = "Wróć do Inwestycji" if lang == "pl" else "Back to Investing"
    pill = "EUR/USD: 10 000 EUR · S&P 500: 10 000 USD · BTC/USD: 10 000 USD" if lang == "pl" else "EUR/USD: EUR 10,000 · S&P 500: USD 10,000 · BTC/USD: USD 10,000"
    simple_note = "Każda pozycja ma zapisany plan: stop loss, take profit, ryzyko do SL, potencjał do TP i wniosek z analizy. Wewnętrzne pola modelu typu score i pewność nie są pokazywane użytkownikowi." if lang == "pl" else "Every position has a saved plan: stop loss, take profit, risk to SL, potential to TP and a review note. Internal fields such as score and confidence are hidden."
    nav = "<a href='/pl/aktualnosci.html'>Aktualności</a><a href='/pl/geopolityka.html'>Geopolityka</a><a href='/pl/zdrowie.html'>Zdrowie</a><a href='/pl/nauka.html'>Nauka</a><a href='/pl/inwestycje.html'>Inwestycje</a><a href='/pl/o-projekcie.html'>O nas</a>" if lang == "pl" else "<a href='/en/news.html'>News</a><a href='/en/geopolitics.html'>Geopolitics</a><a href='/en/health.html'>Health</a><a href='/en/science.html'>Science</a><a href='/en/investing.html'>Investing</a>"
    logo_href = "/pl/" if lang == "pl" else "/en/"
    switch_href = "/en/investing/weekly-forecasts.html" if lang == "pl" else "/pl/inwestycje/prognozy-tygodniowe.html"
    switch_txt = "EN" if lang == "pl" else "PL"
    return f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)} | BriefRooms</title><meta name='description' content='{html.escape(desc)}'><link rel='icon' href='/assets/favicon.svg'><link rel='stylesheet' href='/assets/site.css?v=rooms-risk-1'><style>{css()}</style></head><body><header><div class='top'><a class='brand' href='{logo_href}'><span class='logo'>BRIEFROOMS</span><span class='trust-pill'>AI-assisted</span></a><nav class='nav'>{nav}</nav><a class='icon-btn' href='{switch_href}'>{switch_txt}</a></div></header><section class='hero'><div class='hero-card'><span class='pill'>{html.escape(pill)}</span><h1>{html.escape(title)}</h1><p class='lead'>{html.escape(desc)}</p></div></section><main>{render_summary_panel(weeks, cfg, live_prices, lang)}<section class='panel notice'><p><b>{'Zasada widoku' if lang == 'pl' else 'View rule'}:</b> {html.escape(simple_note)}</p></section>{render_learning_panel(lang)}{render_live_price_panel(method, live_prices, lang)}{''.join(week_sections)}<p>← <a class='back' href='{home}'>{html.escape(back)}</a></p><section class='legal'><p>{html.escape(legal)}</p></section></main><footer>© BriefRooms</footer></body></html>"


def render_pages(refresh_live: bool = True) -> None:
    if refresh_live:
        base.capture_live_prices()
    risk.review_all()
    base.write_text_lf(base.PL_PAGE, render_page("pl"))
    base.write_text_lf(base.EN_PAGE, render_page("en"))


def patch_base() -> None:
    base.calculate_result = calculate_result
    base.render_instrument_card = render_instrument_card
    base.render_live_price_panel = render_live_price_panel
    base.render_page = render_page
    base.render_pages = render_pages


if __name__ == "__main__":
    patch_base()
    base.main()
