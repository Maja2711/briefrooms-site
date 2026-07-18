#!/usr/bin/env python3
"""Attach the shared light theme and X-sharing helper to all geopolitics books.

The migration is intentionally idempotent: running it repeatedly never duplicates
asset tags. Article copy and source links remain untouched.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = (
    "pl/geo/rosja-drony-paliwa-zboze.html",
    "pl/geo/polska-panstwo-frontowe.html",
    "pl/geo/ziemie-rzadkie.html",
    "pl/geo/usa-chiny-2025.html",
    "pl/geo/upadek-hegemona.html",
    "en/geo/russia-drones-fuel-grain.html",
    "en/geo/black-sea.html",
    "en/geo/rare-earths.html",
    "en/geo/falling-hegemon.html",
    "en/geo/usa-china-2025.html",
)

CSS_HREF = "/assets/geo-article-unified.css?v=1"
JS_SRC = "/assets/geo-article-unified.js?v=1"
CSS_TAG = f'  <link rel="stylesheet" href="{CSS_HREF}" />'
JS_TAG = f'  <script defer src="{JS_SRC}"></script>'


def insert_once(text: str, marker: str, tag: str, closing_tag: str) -> str:
    if marker in text:
        return text
    if closing_tag not in text:
        raise ValueError(f"Missing {closing_tag}")
    return text.replace(closing_tag, f"{tag}\n{closing_tag}", 1)


def update_page(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = insert_once(original, CSS_HREF, CSS_TAG, "</head>")
    updated = insert_once(updated, JS_SRC, JS_TAG, "</body>")

    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def validate() -> None:
    missing = [relative for relative in TARGETS if not (ROOT / relative).exists()]
    if missing:
        raise FileNotFoundError("Missing geopolitics pages: " + ", ".join(missing))

    for relative in TARGETS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        if text.count(CSS_HREF) != 1:
            raise AssertionError(f"{relative}: expected exactly one unified CSS link")
        if text.count(JS_SRC) != 1:
            raise AssertionError(f"{relative}: expected exactly one unified JS script")


if __name__ == "__main__":
    changed = []
    for relative in TARGETS:
        if update_page(ROOT / relative):
            changed.append(relative)
    validate()
    print(f"Unified {len(TARGETS)} geopolitics pages; changed {len(changed)} files.")
