#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HEADER_VERSION = "20260719-1"
ASSET_VERSION = "governance-12"
TZ = ZoneInfo("Europe/Warsaw")

PAGES = {
    ROOT / "pl/inwestycje/pozycje-tygodniowe.html": {
        "lang": "pl", "title": "Otwarte pozycje tygodniowe — BriefRooms",
        "desc": "Otwarte pozycje paper-trading EUR/USD, S&P 500 futures i BTC/USD.",
        "invest": "/pl/inwestycje.html", "h1": "Otwarte pozycje tygodniowe",
        "lead": "Model utrzymuje pozycję LONG albo SHORT na każdym śledzonym instrumencie.",
        "current": "Bieżący tydzień", "open": "Cena otwarcia", "status": "Status",
        "sl": "Stop Loss", "tp": "Take Profit", "active": "w trakcie",
        "back": "← Wróć do Inwestycji",
        "legal": "Treści mają charakter edukacyjny i analityczny. Są to wyłącznie pozycje paper-trading, a nie rekomendacje ani rzeczywiste zlecenia."
    },
    ROOT / "en/investing/open-weekly-positions.html": {
        "lang": "en", "title": "Open weekly positions — BriefRooms",
        "desc": "Open EUR/USD, S&P 500 futures and BTC/USD paper-trading positions.",
        "invest": "/en/investing.html", "h1": "Open weekly positions",
        "lead": "The model maintains a LONG or SHORT position on every tracked instrument.",
        "current": "Current week", "open": "Entry price", "status": "Status",
        "sl": "Stop Loss", "tp": "Take Profit", "active": "in progress",
        "back": "← Back to Investing",
        "legal": "Content is educational and analytical. These are paper-trading positions only, not recommendations or real broker orders."
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


def current_week() -> dict:
    now = datetime.now(TZ)
    iso = now.isocalendar()
    path = ROOT / "data" / "investments" / "weekly" / f"{iso.year}-W{iso.week:02d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value, instrument_id: str) -> str:
    if value is None:
        return "—"
    digits = 5 if instrument_id == "eurusd" else 2
    return f"{float(value):,.{digits}f}".replace(",", " ")


def cards(week: dict, cfg: dict) -> str:
    rows = []
    for item in week.get("instruments", []):
        instrument_id = str(item.get("instrument_id") or "")
        label = item.get("label_pl" if cfg["lang"] == "pl" else "label_en") or item.get("symbol") or instrument_id
        direction = str(item.get("direction") or "neutral").upper()
        tone = "long" if direction == "LONG" else "short" if direction == "SHORT" else "neutral"
        risk = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
        status = cfg["active"] if item.get("trade_status") == "open" else str(item.get("trade_status") or "—")
        rows.append(
            f'<article class="card {tone}"><div class="head"><div><p>{html.escape(str(label))}</p>'
            f'<h3>{html.escape(direction)}</h3></div></div><dl class="grid">'
            f'<div class="cell"><dt>{cfg["open"]}</dt><dd>{fmt(item.get("entry_price"), instrument_id)}</dd></div>'
            f'<div class="cell"><dt>{cfg["sl"]}</dt><dd>{fmt(risk.get("stop_loss_price"), instrument_id)}</dd></div>'
            f'<div class="cell"><dt>{cfg["tp"]}</dt><dd>{fmt(risk.get("take_profit_price"), instrument_id)}</dd></div>'
            f'<div class="cell"><dt>{cfg["status"]}</dt><dd>{html.escape(status)}</dd></div>'
            f'</dl></article>'
        )
    return "".join(rows)


def page(cfg: dict, week: dict) -> str:
    rendered_cards = cards(week, cfg)
    return f'''<!doctype html><html lang="{cfg['lang']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{cfg['title']}</title><meta name="description" content="{cfg['desc']}"><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/investments-weekly-public.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/investments-weekly-governance.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/assets/site-header.css?v={HEADER_VERSION}"><script src="/scripts/site-header.js?v={HEADER_VERSION}" defer></script></head><body><header id="site-header"></header><div class="wrap"><section class="hero"><span class="pill">EUR/USD · S&amp;P 500 · BTC/USD</span><h1>{cfg['h1']}</h1><p id="updated" class="lead">{cfg['lead']}</p></section><main id="app"><section class="panel"><h2>{cfg['current']}: {html.escape(str(week.get('week_id') or ''))}</h2><div class="cards">{rendered_cards}</div></section><p class="legal">{cfg['legal']}</p></main><a class="back" href="{cfg['invest']}">{cfg['back']}</a></div><footer>© BriefRooms</footer><script>window.BR_WEEKLY={{lang:'{cfg['lang']}'}};</script><script src="/scripts/investments-weekly-public.js?v={ASSET_VERSION}" defer></script><script src="/scripts/investments-weekly-governance.js?v={ASSET_VERSION}" defer></script></body></html>\n'''


def update_room_links() -> None:
    for path, (old, new) in ROOM_LINKS.items():
        text = path.read_text(encoding="utf-8")
        updated = text.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8", newline="\n")
            print(f"updated weekly link in {path}")


def main() -> None:
    week = current_week()
    for path, cfg in PAGES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page(cfg, week), encoding="utf-8", newline="\n")
        print(f"wrote {path}")
    for alias, canonical in ALIASES.items():
        alias.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        print(f"updated alias {alias}")
    update_room_links()


if __name__ == "__main__":
    main()
