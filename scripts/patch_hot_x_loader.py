#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ensure PL/EN homepages load exactly one resilient Hot X renderer."""
from pathlib import Path
import re

FILES = [Path("pl/index.html"), Path("en/index.html")]
NEW = "/scripts/hot-x-render.js?v=resilient-comments-3"
SCRIPT_RE = re.compile(r'\s*<script\s+src=["\']/scripts/hot-x-render\.js\?v=[^"\']+["\']\s+defer></script>\s*', re.I)

for path in FILES:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    cleaned = SCRIPT_RE.sub("\n", text)
    if "</body>" not in cleaned:
        raise SystemExit(f"Missing </body> in {path}")
    desired = cleaned.replace("</body>", f'<script src="{NEW}" defer></script>\n</body>', 1)
    if desired != text:
        path.write_text(desired, encoding="utf-8", newline="\n")
        print(f"patched and deduplicated {path}")
    else:
        print(f"{path}: renderer already correct")
