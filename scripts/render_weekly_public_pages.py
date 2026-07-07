#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGES = {
    ROOT / "pl/inwestycje/prognozy-tygodniowe.html": {
        "lang": "pl",
        "title": "Otwarte pozycje tygodniowe | BriefRooms",
        "desc": "Prosty widok pozycji tygodniowych: EUR/USD, S&P 500 futures i BTC/USD.",
        "home": "/pl/",
        "invest": "/pl/inwestycje.html",
        "weekly": "/pl/inwestycje/prognozy-tygodniowe.html",
        "scenario": "/pl/inwestycje/spx-scenariusze-2026.html",
        "switch": "/en/investing/weekly-forecasts.html",
        "switch_txt": "EN",
        "nav1": "Inwestycje",
        "nav2": "Pozycje tygodniowe",
        "nav3": "Scenariusze S&P 500",
        "h1": "Otwarte pozycje tygodniowe",
        "lead": "Prosty widok pozycji tygodniowych. Ceny bieżące, wynik, SL/TP, zamknięcie i historia są pokazane bez wewnętrznych analiz modelu.",
        "loading": "Ładowanie pozycji…",
        "back": "← Wróć do Inwestycji",
    },
    ROOT / "en/investing/weekly-forecasts.html": {
        "lang": "en",
        "title": "Open weekly positions | BriefRooms",
        "desc": "Simple weekly-position view: EUR/USD, S&P 500 futures and BTC/USD.",
        "home": "/en/",
        "invest": "/en/investing.html",
        "weekly": "/en/investing/weekly-forecasts.html",
        "scenario": "/en/investing/spx-scenarios-2026.html",
        "switch": "/pl/inwestycje/prognozy-tygodniowe.html",
        "switch_txt": "PL",
        "nav1": "Investing",
        "nav2": "Weekly positions",
        "nav3": "S&P 500 scenarios",
        "h1": "Open weekly positions",
        "lead": "Simple weekly-position view. Current prices, result, SL/TP, close and history are shown without internal model analysis.",
        "loading": "Loading positions…",
        "back": "← Back to Investing",
    },
}


def html_page(c):
    return f'''<!doctype html><html lang="{c['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{c['title']}</title><meta name="description" content="{c['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v=restore-1"></head><body><div class="wrap"><header class="top"><a class="brand" href="{c['home']}"><span class="mark">BRs</span><span class="name">BriefRooms</span><span class="badge">AI-assisted</span></a><nav class="nav"><a href="{c['invest']}">{c['nav1']}</a><a class="active" href="{c['weekly']}">{c['nav2']}</a><a href="{c['scenario']}">{c['nav3']}</a></nav><a class="lang" href="{c['switch']}">{c['switch_txt']}</a></header><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{c['h1']}</h1><p class="lead">{c['lead']}</p><p id="updated" class="lead"></p></section><main id="app"><section class="panel empty">{c['loading']}</section></main><a class="back" href="{c['invest']}">{c['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{c['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v=restore-1" defer></script></body></html>\n'''


def main():
    for path, cfg in PAGES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_page(cfg), encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
