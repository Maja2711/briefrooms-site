#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER_VERSION = "20260719-1"

PAGES = {
    ROOT / "pl/inwestycje/prognozy-tygodniowe.html": {
        "lang": "pl",
        "title": "Pozycje tygodniowe — BriefRooms",
        "desc": "Pozycje tygodniowe i codzienna analiza: EUR/USD, S&P 500 futures i BTC/USD.",
        "invest": "/pl/inwestycje.html",
        "h1": "Pozycje tygodniowe",
        "lead": "Strona pokazuje trzy pozycje paper trading, ceny, wynik, historię i najnowszą codzienną analizę każdej pozycji.",
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
        "desc": "Weekly positions and daily analysis: EUR/USD, S&P 500 futures and BTC/USD.",
        "invest": "/en/investing.html",
        "h1": "Weekly positions",
        "lead": "The page shows three paper positions, prices, results, history and the latest daily analysis for each position.",
        "fallback_h2": "Last stored week",
        "eur": "Position data is loaded from weekly files.",
        "spx": "Position data is loaded from weekly files.",
        "btc": "Position data is loaded from weekly files.",
        "legal": "Content is educational and analytical. It is not investment advice or financial advice. Do not make investment decisions based only on this page.",
        "back": "← Back to Investing",
    },
}


def html_page(c):
    return f'''<!doctype html><html lang="{c['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{c['title']}</title><meta name="description" content="{c['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v=quality-6"><link rel="stylesheet" href="/assets/site-header.css?v={HEADER_VERSION}" /><script src="/scripts/site-header.js?v={HEADER_VERSION}" defer></script></head><body><header id="site-header"></header><div class="wrap"><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{c['h1']}</h1><p id="updated" class="lead">{c['lead']}</p></section><main id="app"><section class="panel"><h2>{c['fallback_h2']}</h2><div class="mini-grid"><div class="mini"><dt>EUR/USD</dt><dd class="neutral">—</dd><p>{c['eur']}</p></div><div class="mini"><dt>S&amp;P 500 futures</dt><dd class="neutral">—</dd><p>{c['spx']}</p></div><div class="mini"><dt>BTC/USD</dt><dd class="neutral">—</dd><p>{c['btc']}</p></div></div><p class="legal">{c['legal']}</p></section></main><a class="back" href="{c['invest']}">{c['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{c['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v=quality-6" defer></script></body></html>
'''


def main():
    for path, cfg in PAGES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_page(cfg), encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
