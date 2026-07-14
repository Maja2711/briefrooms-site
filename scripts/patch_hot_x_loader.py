#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ensure PL/EN homepages load the resilient Hot X comment renderer."""
from pathlib import Path

FILES = [Path("pl/index.html"), Path("en/index.html")]
OLD_VERSIONS = [
    "/scripts/hot-x-render.js?v=exact-x-1",
    "/scripts/hot-x-render.js?v=resilient-x-2",
]
NEW = "/scripts/hot-x-render.js?v=resilient-comments-3"

for path in FILES:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    changed = False
    for old in OLD_VERSIONS:
        if old in text:
            text = text.replace(old, NEW)
            changed = True
    if NEW not in text and "</body>" in text:
        text = text.replace("</body>", f'<script src="{NEW}" defer></script>\n</body>')
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"patched {path}")
