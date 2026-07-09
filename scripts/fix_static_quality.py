#!/usr/bin/env python3
"""Static quality guard for generated BriefRooms pages.

Runs after manual fixes or automated generators. It removes generic AI boilerplate
from already-generated news HTML, makes home pages readable without JS, and keeps
Hot X links clearly described as X source links.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, html: str) -> None:
    (ROOT / path).write_text(html, encoding="utf-8", newline="\n")


def strip_generic_news(path: str, why_label: str) -> None:
    p = ROOT / path
    if not p.exists():
        return
    html = p.read_text(encoding="utf-8")
    before = html
    html = re.sub(
        rf'\n\s*<div class="sec"><strong>{re.escape(why_label)}:</strong>.*?</div>',
        '',
        html,
        flags=re.I | re.S,
    )
    html = re.sub(r'(<strong>Warning:</strong>)\s*(?:Warning:\s*)+', r'\1 ', html, flags=re.I)
    html = re.sub(r'(<strong>Uwaga:</strong>)\s*(?:Uwaga|Ostrożnie)\s*:\s*', r'\1 ', html, flags=re.I)
    if html != before:
        write(path, html)
        print(f"cleaned generic news comments in {path}")


def fix_home_en() -> None:
    path = "en/index.html"
    p = ROOT / path
    if not p.exists():
        return
    html = p.read_text(encoding="utf-8")
    before = html
    fallback = '''<a class="brief-card" href="/en/news.html"><div class="thumb"><div class="fallback-art">BR</div><span class="tag">News</span></div><div class="brief-body"><h3 class="brief-title">Latest briefs are refreshed automatically</h3><p class="brief-desc">Open the News room for the current source-linked briefs. The cards update when home_brief.json loads.</p><span class="brief-source"><b>BriefRooms</b><span class="brief-link">Open news →</span></span></div></a>'''
    html = html.replace('Update: loading...', 'Update: latest briefs')
    html = html.replace('<div id="latest-briefs" class="brief-grid"></div>', f'<div id="latest-briefs" class="brief-grid">{fallback}</div>')
    if html != before:
        write(path, html)
        print(f"fixed no-JS fallback in {path}")


def fix_home_pl() -> None:
    path = "pl/index.html"
    p = ROOT / path
    if not p.exists():
        return
    html = p.read_text(encoding="utf-8")
    before = html
    fallback = '''<a class="brief-card" href="/pl/aktualnosci.html"><div class="thumb"><div class="fallback-art">BR</div><span class="tag">Aktualności</span></div><div class="brief-body"><h3 class="brief-title">Najnowsze briefy odświeżają się automatycznie</h3><p class="brief-desc">Otwórz pokój Aktualności, żeby zobaczyć aktualne briefy z linkami do źródeł. Karty uzupełnią się po wczytaniu home_brief.json.</p><span class="brief-source"><b>BriefRooms</b><span class="brief-link">Otwórz aktualności →</span></span></div></a>'''
    html = html.replace('Aktualizacja: ładowanie…', 'Aktualizacja: najnowsze briefy')
    html = html.replace('<div id="latest-briefs" class="brief-grid"></div>', f'<div id="latest-briefs" class="brief-grid">{fallback}</div>')
    html = html.replace('Otwórz na X →', 'Źródło na X →')
    if html != before:
        write(path, html)
        print(f"fixed no-JS/hot-x labels in {path}")


def main() -> None:
    strip_generic_news("en/news.html", "Why it matters")
    strip_generic_news("pl/aktualnosci.html", "Dlaczego to ważne")
    fix_home_en()
    fix_home_pl()


if __name__ == "__main__":
    main()
