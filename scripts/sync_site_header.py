#!/usr/bin/env python3
"""Attach the shared site header to public PL/EN pages.

The script intentionally skips generated briefs, their dynamic redirect templates,
and explicit redirect-only pages. It is idempotent and preserves existing line endings.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "20260718-1"
STYLESHEET = f'<link rel="stylesheet" href="/assets/site-header.css?v={VERSION}" />'
SCRIPT = f'<script src="/scripts/site-header.js?v={VERSION}" defer></script>'
HOST = '<header id="site-header"></header>'
EXCLUDED = {
    Path("pl/brief.html"),
    Path("en/brief.html"),
    Path("en/geo/topic.html"),
}
EXCLUDED_DIRECTORIES = {"briefy", "briefs"}
HEADER_RE = re.compile(r"<header\b[^>]*>.*?</header>", re.IGNORECASE | re.DOTALL)
BODY_RE = re.compile(r"(<body\b[^>]*>)", re.IGNORECASE)


def public_pages() -> list[Path]:
    pages: list[Path] = []
    for language in ("pl", "en"):
        for path in (ROOT / language).rglob("*.html"):
            relative = path.relative_to(ROOT)
            if relative in EXCLUDED or EXCLUDED_DIRECTORIES.intersection(relative.parts):
                continue
            pages.append(path)
    return sorted(pages)


def strip_legacy_site_header(text: str) -> str:
    matches = list(HEADER_RE.finditer(text))
    for match in reversed(matches):
        block = match.group(0)
        opening = block[: block.find(">") + 1]
        class_match = re.search(r'class=["\']([^"\']*)["\']', opening, re.IGNORECASE)
        classes = set(class_match.group(1).split()) if class_match else set()
        is_legacy_nav = (
            "top" in classes
            and re.search(r"<nav\b", block, re.IGNORECASE)
            and re.search(r'href=["\']/(?:pl|en)/["\']', block, re.IGNORECASE)
        )
        if is_legacy_nav:
            text = text[: match.start()] + text[match.end() :]
    return text


def transform(text: str, newline: str) -> str:
    text = strip_legacy_site_header(text)

    if "/assets/site-header.css" not in text:
        text = re.sub(r"</head>", STYLESHEET + newline + SCRIPT + newline + "</head>", text, count=1, flags=re.IGNORECASE)
    elif "/scripts/site-header.js" not in text:
        text = re.sub(r"</head>", SCRIPT + newline + "</head>", text, count=1, flags=re.IGNORECASE)

    host_count = len(re.findall(r'id=["\']site-header["\']', text, re.IGNORECASE))
    if host_count == 0:
        text, replacements = BODY_RE.subn(r"\1" + newline + HOST, text, count=1)
        if replacements != 1:
            raise ValueError("missing <body> element")
    elif host_count != 1:
        raise ValueError(f"expected one site-header host, found {host_count}")

    return text


def update(path: Path, check: bool) -> bool:
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    original = raw.decode("utf-8")
    updated = transform(original, newline)
    changed = updated != original
    if changed and not check:
        path.write_bytes(updated.encode("utf-8"))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report pages that still need synchronization")
    args = parser.parse_args()

    changed: list[Path] = []
    for path in public_pages():
        if update(path, args.check):
            changed.append(path.relative_to(ROOT))

    for path in changed:
        print(path.as_posix())
    if args.check and changed:
        print(f"{len(changed)} page(s) require synchronization")
        return 1
    print(f"Synchronized {len(changed)} page(s); public scope: {len(public_pages())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
