#!/usr/bin/env python3
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "render_weekly_public_pages.py"
FILES = [
    ROOT / "pl" / "inwestycje" / "prognozy-tygodniowe.html",
    ROOT / "en" / "investing" / "weekly-forecasts.html",
]
REMOVE_TEXTS = [
    '<p class="lead">Prosty ciemny widok. Wewnętrzna analiza nie jest pokazywana na stronie.</p>',
    '<p class="lead">Prosty widok pozycji tygodniowych. Ceny bieżące, wynik, SL/TP, zamknięcie i historia są pokazane bez wewnętrznych analiz modelu.</p>',
    '<p class="lead">Simple dark public view.</p>',
    '<p class="lead">Simple weekly-position view. Current prices, result, SL/TP, close and history are shown without internal model analysis.</p>',
]


def remove_helper_texts() -> None:
    for path in FILES:
        if not path.exists():
            continue
        html = path.read_text(encoding="utf-8")
        before = html
        for text in REMOVE_TEXTS:
            html = html.replace(text, "")
        if html != before:
            path.write_text(html, encoding="utf-8", newline="\n")
            print(f"removed weekly helper text from {path}")


if __name__ == "__main__":
    runpy.run_path(str(TARGET), run_name="__main__")
    remove_helper_texts()
