#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATHS = [ROOT / "pl" / "index.html", ROOT / "en" / "index.html"]
ASSET_VERSION = 3
SCRIPT_RE = re.compile(r'<script\s+src="/scripts/home-weekly-top-position\.js\?v=\d+"\s+defer></script>', re.I)
SCRIPT_TAG = f'<script src="/scripts/home-weekly-top-position.js?v={ASSET_VERSION}" defer></script>'


def patch(source: str) -> str:
    if SCRIPT_RE.search(source):
        return SCRIPT_RE.sub(SCRIPT_TAG, source, count=1)
    body_end = source.lower().rfind("</body>")
    if body_end < 0:
        raise RuntimeError("Homepage has no </body> marker")
    return source[:body_end] + SCRIPT_TAG + "\n" + source[body_end:]


def validate(source: str) -> None:
    if SCRIPT_TAG not in source:
        raise RuntimeError(f"Missing current market signal script tag: {SCRIPT_TAG}")
    if source.count("home-weekly-top-position.js") != 1:
        raise RuntimeError("Homepage must load the market signal script exactly once")
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
        print("HOME_MARKET_SIGNAL_BAR_OK")
    else:
        print("Updated: " + (", ".join(changed) if changed else "already current"))


if __name__ == "__main__":
    main()
