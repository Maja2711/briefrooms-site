#!/usr/bin/env python3
"""Friendly public view for BriefRooms Investing."""
from __future__ import annotations

import html
from typing import Any, Dict, Optional, Tuple

import investments_weekly as base

EURUSD_NOTIONAL = 10_000.0


def eurusd_notional(item: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    return base.safe_float(item.get("notional_eur")) or base.safe_float(cfg.get("notional_eur")) or EURUSD_NOTIONAL


def money(value: Optional[float], currency: str = "USD") -> str:
    return "—" if value is None else f"{value:+,.2f} {currency}".replace(",", " ")


def trade_metrics(item: Dict[str, Any], mark: Optional[float], cfg: Dict[str, Any]) -> Dict[str, Any]:
    entry = base.safe_float(item.get("entry_price"))
    direction = str(item.get("direction") or "neutral")
    if entry is None or mark is None or direction not in {"long", "short"}:
        return {"available": False}
    move, pct = base.strategy_move(entry, mark, direction, cfg)
    if item.get("instrument_id") == "eurusd":
        notional = eurusd_notional(item, cfg)
        return {"available": True, "value": move * notional, "currency": "USD", "units": move / 0.0001, "unit_pl": "pipsów", "unit_en": "pips", "pct": pct, "notional": notional}
    return {"available": True, "value": move, "currency": None, "units": move, "unit_pl": "pkt", "unit_en": "pts", "pct": pct, "notional": None}


def calculate_result(item: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    res = trade_metrics(item, base.safe_float(item.get("exit_price")), cfg)
    if not res.get("available"):
        if str(item.get("direction") or "neutral") == "neutral":
            item.update({"result": "no_trade", "result_value": 0, "result_percent": 0})
        return
    value = base.safe_float(res.get("value"))
    item.update({"result_value": value, "result_percent": res.get("pct"), "result_units": res.get("units"), "result": "flat" if value is not None and abs(value) < 0.05 else "profit" if value is not None and value > 0 else "loss"})
    if item.get("instrument_id") == "eurusd":
        item.update({"notional_eur": res.get("notional"), "result_currency": "USD"})


def dir_text(item: Dict[str, Any], lang: str) -> str:
    direction = str(item.get("direction") or "neutral")
    if item.get("instrument_id") == "eurusd":
        labels = {"pl": {"long": "Kup EUR / sprzedaj USD", "short": "Sprzedaj EUR / kup USD", "neutral": "Bez pozycji — obserwacja EUR/USD"}, "en": {"long": "Buy EUR / sell USD", "short": "Sell EUR / buy USD", "neutral": "No position — observe EUR/USD"}}
    else:
        labels = {"pl": {"long": "Scenariusz wzrostowy", "short": "Scenariusz spadkowy", "neutral": "Obserwacja rynku"}, "en": {"long": "Upside scenario", "short": "Downside scenario", "neutral": "Market observation"}}
    return labels[lang].get(direction, labels[lang]["neutral"])


def tone(value: Optional[float]) -> str:
    return "neutral" if value is None or abs(value) < 0.000001 else "positive" if value > 0 else "negative"


def pnl_text(item: Dict[str, Any], mark: Optional[float], cfg: Dict[str, Any], lang: str) -> Tuple[str, str]:
    if str(item.get("direction") or "neutral") not in {"long", "short"}:
        return ("Bez pozycji — obserwacja" if lang == "pl" else "No position — observation"), "neutral"
    res = trade_metrics(item, mark, cfg)
    if not res.get("available"):
        return ("Czekamy na cenę otwarcia" if base.safe_float(item.get("entry_price")) is None and lang == "pl" else "Waiting for opening price" if base.safe_float(item.get("entry_price")) is None else "Brak świeżej ceny" if lang == "pl" else "No fresh price"), "neutral"
    units = base.safe_float(res.get("units")) or 0.0
    pct = base.safe_float(res.get("pct")) or 0.0
    unit = res.get("unit_pl" if lang == "pl" else "unit_en")
    value = base.safe_float(res.get("value"))
    if res.get("currency"):
        return f"{money(value, str(res.get('currency')))} · {units:+.1f} {unit} · {pct:+.2f}%", tone(value)
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
    else:
        text = f"{delta:+.2f} pkt · {pct:+.2f}%" if lang == "pl" else f"{delta:+.2f} pts · {pct:+.2f}%"
    return text, tone(delta)


def metric(label: str, value: str, state: str = "", big: bool = False) -> str:
    cls = "metric" + (f" {state}" if state else "") + (" big" if big else "")
    return f"<div class='{cls}'><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>"


def render_instrument_card(item: Dict[str, Any], week: Dict[str, Any], lang: str, cfg: Dict[str, Any], live_prices: Dict[str, Any]) -> str:
    inst_id = str(item.get("instrument_id") or "")
    label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("symbol") or inst_id)
    rec = base.live_record(live_prices, inst_id)
    current = base.safe_float(rec.get("price"))
    open_point = base.week_open_point(item, week, cfg)
    open_price = base.safe_float(open_point.price)
    close_price = base.safe_float(item.get("exit_price"))
    open_txt = base.format_price(open_price) if open_price is not None else ("Czekamy na otwarcie" if lang == "pl" else "Waiting for open")
    close_txt = base.format_price(close_price) if close_price is not None else ("Tydzień trwa — zamknięcie później" if base.week_is_in_progress(week) and lang == "pl" else "Week in progress — close later" if base.week_is_in_progress(week) else "Brak ceny zamknięcia" if lang == "pl" else "No close price")
    live_txt, live_state = pnl_text(item, current, cfg, lang)
    final_txt, final_state = pnl_text(item, close_price, cfg, lang) if close_price is not None else (close_txt, "neutral")
    move_txt, move_state = market_move(current, open_price, item, lang)
    nominal = f"{eurusd_notional(item, cfg):,.0f} EUR".replace(",", " ") if inst_id == "eurusd" else ("bez nominalu — wynik w punktach" if lang == "pl" else "no notional — result in points")
    status = "Pozycja zamknięta i rozliczona" if close_price is not None and lang == "pl" else "Position closed and settled" if close_price is not None else "Pozycja otwarta" if open_price is not None and lang == "pl" else "Position open" if open_price is not None else "Pozycja zaplanowana" if lang == "pl" else "Position planned"
    direction = str(item.get("direction") or "neutral")
    dir_class = {"long": "positive", "short": "negative"}.get(direction, "neutral")
    current_txt = base.format_price(current) if current is not None else ("Brak świeżej ceny" if lang == "pl" else "No fresh price")
    updated = base.timestamp_label(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"), lang, "brak aktualizacji" if lang == "pl" else "no update")
    items = [
        metric("Cena otwarcia pozycji" if lang == "pl" else "Position opening price", open_txt),
        metric("Cena zamknięcia pozycji" if lang == "pl" else "Position closing price", close_txt),
        metric("Zysk / strata teraz" if lang == "pl" else "Profit / loss now", live_txt, live_state, True),
        metric("Wynik po zamknięciu" if lang == "pl" else "Final result", final_txt, final_state, True),
        metric("Nominał pozycji" if lang == "pl" else "Position notional", nominal),
        metric("Ruch rynku od otwarcia" if lang == "pl" else "Market move from open", move_txt, move_state),
        metric("Status" if lang == "pl" else "Status", status),
    ]
    return f"""
    <article class='instrument {dir_class}'><div class='head'><div><p>{'Pozycja edukacyjna' if lang == 'pl' else 'Educational position'}</p><h3>{html.escape(label)}</h3><small>Yahoo Finance · {html.escape(str(item.get('symbol') or cfg.get('symbol') or ''))}</small></div><b>{html.escape(dir_text(item, lang))}</b></div><div class='price'><div><span>{'Cena teraz' if lang == 'pl' else 'Price now'}</span><strong>{html.escape(current_txt)}</strong><small>{'Aktualizacja' if lang == 'pl' else 'Updated'}: {html.escape(updated)}</small></div><div><span>{'Start pozycji' if lang == 'pl' else 'Position start'}</span><strong>{html.escape(open_txt)}</strong><small>{'Zamknięcie' if lang == 'pl' else 'Close'}: {html.escape(close_txt)}</small></div></div><dl class='grid'>{''.join(items)}</dl></article>"""


def render_live_price_panel(method: Dict[str, Any], live_prices: Dict[str, Any], lang: str) -> str:
    cards = []
    for inst in method.get("instruments", []):
        rec = base.live_record(live_prices, inst.get("id", ""))
        price = base.safe_float(rec.get("price"))
        label = inst.get("label_pl" if lang == "pl" else "label_en", inst.get("id", ""))
        cards.append(f"<div class='price-card'><h3>{html.escape(str(label))}</h3><p>{html.escape(base.format_price(price) if price is not None else ('Brak świeżej ceny' if lang == 'pl' else 'No fresh price'))}</p></div>")
    return f"<section class='panel'><h2>{'Aktualne ceny rynkowe' if lang == 'pl' else 'Current market prices'}</h2><div class='prices'>{''.join(cards)}</div></section>"


def css() -> str:
    return """body{margin:0;background:#f4f7fb;color:#0f172a;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}header,main{max-width:1180px;margin:auto;padding:0 20px}header{padding-top:42px}h1{font-size:clamp(2rem,4vw,3.4rem);letter-spacing:-.04em}.lead{color:#475569;line-height:1.6;max-width:850px}.pill{display:inline-flex;padding:8px 12px;border-radius:999px;background:#eff6ff;color:#1d4ed8;font-weight:900}.notice,.panel,.week{background:#fff;border:1px solid #dbe4ee;border-radius:24px;box-shadow:0 20px 55px #0f172a14;margin:20px 0;padding:22px}.prices,.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.price-card,.instrument{background:#fbfdff;border:1px solid #dbe4ee;border-radius:20px;padding:18px}.instrument.positive{border-top:6px solid #16a34a}.instrument.negative{border-top:6px solid #dc2626}.instrument.neutral{border-top:6px solid #64748b}.head,.price{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:start}.head p{margin:0 0 6px;color:#2563eb;font-size:.75rem;font-weight:900;text-transform:uppercase}.head h3{margin:0}.head b{border-radius:999px;padding:9px 12px;background:#e0f2fe;color:#075985}.price{grid-template-columns:1.2fr .8fr;margin:18px 0}.price>div,.metric{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:13px}.price span,dt{display:block;color:#64748b;font-size:.72rem;font-weight:900;text-transform:uppercase;letter-spacing:.05em}.price strong{display:block;font-size:clamp(1.5rem,3vw,2.7rem);margin:6px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0}.metric.big{box-shadow:inset 0 0 0 1px #bfdbfe}.metric.positive dd{color:#166534}.metric.negative dd{color:#991b1b}.metric.neutral dd{color:#075985}dd{margin:6px 0 0;font-weight:850;overflow-wrap:anywhere}.legal{color:#64748b;font-size:.85rem;border-top:1px solid #dbe4ee;margin-top:28px;padding-top:16px}@media(max-width:850px){header,main{padding:0 14px}.prices,.cards,.price,.grid,.head{grid-template-columns:1fr}.notice,.panel,.week{padding:16px;border-radius:18px}}"""


def render_page(lang: str) -> str:
    method = base.load_json(base.METHOD_PATH, {})
    live_prices = base.load_json(base.LIVE_PRICE_PATH, {"prices": {}})
    cfg = {x.get("id"): x for x in method.get("instruments", [])}
    title = "Inwestycje — pozycje tygodniowe" if lang == "pl" else "Investing — weekly positions"
    desc = "Prosty widok: kierunek pozycji, cena otwarcia, cena zamknięcia oraz zysk lub strata. Dla EUR/USD pozycja edukacyjna ma nominał 10 000 EUR." if lang == "pl" else "Simple view: direction, opening price, closing price and profit or loss. EUR/USD uses a EUR 10,000 notional."
    weeks = []
    for week in base.load_weeklies()[:20]:
        cards = "".join(render_instrument_card(x, week, lang, cfg.get(x.get("instrument_id"), {}), live_prices) for x in week.get("instruments", []))
        weeks.append(f"<section class='week'><h2>{html.escape(str(week.get('week_id','')))}</h2><p>{html.escape(str(week.get('forecast_for_week_start','')))} — {html.escape(str(week.get('forecast_for_week_end','')))}</p><div class='cards'>{cards}</div></section>")
    legal = "Treści mają charakter informacyjny i edukacyjny. Nie są rekomendacją inwestycyjną ani poradą finansową." if lang == "pl" else "Content is informational and educational. It is not investment advice or a financial recommendation."
    home = "/pl/inwestycje.html" if lang == "pl" else "/en/investing.html"
    back = "Wróć do Inwestycji" if lang == "pl" else "Back to Investing"
    return f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)} | BriefRooms</title><meta name='description' content='{html.escape(desc)}'><link rel='icon' href='/assets/favicon.svg'><link rel='stylesheet' href='/assets/site.css?v=rooms3'><style>{css()}</style></head><body><header><span class='pill'>EUR/USD: 10 000 EUR</span><h1>{html.escape(title)}</h1><p class='lead'>{html.escape(desc)}</p></header><main><section class='notice'><p><b>{'Prosty widok' if lang == 'pl' else 'Simple view'}:</b> {'Pokazujemy kierunek, otwarcie, zamknięcie, nominał oraz zysk/stratę. Nie pokazujemy wewnętrznych pól modelu typu score i pewność.' if lang == 'pl' else 'We show direction, open, close, notional and profit/loss. Internal model fields such as score and confidence are hidden.'}</p></section>{render_live_price_panel(method, live_prices, lang)}{''.join(weeks)}<p>← <a href='{home}'>{html.escape(back)}</a></p><section class='legal'><p>{html.escape(legal)}</p></section></main><footer style='text-align:center;color:#64748b;padding:24px'>© BriefRooms</footer></body></html>"


def patch_base() -> None:
    base.calculate_result = calculate_result
    base.render_instrument_card = render_instrument_card
    base.render_live_price_panel = render_live_price_panel
    base.render_page = render_page


if __name__ == "__main__":
    patch_base()
    base.main()
