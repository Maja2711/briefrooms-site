#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "pl" / "aktualnosci.html",
    ROOT / "en" / "news.html",
    ROOT / "pl" / "index.html",
    ROOT / "en" / "index.html",
]
VERSION = "2"
TAG = f'<script src="/scripts/news-live.js?v={VERSION}" defer></script>'
PATTERN = re.compile(r'\s*<script\s+src=["\']/scripts/news-live\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)


def install(path: Path) -> bool:
    old = path.read_text(encoding="utf-8")
    new = PATTERN.sub("", old)
    if "</body>" not in new:
        raise RuntimeError(f"closing body tag missing: {path.relative_to(ROOT)}")
    new = new.replace("</body>", TAG + "\n</body>", 1)
    if new == old:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    print(f"installed live news runtime in {path.relative_to(ROOT)}")
    return True


def main() -> None:
    for path in PAGES:
        install(path)


if __name__ == "__main__":
    main()
