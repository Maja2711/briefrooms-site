#!/usr/bin/env python3
"""User-friendly renderer and EUR/USD notional layer for BriefRooms Investing."""

from __future__ import annotations

import html
from typing import Any, Dict, Optional, Tuple

import investments_weekly as base

EURUSD_DEFAULT_NOTIONAL = 10_000.0


def fmt_number(value: Any, digits: int = 2) -> str:
    number = base.safe_float(value)
    if number is None:
        return "—"
    return f"{number:,.{digits}f}".replace(",", " ")


def eurusd_notional(item: Dict[str, Any], inst_cfg: Dict[str, Any]) -> float:
    return base.safe_float(item.get("notional_eur")) or base.safe_float(inst_cfg.get("notional_eur")) or EURUSD_DEFAULT_NOTIONAL


def money(value: Optional[float], currency: str) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f} {currency}".replace(",", " ")


def trade_metrics(item: Dict[str, Any], mark: Optional[float], inst_cfg: Dict[str, Any]) -> Dict[str, Any]:
    entry = base.safe_float(item.get("entry_price"))
    direction = str(item.get("direction") or "neutral")
    if entry is None or mark is None or direction not in {"long", "short"}:
        return {"available": False}
    move, pct = base.strategy_move(entry, mark, direction, inst_cfg)
    if item.get("instrument_id") == "eurusd":
        pip_size = base.safe_float(inst_cfg.get("pip_size")) or 0.0001
        notional = eurusd_notional(item, inst_cfg)
        return {
            "available": True,
            "move": move,
            "pct": pct,
            "units": move / pip_size,
            "unit_pl": "pipsów",
            "unit_en": "pips",
            "pnl": move * notional,
            "currency": "USD",
            "notional": notional,
        }
    return {
        "available": True,
        "move": move,
        "pct": pct,
        "units": move,
        "unit_pl": "pkt",
        "unit_en": "pts",
        "pnl": None,
        "currency": None,
        "notional": None,
    }


def calculate_result(item: Dict[str, Any], inst_cfg: Dict[str, Any]) -> None:
    close = base.safe_float(item.get("exit_price"))
    metrics = trade_metrics(item, close, inst_cfg)
    if not metrics.get("available"):
        if str(item.get("direction") or "neutral") == "neutral":
            item.update({"result": "no_trade", "result_value": 0, "result_percent": 0})
        return
    value = base.safe_float(metrics.get("pnl")) if metrics.get("pnl") is not None else base.safe_float(metrics.get("units"))
    item["result_value"] = value
    item["result_percent"] = metrics.get("pct")
    item["result_units"] = metrics.get("units")
    if metrics.get("currency"):
        item["result_currency"] = metrics.get("currency")
    if metrics.get("notional"):
        item["notional_eur"] = metrics.get("notional")
    item["result"] = "flat" if value is not None and abs(value) < 0.05 else "profit" if value is not None and value > 0 else "loss"


def user_direction_label(item: Dict[str, Any], lang: str) -> str:
    direction = str(item.get("direction") or "neutral")
    if item.get("instrument_id") == "eurusd":
        labels = {
            "pl": {"long": "Kup EUR / sprzedaj USD", "short": "Sprzedaj EUR / kup USD", "neutral": "Bez pozycji — obserwacja EUR/USD"},
            "en": {"long": "Buy EUR / sell USD", "short": "Sell EUR / buy USD", "neutral": "No position — observe EUR/USD"},
        }
        return labels[lang].get(direction, labels[lang]["neutral"])
    labels = {
        "pl": {"long": "Scenariusz wzrostowy", "short": "Scenariusz spadkowy", "neutral": "Obserwacja rynku"},
        "en": {"long": "Upside scenario", "short": "Downside scenario", "neutral": "Market observation"},
    }
    return labels[lang].get(direction, labels[lang]["neutral"])


def result_text(item: Dict[str, Any], mark: Optional[float], inst_cfg: Dict[str, Any], lang: str) -> Tuple[str, str]:
    direction = str(item.get("direction") or "neutral")
    if direction not in {"long", "short"}:
        return ("Brak pozycji — tylko obserwacja" if lang == "pl" else "No position — observation only"), "neutral"
    metrics = trade_metrics(item, mark, inst_cfg)
    if not metrics.get("available"):
        if base.safe_float(item.get("entry_price")) is None:
            return ("Czekamy na cenę otwarcia pozycji" if lang == "pl" else "Waiting for opening price"), "neutral"
        return ("Brak świeżej ceny rynkowej" if lang == "pl" else "No fresh market price"), "neutral"
    pct = metrics.get("pct")
    pct_text = "—" if pct is None else f"{pct:+.2f}%"
    units = base.safe_float(metrics.get("units")) or 0.0
    unit = str(metrics.get("unit_pl" if lang == "pl" else "unit_en"))
    if metrics.get("pnl") is not None:
        value_text = f"{money(base.safe_float(metrics.get('pnl')), str(metrics.get('currency')))} · {units:+.1f} {unit} · {pct_text}"
        value = base.safe_float(metrics.get("pnl"))
    else:
        value_text = f"{units:+.1f} {unit} · {pct_text}"
        value = units
    return value_text, base.change_class(value)


def final_result_text(item: Dict[str, Any], week: Dict[str, Any], inst_cfg: Dict[str, Any], lang: str) -> Tuple[str, str]:
    close = base.safe_float(item.get("exit_price"))
    if close is None:
        return (base.week_in_progress_label(lang) if base.week_is_in_progress(week) else ("Brak ceny zamknięcia" if lang == "pl" else "No close price")), "neutral"
    text, tone = result_text(item, close, inst_cfg, lang)
    label = base.result_label(item.get("result"), lang)
    return f"{label}: {text}", tone


def position_status(item: Dict[str, Any], week: Dict[str, Any], open_price: Optional[float], lang: str, price_stale: bool = False) -> str:
    direction = str(item.get("direction") or "neutral")
    if price_stale:
        return "Cena rynkowa wymaga odświeżenia" if lang == "pl" else "Market price needs refresh"
    if direction == "neutral":
        return "Bez pozycji — obserwacja rynku" if lang == "pl" else "No position — market observation"
    if open_price is None:
        return "Pozycja zaplanowana — czekamy na otwarcie" if lang == "pl" else "Position planned — waiting for entry"
    if base.safe_float(item.get("exit_price")) is not None:
        return "Pozycja zamknięta i rozliczona" if lang == "pl" else "Position closed and settled"
    if base.week_is_in_progress(week):
        return "Pozycja otwarta — trwa tydzień" if lang == "pl" else "Position open — week in progress"
    return "Czekamy na cenę zamknięcia" if lang == "pl" else "Waiting for close price"


def rationale_html(item: Dict[str, Any], lang: str) -> str:
    rationale = item.get("rationale_pl" if lang == "pl" else "rationale_en", [])
    visible = [base.rationale_display_text(str(x)) for x in rationale if not base.is_legal_rationale(str(x))]
    if not visible:
        return ""
    heading = "Dlaczego taki scenariusz?" if lang == "pl" else "Why this scenario?"
    lis = "".join(f"<li>{html.escape(line)}</li>" for line in visible[:3])
    return f'<div class="forecast-rationale"><h4>{heading}</h4><ul>{lis}</ul></div>'


def render_metric(label: str, value: str, tone: str = "", featured: bool = False) -> str:
    tone_class = f" forecast-metric--{html.escape(tone)}" if tone else ""
    featured_class = " forecast-metric--featured" if featured else ""
    return f'<div class="forecast-metric{tone_class}{featured_class}"><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>'


def render_instrument_card(item: Dict[str, Any], week: Dict[str, Any], lang: str, inst_cfg: Dict[str, Any], live_prices: Dict[str, Any]) -> str:
    inst_id = str(item.get("instrument_id", ""))
    label = str(item.get("label_pl" if lang == "pl" else "label_en") or item.get("symbol") or inst_id)
    symbol = str(item.get("symbol") or inst_cfg.get("symbol") or "")
    rec = base.live_record(live_prices, inst_id)
    current = base.safe_float(rec.get("price"))
    stale = base.live_price_is_stale(rec)
    current_display = base.format_price(current) if current is not None else base.no_fresh_price_label(lang)
    updated = base.timestamp_label(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"), lang, "Brak aktualizacji ceny" if lang == "pl" else "No price update available")
    open_point = base.week_open_point(item, week, inst_cfg)
    open_price = base.safe_float(open_point.price)
    open_display = base.format_price(open_price) if open_price is not None else base.no_week_open_label(lang)
    close_display = base.close_display(item, week, lang)
    market_text, market_tone = base.current_market_change_display(current, open_price, item, inst_cfg, lang)
    live_text, live_tone = result_text(item, current, inst_cfg, lang)
    final_text, final_tone = final_result_text(item, week, inst_cfg, lang)
    direction = str(item.get("direction") or "neutral")
    dir_class = base.direction_class(direction)
    nominal = f"{fmt_number(eurusd_notional(item, inst_cfg), 0)} EUR" if inst_id == "eurusd" else ("bez nominalu — wynik w punktach" if lang == "pl" else "no notional — result in points")
    current_label = "Ostatnia zapisana cena" if stale and lang == "pl" else "Last saved price" if stale else "Cena teraz" if lang == "pl" else "Price now"
    update_text = base.stale_price_message(rec, lang) if stale else f"{'Aktualizacja' if lang == 'pl' else 'Updated'}: {updated}"
    metrics = [
        render_metric("Cena otwarcia pozycji" if lang == "pl" else "Position opening price", open_display),
        render_metric("Cena zamknięcia pozycji" if lang == "pl" else "Position closing price", close_display),
        render_metric("Zysk / strata teraz" if lang == "pl" else "Profit / loss now", live_text, live_tone, True),
        render_metric("Wynik po zamknięciu" if lang == "pl" else "Final result", final_text, final_tone, True),
        render_metric("Nominał pozycji" if lang == "pl" else "Position notional", nominal),
        render_metric("Ruch rynku od otwarcia" if lang == "pl" else "Market move from open", market_text, market_tone),
        render_metric("Status" if lang == "pl" else "Status", position_status(item, week, open_price, lang, stale)),
    ]
    return f'''
      <article class="forecast-instrument forecast-instrument-card forecast-instrument-card--{dir_class}{' price-stale' if stale else ''}">
        <div class="forecast-card-head"><div><p class="forecast-kicker">{'Pozycja edukacyjna' if lang == 'pl' else 'Educational position'}</p><h3>{html.escape(label)}</h3><p class="forecast-source"><span>{'Źródło ceny' if lang == 'pl' else 'Price source'}:</span> Yahoo Finance · {html.escape(symbol)}</p></div><span class="forecast-direction-badge forecast-direction-badge--{dir_class}">{html.escape(user_direction_label(item, lang))}</span></div>
        <div class="forecast-price-zone"><div class="forecast-price-primary"><span>{html.escape(current_label)}</span><strong class="forecast-price-main">{html.escape(current_display)}</strong><small>{html.escape(update_text)}</small></div><div class="forecast-price-secondary"><span>{'Start pozycji' if lang == 'pl' else 'Position start'}</span><strong>{html.escape(open_display)}</strong><em>{'Zamknięcie' if lang == 'pl' else 'Close'}: {html.escape(close_display)}</em></div></div>
        <dl class="forecast-metric-grid">{''.join(metrics)}</dl>{rationale_html(item, lang)}
      </article>'''


def render_live_price_panel(method: Dict[str, Any], live_prices: Dict[str, Any], lang: str) -> str:
    cards = []
    for inst in method.get("instruments", []):
        inst_id = inst.get("id", "")
        label = inst.get("label_pl" if lang == "pl" else "label_en", inst_id)
        rec = base.live_record(live_prices, inst_id)
        price = base.safe_float(rec.get("price"))
        stale = base.live_price_is_stale(rec)
        price_label = base.format_price(price) if price is not None else base.no_fresh_price_label(lang)
        updated = base.timestamp_label(rec.get("current_price_updated_at") or rec.get("timestamp") or rec.get("last_attempt_at"), lang, "Brak aktualizacji ceny" if lang == "pl" else "No price update available")
        price_dt = "Ostatnia zapisana cena" if stale and lang == "pl" else "Last saved price" if stale else "Cena teraz" if lang == "pl" else "Price now"
        cards.append(f'<div class="price-card{" price-stale" if stale else ""}"><h3>{html.escape(str(label))}</h3><dl><div><dt>{html.escape(price_dt)}</dt><dd>{html.escape(price_label)}</dd></div><div><dt>{"Aktualizacja" if lang == "pl" else "Updated"}</dt><dd>{html.escape(updated)}</dd></div></dl></div>')
    title = "Aktualne ceny rynkowe" if lang == "pl" else "Current market prices"
    lead = "Ceny służą do pokazania bieżącego zysku lub straty otwartej pozycji." if lang == "pl" else "Prices are used to show current profit or loss for open positions."
    return f'<section class="price-panel"><div class="section-title"><h2>{html.escape(title)}</h2><p>{html.escape(lead)}</p></div><div class="price-grid">{"".join(cards)}</div></section>'


def page_css() -> str:
    return """
    :root{color-scheme:light;--ink:#0f172a;--muted:#64748b;--line:#dbe4ee;--green:#16a34a;--red:#dc2626;}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top left,#e0f2fe 0,#f5f7fb 36%,#eef2f7 100%);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header,main{max-width:1180px;margin:0 auto;padding:0 20px}header{padding-top:42px;padding-bottom:20px}h1{font-size:clamp(2.15rem,4vw,3.5rem);line-height:1.03;margin:.5rem 0 .85rem;letter-spacing:-.04em}.lead{font-size:1.12rem;line-height:1.65;max-width:850px;margin:0;color:#40506a}.hero-pill,.week-badge{display:inline-flex;border-radius:999px;background:#eff6ff;color:#1d4ed8;border:1px solid rgba(37,99,235,.15);padding:8px 12px;font-weight:900;font-size:.8rem}.notice,.price-panel,.forecast-week-card{background:rgba(255,255,255,.92);border:1px solid rgba(219,228,238,.95);border-radius:24px;box-shadow:0 24px 70px rgba(15,23,42,.10);margin:20px 0;padding:22px}.notice{display:grid;gap:9px}.notice p,.section-title p,.week-head p{margin:0;color:#64748b;line-height:1.55}.section-title{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:16px}.section-title h2,.week-head h2{margin:0;color:#0f172a}.price-grid,.forecast-instrument-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.price-card,.forecast-instrument-card{background:linear-gradient(180deg,#fff 0%,#fbfdff 100%);border:1px solid var(--line);border-radius:22px;box-shadow:0 16px 42px rgba(15,23,42,.08);padding:20px;min-width:0}.price-card{box-shadow:none}.price-card h3{margin:0 0 12px}.price-card dl{display:grid;gap:10px;margin:0}.price-stale{border-color:#f59e0b;background:#fffbeb}.week-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;border-bottom:1px solid #e2e8f0;margin-bottom:18px;padding-bottom:16px}.forecast-instrument-card--positive{border-top:6px solid var(--green)}.forecast-instrument-card--negative{border-top:6px solid var(--red)}.forecast-instrument-card--neutral{border-top:6px solid #64748b}.forecast-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:18px}.forecast-kicker{margin:0 0 6px;color:#2563eb;font-size:.74rem;text-transform:uppercase;letter-spacing:.08em;font-weight:900}.forecast-card-head h3{margin:0 0 5px;font-size:1.35rem}.forecast-source{margin:0;color:#64748b;font-size:.88rem;line-height:1.35}.forecast-source span{font-weight:800;color:#334155}.forecast-direction-badge{display:inline-flex;width:max-content;max-width:100%;border-radius:999px;padding:9px 12px;font-size:.8rem;font-weight:900;line-height:1.2}.forecast-direction-badge--positive{background:#dcfce7;color:#166534}.forecast-direction-badge--negative{background:#fee2e2;color:#991b1b}.forecast-direction-badge--neutral{background:#e0f2fe;color:#075985}.forecast-price-zone{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(210px,.75fr);gap:16px;margin-bottom:18px}.forecast-price-primary,.forecast-price-secondary{background:#f8fafc;border:1px solid #e2e8f0;border-radius:18px;padding:16px;min-width:0}.forecast-price-primary span,.forecast-price-secondary span,dt{display:block;color:#64748b;font-size:.72rem;font-weight:900;text-transform:uppercase;letter-spacing:.06em}.forecast-price-main{display:block;margin:7px 0 5px;font-size:clamp(2rem,4vw,3.05rem);line-height:1;font-weight:900;letter-spacing:-.04em;overflow-wrap:anywhere}.forecast-price-primary small,.forecast-price-secondary em{display:block;color:#64748b;font-size:.86rem;line-height:1.35;font-style:normal}.forecast-price-secondary strong{display:block;margin:7px 0 9px;font-size:1.35rem;line-height:1.15;overflow-wrap:anywhere}.forecast-metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:0}.forecast-metric{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:12px 13px}.forecast-metric--featured{background:linear-gradient(180deg,#fff,#f8fafc);box-shadow:inset 0 0 0 1px rgba(37,99,235,.06)}.forecast-metric--positive dd{color:#166534}.forecast-metric--negative dd{color:#991b1b}.forecast-metric--neutral dd{color:#075985}dd{margin:6px 0 0;font-weight:850;line-height:1.35;overflow-wrap:anywhere}.forecast-rationale{margin-top:16px;border-top:1px solid #e2e8f0;padding-top:14px}.forecast-rationale h4{margin:0 0 8px;color:#334155;font-size:.82rem;text-transform:uppercase;letter-spacing:.06em}.forecast-rationale ul{margin:0;padding-left:1.1rem;color:#475569;line-height:1.58}.forecast-legal-note{border-top:1px solid #dbe4ee;margin:32px 0 20px;padding-top:16px;color:#64748b;font-size:.82rem;line-height:1.55}.back{margin:24px 0 16px}a{color:#1d4ed8}footer{color:#64748b;text-align:center;padding:22px 16px 30px;font-size:.88rem}@media(max-width:860px){header,main{padding:0 14px}header{padding-top:30px}.section-title,.week-head,.forecast-card-head{display:block}.price-grid,.forecast-instrument-grid,.forecast-price-zone,.forecast-metric-grid{grid-template-columns:1fr}.notice,.price-panel,.forecast-week-card,.forecast-instrument-card{padding:16px;border-radius:18px}.forecast-direction-badge{margin-top:10px}.forecast-price-main{font-size:2.15rem}}
    """


def render_page(lang: str) -> str:
    method = base.load_json(base.METHOD_PATH, {})
    live_prices = base.load_json(base.LIVE_PRICE_PATH, {"prices": {}})
    cfg_by_id = {x.get("id"): x for x in method.get("instruments", [])}
    title = "Inwestycje — pozycje tygodniowe" if lang == "pl" else "Investing — weekly positions"
    desc = "Prosty widok: kierunek pozycji, cena otwarcia, cena zamknięcia oraz zysk lub strata. Dla EUR/USD pozycja edukacyjna ma nominał 10 000 EUR." if lang == "pl" else "A simple view: position direction, opening price, closing price and profit or loss. For EUR/USD the educational position uses a EUR 10,000 notional."
    canonical = "https://briefrooms.com/pl/inwestycje/prognozy-tygodniowe.html" if lang == "pl" else "https://briefrooms.com/en/investing/weekly-forecasts.html"
    home_link = "/pl/inwestycje.html" if lang == "pl" else "/en/investing.html"
    home_text = "Wróć do Inwestycji" if lang == "pl" else "Back to Investing"
    updated = base.now_local().strftime("%Y-%m-%d %H:%M %Z")
    weeks_html = []
    for week in base.load_weeklies()[:20]:
        instruments = "".join(render_instrument_card(x, week, lang, cfg_by_id.get(x.get("instrument_id"), {}), live_prices) for x in week.get("instruments", []))
        created = base.timestamp_label(week.get("forecast_created_at"), lang, "Nie zapisano daty utworzenia scenariusza" if lang == "pl" else "Scenario creation date was not saved")
        weeks_html.append(f'<section class="week-card forecast-week-card"><div class="week-head"><div><span class="week-badge">{html.escape(str(week.get("week_id", "")))}</span><h2>{"Tydzień pozycji" if lang == "pl" else "Position week"}</h2><p>{"Okres" if lang == "pl" else "Period"}: {html.escape(str(week.get("forecast_for_week_start", "")))} — {html.escape(str(week.get("forecast_for_week_end", "")))}</p><p>{"Scenariusz utworzony" if lang == "pl" else "Scenario created"}: {html.escape(created)}</p></div></div><div class="forecast-instrument-grid">{instruments}</div></section>')
    if not weeks_html:
        weeks_html.append(f'<section class="week-card forecast-week-card"><h2>{"Brak zapisanych pozycji" if lang == "pl" else "No saved positions yet"}</h2></section>')
    legal_title = "Nota prawna" if lang == "pl" else "Legal note"
    legal_text = "Treści prezentowane na BriefRooms mają charakter wyłącznie informacyjny i edukacyjny. Nie stanowią rekomendacji inwestycyjnej, porady inwestycyjnej, analizy inwestycyjnej ani oferty kupna lub sprzedaży instrumentów finansowych. Decyzje inwestycyjne użytkownik podejmuje samodzielnie i na własne ryzyko." if lang == "pl" else "Content presented on BriefRooms is for informational and educational purposes only. It is not an investment recommendation, investment advice, investment analysis, or an offer to buy or sell financial instruments. Users make investment decisions independently and at their own risk."
    data_note = "Pokazujemy tylko informacje zrozumiałe dla użytkownika: kierunek, otwarcie, zamknięcie, nominał oraz zysk/stratę. Wewnętrzne wartości modelu nie są publikowane na karcie." if lang == "pl" else "We show only user-friendly information: direction, open, close, notional and profit/loss. Internal model values are not published on the card."
    methodology_text = "Scenariusz jest automatycznie aktualizowany na podstawie zapisanych danych tygodnia i bieżących cen rynkowych." if lang == "pl" else "The scenario is automatically updated from saved weekly data and current market prices."
    hero = "EUR/USD: nominał 10 000 EUR" if lang == "pl" else "EUR/USD: EUR 10,000 notional"
    css = page_css()
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" /><title>{html.escape(title)} | BriefRooms</title><meta name="description" content="{html.escape(desc)}" /><link rel="icon" href="/assets/favicon.svg" /><link rel="stylesheet" href="/assets/site.css?v=rooms3" /><link rel="canonical" href="{canonical}" /><style>{css}</style></head><body><header><span class="hero-pill">{html.escape(hero)}</span><h1>{html.escape(title)}</h1><p class="lead">{html.escape(desc)}</p></header><main><section class="notice"><p><strong>{'Prosty widok' if lang == 'pl' else 'Simple view'}:</strong> {html.escape(data_note)}</p><p>{html.escape(methodology_text)} <a href="/data/investments/methodology.json">methodology.json</a> · <a href="/data/investments/method_changelog.md">changelog</a></p><p>{'Ostatnia aktualizacja strony' if lang == 'pl' else 'Page last updated'}: {html.escape(updated)}</p></section>{render_live_price_panel(method, live_prices, lang)}{''.join(weeks_html)}<p class="back">← <a href="{home_link}">{html.escape(home_text)}</a></p><section class="forecast-legal-note" aria-label="{html.escape(legal_title)}"><h2>{html.escape(legal_title)}</h2><p>{html.escape(legal_text)}</p></section></main><footer>© BriefRooms</footer><!-- Cloudflare Web Analytics --><script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "9adde99e330a4b0d991627986ac34246"}}'></script><!-- End Cloudflare Web Analytics --></body></html>'''


def patch_base() -> None:
    base.calculate_result = calculate_result
    base.render_instrument_card = render_instrument_card
    base.render_live_price_panel = render_live_price_panel
    base.render_page = render_page


if __name__ == "__main__":
    patch_base()
    base.main()
