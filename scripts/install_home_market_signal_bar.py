#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATHS = [ROOT / "pl" / "index.html", ROOT / "en" / "index.html"]
ASSET_VERSION = 6
ASSET_PATH = "/scripts/home-market-signal-v6.js"
SCRIPT_RE = re.compile(
    r'<script\s+src="/scripts/(?:home-weekly-top-position\.js\?v=\d+|home-market-signal-v6\.js)"\s+defer></script>',
    re.I,
)
SCRIPT_TAG = f'<script src="{ASSET_PATH}" defer></script>'
LEGACY_LOCALE_LINK_RE = re.compile(
    r'<script\s+id="home-market-signal-locale-link">.*?</script>',
    re.I | re.S,
)

def patch(source: str) -> str:
    if SCRIPT_RE.search(source):
        source = SCRIPT_RE.sub(SCRIPT_TAG, source, count=1)
    else:
        body_end = source.lower().rfind("</body>")
        if body_end < 0:
            raise RuntimeError("Homepage has no </body> marker")
        source = source[:body_end] + SCRIPT_TAG + "\n" + source[body_end:]
    return LEGACY_LOCALE_LINK_RE.sub("", source)

def validate(source: str) -> None:
    if SCRIPT_TAG not in source:
        raise RuntimeError(f"Missing current market signal script tag: {SCRIPT_TAG}")
    if source.count("home-market-signal-v6.js") != 1:
        raise RuntimeError("Homepage must load the v6 market signal script exactly once")
    if "home-weekly-top-position.js" in source:
        raise RuntimeError("Legacy market signal asset must not be loaded")
    if 'id="home-market-signal-locale-link"' in source:
        raise RuntimeError("Legacy Daily Trade locale-link guard must be removed")
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
    print("HOME_MARKET_SIGNAL_BAR_OK" if args.check else "Updated: " + (", ".join(changed) if changed else "already current"))

if __name__ == "__main__":
    main()
