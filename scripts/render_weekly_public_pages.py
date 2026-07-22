#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER_VERSION = "20260719-1"
ASSET_VERSION = "governance-9"

PAGES = {
    ROOT / "pl/inwestycje/prognozy-tygodniowe.html": {
        "lang": "pl", "title": "Pozycje tygodniowe — BriefRooms",
        "desc": "Weekly model positions with shared validation, no backdated entries and thesis-invalidation re-entry locks.",
        "invest": "/pl/inwestycje.html", "h1": "Pozycje tygodniowe",
        "lead": "Model v5 stosuje wspólną walidację sygnałów, zakazuje wejść wstecznych i blokuje ponowne wejście po unieważnieniu tezy lub zdarzeniu materialnym.",
        "fallback_h2": "Ostatnio zapisany tydzień", "back": "← Wróć do Inwestycji",
        "legal": "Treści mają charakter edukacyjny i analityczny. To nie jest rekomendacja inwestycyjna ani porada finansowa. Wyniki modelu nie gwarantują przyszłych rezultatów."
    },
    ROOT / "en/investing/weekly-forecasts.html": {
        "lang": "en", "title": "Weekly positions — BriefRooms",
        "desc": "Weekly model positions with shared validation, no backdated entries and thesis-invalidation re-entry locks.",
        "invest": "/en/investing.html", "h1": "Weekly positions",
        "lead": "Model v5 applies shared signal validation, forbids backdated entries and blocks re-entry after thesis invalidation or a material event.",
        "fallback_h2": "Last stored week", "back": "← Back to Investing",
        "legal": "Content is educational and analytical. It is not investment advice or financial advice. Model results do not guarantee future performance."
    },
}


def page(c):
    loaded = "Dane pozycji są ładowane z plików tygodniowych." if c["lang"] == "pl" else "Position data is loaded from weekly files."
    return f'''<!doctype html><html lang="{c['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{c['title']}</title><meta name="description" content="{c['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/investments-weekly-governance.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/site-header.css?v={HEADER_VERSION}"><script src="/scripts/site-header.js?v={HEADER_VERSION}" defer></script></head><body><header id="site-header"></header><div class="wrap"><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{c['h1']}</h1><p id="updated" class="lead">{c['lead']}</p></section><main id="app"><section class="panel"><h2>{c['fallback_h2']}</h2><div class="mini-grid"><div class="mini"><dt>EUR/USD</dt><dd class="neutral">—</dd><p>{loaded}</p></div><div class="mini"><dt>S&amp;P 500 futures</dt><dd class="neutral">—</dd><p>{loaded}</p></div><div class="mini"><dt>BTC/USD</dt><dd class="neutral">—</dd><p>{loaded}</p></div></div><p class="legal">{c['legal']}</p></section></main><a class="back" href="{c['invest']}">{c['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{c['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v={ASSET_VERSION}" defer></script><script src="/scripts/investments-weekly-governance.js?v={ASSET_VERSION}" defer></script></body></html>\n'''


def main():
    for path, cfg in PAGES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page(cfg), encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
