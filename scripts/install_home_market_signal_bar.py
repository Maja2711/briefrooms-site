#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATHS = [ROOT / "pl" / "index.html", ROOT / "en" / "index.html"]

MARKER = "<!-- MARKET_SIGNAL_DISABLED_INTERACTION_HOTFIX -->"

SIGNAL_TAG_RE = re.compile(
    r'\s*<script\s+src="/scripts/(?:home-market-signal-v6\.js|home-market-signal-guard-v1\.js|home-weekly-top-position\.js\?v=\d+)"\s+defer></script>',
    re.I,
)

def patch(source: str) -> str:
    source = SIGNAL_TAG_RE.sub("", source)
    if MARKER not in source:
        anchor = '<script src="/scripts/home-briefs.js?v=seo-static-1" defer></script>'
        if anchor not in source:
            raise RuntimeError("Homepage home-briefs anchor is missing")
        source = source.replace(anchor, anchor + "\n" + MARKER, 1)
    return source

def validate(source: str) -> None:
    for forbidden in (
        "home-market-signal-v6.js",
        "home-market-signal-guard-v1.js",
        "home-weekly-top-position.js",
    ):
        if forbidden in source:
            raise RuntimeError(f"Disabled market-signal asset is still loaded: {forbidden}")
    if MARKER not in source:
        raise RuntimeError("Market-signal emergency-disable marker is missing")
    if "br-share-strip" not in source:
        raise RuntimeError("Homepage share bar anchor is missing")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed: list[str] = []

    for path in HOME_PATHS:
        source = path.read_text(encoding="utf-8")
        if args.check:
            validate(source)
            continue
        updated = patch(source)
        validate(updated)
        if updated != source:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed.append(str(path.relative_to(ROOT)))

    if args.check:
        print("HOME_MARKET_SIGNAL_DISABLED_OK")
    else:
        print("Updated: " + (", ".join(changed) if changed else "already disabled"))

if __name__ == "__main__":
    main()
