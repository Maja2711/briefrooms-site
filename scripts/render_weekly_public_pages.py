#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGES = {
    ROOT / "pl/inwestycje/prognozy-tygodniowe.html": {
        "lang": "pl",
        "title": "Pozycje tygodniowe — BriefRooms",
        "desc": "Prosty widok pozycji tygodniowych: EUR/USD, S&P 500 futures i BTC/USD.",
        "home": "/pl/",
        "invest": "/pl/inwestycje.html",
        "portfolio": "/pl/inwestycje/portfel-10k.html",
        "weekly": "/pl/inwestycje/prognozy-tygodniowe.html",
        "scenario": "/pl/inwestycje/spx-scenariusze-2026.html",
        "switch": "/en/investing/weekly-forecasts.html",
        "switch_txt": "EN",
        "nav1": "Inwestycje",
        "nav_portfolio": "Inwestycje 10k",
        "nav2": "Pozycje tygodniowe",
        "nav3": "S&P 500",
        "h1": "Pozycje tygodniowe",
        "lead": "Po wczytaniu skryptu strona aktualizuje ceny, status zamknięcia i historię.",
        "fallback_h2": "Ostatnio zapisany tydzień",
        "eur": "Dane pozycji są ładowane z plików tygodniowych.",
        "spx": "Dane pozycji są ładowane z plików tygodniowych.",
        "btc": "Dane pozycji są ładowane z plików tygodniowych.",
        "legal": "Treści mają charakter edukacyjny i analityczny. To nie jest rekomendacja inwestycyjna ani porada finansowa. Nie podejmuj decyzji inwestycyjnych wyłącznie na podstawie tej strony.",
        "back": "← Wróć do Inwestycji",
    },
    ROOT / "en/investing/weekly-forecasts.html": {
        "lang": "en",
        "title": "Weekly positions — BriefRooms",
        "desc": "Simple weekly-position view: EUR/USD, S&P 500 futures and BTC/USD.",
        "home": "/en/",
        "invest": "/en/investing.html",
        "portfolio": "/en/investing/portfolio-10k.html",
        "weekly": "/en/investing/weekly-forecasts.html",
        "scenario": "/en/investing/spx-scenarios-2026.html",
        "switch": "/pl/inwestycje/prognozy-tygodniowe.html",
        "switch_txt": "PL",
        "nav1": "Investing",
        "nav_portfolio": "10K Investing",
        "nav2": "Weekly positions",
        "nav3": "S&P 500",
        "h1": "Weekly positions",
        "lead": "After the script loads, the page updates prices, close status and history.",
        "fallback_h2": "Last stored week",
        "eur": "Position data is loaded from weekly files.",
        "spx": "Position data is loaded from weekly files.",
        "btc": "Position data is loaded from weekly files.",
        "legal": "Content is educational and analytical. It is not investment advice or financial advice. Do not make investment decisions based only on this page.",
        "back": "← Back to Investing",
    },
}


def html_page(c):
    return f'''<!doctype html><html lang="{c['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{c['title']}</title><meta name="description" content="{c['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v=quality-4"></head><body><div class="wrap"><header class="top"><a class="brand" href="{c['home']}"><span class="mark">BRs</span><span class="name">BriefRooms</span></a><nav class="nav"><a href="{c['invest']}">{c['nav1']}</a><a href="{c['portfolio']}">{c['nav_portfolio']}</a><a class="active" href="{c['weekly']}">{c['nav2']}</a><a href="{c['scenario']}">{c['nav3']}</a></nav><a class="lang" href="{c['switch']}">{c['switch_txt']}</a></header><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{c['h1']}</h1><p id="updated" class="lead">{c['lead']}</p></section><main id="app"><section class="panel"><h2>{c['fallback_h2']}</h2><div class="mini-grid"><div class="mini"><dt>EUR/USD</dt><dd class="neutral">—</dd><p>{c['eur']}</p></div><div class="mini"><dt>S&amp;P 500 futures</dt><dd class="neutral">—</dd><p>{c['spx']}</p></div><div class="mini"><dt>BTC/USD</dt><dd class="neutral">—</dd><p>{c['btc']}</p></div></div><p class="legal">{c['legal']}</p></section></main><a class="back" href="{c['invest']}">{c['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{c['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v=quality-4" defer></script></body></html>
'''


def main():
    for path, cfg in PAGES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_page(cfg), encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
