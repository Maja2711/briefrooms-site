#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep article pages on the same approved comment source as homepage cards.

The server-side quality gate is the only editorial validator. The browser must
not count sentences or run a second language-quality test after a comment has
already received comment_quality_status=passed_*. The patch is intentionally
idempotent and supports both compact and multiline page scripts.
"""
from pathlib import Path
import re

CONFIG = {
    Path("pl/brief.html"): {
        "normalizer": "normalizeText",
        "fallback": "Ten wpis nie ma zatwierdzonego komentarza i nie powinien być widoczny jako karta BriefRooms.",
    },
    Path("en/brief.html"): {
        "normalizer": "removeBoilerplate",
        "fallback": "This item has no approved comment and should not be visible as a BriefRooms card.",
    },
}

APPROVED_RE = re.compile(r"function approvedComment\(item\)\{.*?\}", re.S)
BUILD_RE = re.compile(r"function buildSummary\(item\)\{.*?\}", re.S)

for path, cfg in CONFIG.items():
    text = path.read_text(encoding="utf-8")
    approved = (
        f"function approvedComment(item){{const text={cfg['normalizer']}(item.full_brief||item.details||'');"
        "return String(item.comment_quality_status||'').startsWith('passed_')&&text?text:'';}"
    )
    build = "function buildSummary(item){return approvedComment(item);}"

    if APPROVED_RE.search(text):
        text = APPROVED_RE.sub(approved, text, count=1)
    else:
        # Older templates may have only buildSummary. Insert the approved source
        # immediately before it, without depending on an exact legacy string.
        match = BUILD_RE.search(text)
        if match:
            text = text[:match.start()] + approved + "\n" + text[match.start():]
        else:
            raise SystemExit(f"approvedComment/buildSummary not found in {path}")

    if BUILD_RE.search(text):
        text = BUILD_RE.sub(build, text, count=1)

    # A page may call approvedComment directly (PL) or through buildSummary (EN).
    if "approvedComment(item)" not in text:
        raise SystemExit(f"approvedComment is not used in {path}")
    if "comment_quality_status" not in text or "full_brief||item.details" not in text:
        raise SystemExit(f"approved comment contract missing in {path}")

    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{path}: approved comment source enforced")
