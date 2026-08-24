#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATHS = {
    "pl": ROOT / "pl" / "index.html",
    "en": ROOT / "en" / "index.html",
}

SIDE_RE = re.compile(r'<aside\s+class=["\']side["\'][^>]*>[\s\S]*?</aside>', re.I)
YOUTUBE_RE = re.compile(r'<section\s+class=["\']youtube-picks["\'][^>]*>[\s\S]*?</section>', re.I)
HOT_X_SCRIPT_RE = re.compile(r'\s*<script\s+src=["\']/scripts/hot-x-render\.js[^"\']*["\'][^>]*></script>\s*', re.I)
LAB_CSS_RE = re.compile(r'<link\s+rel=["\']stylesheet["\']\s+href=["\']/assets/home-lab\.css[^"\']*["\'][^>]*>', re.I)
LAB_SCRIPT_RE = re.compile(r'<script\s+src=["\']/scripts/home-lab\.js[^"\']*["\'][^>]*></script>', re.I)

COPY = {
    "pl": {
        "title": "BriefRooms Lab",
        "desc": "Badania, uczenie i rozwój silników BriefRooms — wyniki, postęp i status przeglądów.",
        "loading": "Ładowanie wyników badań…",
    },
    "en": {
        "title": "BriefRooms Lab",
        "desc": "Research, learning and engine development at BriefRooms — results, progress and review status.",
        "loading": "Loading research results…",
    },
}


def lab_section(lang: str) -> str:
    c = COPY[lang]
    return (
        '<!-- BR_HOME_LAB_START -->\n'
        '<section class="home-lab" aria-labelledby="home-lab-title">'
        '<div class="home-lab__head">'
        '<span class="home-lab__eyebrow">Research status</span>'
        f'<h2 id="home-lab-title">{c["title"]}</h2>'
        f'<p>{c["desc"]}</p>'
        '</div>'
        '<div id="home-lab-root" aria-live="polite">'
        '<div class="home-lab__cards" aria-hidden="true">'
        '<div class="home-lab__skeleton"></div>'
        '<div class="home-lab__skeleton"></div>'
        '<div class="home-lab__skeleton"></div>'
        '</div>'
        f'<span class="sr-only">{c["loading"]}</span>'
        '</div>'
        '</section>\n'
        '<!-- BR_HOME_LAB_END -->'
    )


def patch_homepage(path: Path, lang: str) -> bool:
    source = path.read_text(encoding="utf-8")
    side = SIDE_RE.search(source)
    if not side:
        raise RuntimeError(f"Could not locate sidebar in {path}")
    youtube = YOUTUBE_RE.search(side.group(0))
    youtube_html = youtube.group(0) if youtube else ""
    replacement = '<aside class="side">' + lab_section(lang)
    if youtube_html:
        replacement += "\n" + youtube_html
    replacement += '</aside>'
    updated = source[: side.start()] + replacement + source[side.end() :]

    updated = HOT_X_SCRIPT_RE.sub("\n", updated)

    if not LAB_CSS_RE.search(updated):
        marker = '</head>'
        if marker not in updated:
            raise RuntimeError(f"Missing </head> in {path}")
        updated = updated.replace(marker, '<link rel="stylesheet" href="/assets/home-lab.css?v=1">\n' + marker, 1)

    if not LAB_SCRIPT_RE.search(updated):
        marker = '</body>'
        if marker not in updated:
            raise RuntimeError(f"Missing </body> in {path}")
        updated = updated.replace(marker, '<script src="/scripts/home-lab.js?v=1" defer></script>\n' + marker, 1)

    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def check() -> None:
    for lang, path in HOME_PATHS.items():
        source = path.read_text(encoding="utf-8")
        required = (
            '<!-- BR_HOME_LAB_START -->',
            '<!-- BR_HOME_LAB_END -->',
            'id="home-lab-root"',
            '/assets/home-lab.css?v=1',
            '/scripts/home-lab.js?v=1',
        )
        for marker in required:
            if marker not in source:
                raise RuntimeError(f"Missing {marker!r} in {path}")
        forbidden = ('<!-- HOT_X_STATIC_START -->', '/scripts/hot-x-render.js', 'Co krąży w X', "What's circulating on X")
        for marker in forbidden:
            if marker in source:
                raise RuntimeError(f"Legacy Hot X marker still present in {path}: {marker!r}")
        if source.count('id="home-lab-root"') != 1:
            raise RuntimeError(f"Expected exactly one Lab root in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace legacy Hot X homepage sidebar with BriefRooms Lab")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
        print("BriefRooms Lab homepage check passed")
        return
    changed = []
    for lang, path in HOME_PATHS.items():
        if patch_homepage(path, lang):
            changed.append(lang)
    check()
    print("BriefRooms Lab synchronized: " + (", ".join(changed) if changed else "already current"))


if __name__ == "__main__":
    main()
