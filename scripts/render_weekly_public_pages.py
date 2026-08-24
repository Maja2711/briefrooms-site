#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HEADER_VERSION = "20260719-1"
ASSET_VERSION = "weekly-19"
TZ = ZoneInfo("Europe/Warsaw")

PAGES = {
    ROOT / "pl/inwestycje/pozycje-tygodniowe.html": {
        "lang": "pl",
        "title": "Weekly Positions — BriefRooms",
        "desc": "Pozycje EUR/USD, S&P 500 futures i BTC/USD wraz z historią wyników.",
        "invest": "/pl/inwestycje.html",
        "daily": "/pl/inwestycje/daily-trading.html",
        "weekly": "/pl/inwestycje/pozycje-tygodniowe.html",
        "h1": "Weekly Positions",
        "lead": "Ładowanie danych pozycji tygodniowych i historii wyników…",
        "loading": "Ładowanie tygodni…",
        "back": "← Wróć do Daily Trading",
        "daily_label": "Daily Trading",
        "weekly_label": "Weekly Positions",
        "legal": "Treści mają charakter edukacyjny i analityczny. Nie stanowią rekomendacji ani rzeczywistych zleceń.",
    },
    ROOT / "en/investing/open-weekly-positions.html": {
        "lang": "en",
        "title": "Weekly Positions — BriefRooms",
        "desc": "EUR/USD, S&P 500 futures and BTC/USD positions with result history.",
        "invest": "/en/investing.html",
        "daily": "/en/investing/daily-trading.html",
        "weekly": "/en/investing/open-weekly-positions.html",
        "h1": "Weekly Positions",
        "lead": "Loading weekly positions and result history…",
        "loading": "Loading weeks…",
        "back": "← Back to Daily Trading",
        "daily_label": "Daily Trading",
        "weekly_label": "Weekly Positions",
        "legal": "Content is educational and analytical. It is not a recommendation or a real broker order.",
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
    return f'''<!doctype html><html lang="{cfg['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{cfg['title']}</title><meta name="description" content="{cfg['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/investments-weekly-governance.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/investments-hub.css?v=2"><link rel="stylesheet" href="/assets/site-header.css?v={HEADER_VERSION}"><script src="/scripts/site-header.js?v={HEADER_VERSION}" defer></script></head><body><header id="site-header"></header><div class="wrap"><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{cfg['h1']}</h1><p id="updated" class="lead">{cfg['lead']}</p></section><nav class="switcher" aria-label="Trading horizon"><a class="switch-daily" href="{cfg['daily']}">{cfg['daily_label']}</a><a class="switch-weekly active" aria-current="page" href="{cfg['weekly']}">{cfg['weekly_label']}</a></nav><main id="app"><section class="panel"><h2>{shell_week_id()}</h2><p>{cfg['loading']}</p></section><p class="legal">{cfg['legal']}</p></main><a class="back" href="{cfg['daily']}">{cfg['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{cfg['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v={ASSET_VERSION}" defer></script><script src="/scripts/investments-weekly-trade-times.js?v=1" defer></script><script src="/scripts/investments-weekly-governance.js?v={ASSET_VERSION}" defer></script><script src="/scripts/investments-wes-public.js?v={ASSET_VERSION}" defer></script></body></html>\n'''


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
