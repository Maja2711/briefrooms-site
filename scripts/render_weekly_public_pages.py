#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HEADER_VERSION = "20260719-1"
ASSET_VERSION = "weekly-18"
TZ = ZoneInfo("Europe/Warsaw")

PAGES = {
    ROOT / "pl/inwestycje/pozycje-tygodniowe.html": {
        "lang": "pl",
        "title": "Otwarte pozycje tygodniowe — BriefRooms",
        "desc": "Pozycje paper-trading EUR/USD, S&P 500 futures i BTC/USD wraz z historią wyników.",
        "invest": "/pl/inwestycje.html",
        "h1": "Otwarte pozycje tygodniowe",
        "lead": "Ładowanie danych pozycji tygodniowych i historii wyników…",
        "loading": "Ładowanie tygodni…",
        "back": "← Wróć do Inwestycji",
        "legal": "Treści mają charakter edukacyjny i analityczny. Są to wyłącznie pozycje paper-trading, a nie rekomendacje ani rzeczywiste zlecenia.",
    },
    ROOT / "en/investing/open-weekly-positions.html": {
        "lang": "en",
        "title": "Open weekly positions — BriefRooms",
        "desc": "EUR/USD, S&P 500 futures and BTC/USD paper-trading positions with result history.",
        "invest": "/en/investing.html",
        "h1": "Open weekly positions",
        "lead": "Loading weekly positions and result history…",
        "loading": "Loading weeks…",
        "back": "← Back to Investing",
        "legal": "Content is educational and analytical. These are paper-trading positions only, not recommendations or real broker orders.",
    },
}

ALIASES = {
    ROOT / "pl/inwestycje/prognozy-tygodniowe.html": ROOT / "pl/inwestycje/pozycje-tygodniowe.html",
    ROOT / "en/investing/weekly-forecasts.html": ROOT / "en/investing/open-weekly-positions.html",
}

ROOM_LINKS = {
    ROOT / "pl/inwestycje.html": ("/pl/inwestycje/prognozy-tygodniowe.html", "/pl/inwestycje/pozycje-tygodniowe.html"),
    ROOT / "en/investing.html": ("/en/investing/weekly-forecasts.html", "/en/investing/open-weekly-positions.html"),
}


def shell_week_id() -> str:
    now = datetime.now(TZ)
    if now.weekday() >= 4:
        now += timedelta(days=7)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def page(cfg: dict) -> str:
    return f'''<!doctype html><html lang="{cfg['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{cfg['title']}</title><meta name="description" content="{cfg['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/investments-weekly-governance.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/site-header.css?v={HEADER_VERSION}"><script src="/scripts/site-header.js?v={HEADER_VERSION}" defer></script></head><body><header id="site-header"></header><div class="wrap"><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{cfg['h1']}</h1><p id="updated" class="lead">{cfg['lead']}</p></section><main id="app"><section class="panel"><h2>{shell_week_id()}</h2><p>{cfg['loading']}</p></section><p class="legal">{cfg['legal']}</p></main><a class="back" href="{cfg['invest']}">{cfg['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{cfg['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v={ASSET_VERSION}" defer></script><script src="/scripts/investments-weekly-trade-times.js?v=1" defer></script><script src="/scripts/investments-weekly-governance.js?v={ASSET_VERSION}" defer></script><script src="/scripts/investments-wes-public.js?v={ASSET_VERSION}" defer></script></body></html>\n'''


def update_room_links() -> None:
    for path, (old, new) in ROOM_LINKS.items():
        text = path.read_text(encoding="utf-8")
        updated = text.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8", newline="\n")
            print(f"updated weekly link in {path}")


def main() -> None:
    for path, cfg in PAGES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page(cfg), encoding="utf-8", newline="\n")
        print(f"wrote {path}")
    for alias, canonical in ALIASES.items():
        alias.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        print(f"updated alias {alias}")
    update_room_links()


if __name__ == "__main__":
    main()
