#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME_PATHS = [ROOT / "pl" / "index.html", ROOT / "en" / "index.html"]

ASSET_TAG = '<script src="/scripts/home-market-signal-v6.js?v=7" defer></script>'
HOME_BRIEFS_TAG = '<script src="/scripts/home-briefs.js?v=seo-static-1" defer></script>'

MARKET_SIGNAL_RE = re.compile(
    r'\s*<script\s+src="/scripts/(?:'
    r'home-market-signal-v6\.js(?:\?[^"]*)?|'
    r'home-market-signal-guard-v1\.js|'
    r'home-weekly-top-position\.js\?v=\d+'
    r')"\s+defer></script>',
    re.I,
)

def patch(source: str) -> str:
    # Remove every old/duplicate market-signal script and the emergency marker.
    source = MARKET_SIGNAL_RE.sub("", source)
    source = source.replace("<!-- MARKET_SIGNAL_DISABLED_INTERACTION_HOTFIX -->", "")

    # Repair only the literal escape sequences introduced by the emergency hotfix.
    source = source.replace(
        '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\\n'
        '<meta http-equiv="Pragma" content="no-cache">\\n'
        '<meta http-equiv="Expires" content="0">\\n</head>',
        '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\n'
        '<meta http-equiv="Pragma" content="no-cache">\n'
        '<meta http-equiv="Expires" content="0">\n</head>',
    )
    source = source.replace(HOME_BRIEFS_TAG + "\\n", HOME_BRIEFS_TAG + "\n")

    if HOME_BRIEFS_TAG not in source:
        raise RuntimeError("Homepage home-briefs anchor is missing")

    source = source.replace(HOME_BRIEFS_TAG, HOME_BRIEFS_TAG + "\n" + ASSET_TAG, 1)
    return source

def validate(source: str) -> None:
    if source.count("home-market-signal-v6.js?v=7") != 1:
        raise RuntimeError("Homepage must load the passive market signal renderer exactly once")
    if "home-market-signal-guard-v1.js" in source:
        raise RuntimeError("Blocking DOM guard must not be loaded")
    if "home-weekly-top-position.js" in source:
        raise RuntimeError("Legacy renderer must not be loaded")
    if "MARKET_SIGNAL_DISABLED_INTERACTION_HOTFIX" in source:
        raise RuntimeError("Emergency disable marker must not remain")
    if 'must-revalidate">\\n<meta' in source or '</script>\\n' in source:
        raise RuntimeError("Visible literal newline escapes remain in homepage HTML")
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

    print("HOME_MARKET_SIGNAL_OK" if args.check else "Updated: " + (", ".join(changed) if changed else "already current"))

if __name__ == "__main__":
    main()
