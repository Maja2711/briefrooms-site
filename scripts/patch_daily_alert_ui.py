#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "scripts" / "site-header.js"
PAGES = [ROOT / "pl/inwestycje.html", ROOT / "en/investing.html"]
VERSION = "20260723-4"

PANEL_RE = re.compile(r"\n\s*<aside class=\"model\">.*?</aside>\s*\n", re.S)
STATUS_SCRIPT_RE = re.compile(
    r"\n<script>\s*\n\(function\(\)\{\s*\n\s*const closedText=.*?</script>\s*\n",
    re.S,
)


def patch_js() -> bool:
    text = JS.read_text(encoding="utf-8")
    updated = text.replace(
        "var anchor = doc.querySelector('.model');",
        "var anchor = doc.querySelector('#daily-market-alert-anchor');",
    )
    if updated == text and "#daily-market-alert-anchor" not in text:
        raise RuntimeError("Daily alert mount selector was not found")
    if updated != text:
        JS.write_text(updated, encoding="utf-8")
        return True
    return False


def patch_page(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = text

    if 'id="daily-market-alert-anchor"' not in updated:
        updated, count = PANEL_RE.subn(
            '\n  <span id="daily-market-alert-anchor" hidden></span>\n',
            updated,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"Model direction panel not found in {path}")
    else:
        updated = PANEL_RE.sub("\n", updated, count=1)

    updated = STATUS_SCRIPT_RE.sub("\n", updated, count=1)
    updated = re.sub(
        r"/scripts/site-header\.js\?v=\d{8}-\d+",
        f"/scripts/site-header.js?v={VERSION}",
        updated,
    )

    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = patch_js()
    for page in PAGES:
        changed = patch_page(page) or changed
    print("Investing rooms simplified." if changed else "Investing rooms already simplified.")


if __name__ == "__main__":
    main()
