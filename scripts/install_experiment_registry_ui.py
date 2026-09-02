#!/usr/bin/env python3
"""Keep Portfolio10K research-lab frontend assets installed in both languages.

The main Lab panel is injected by the shared navigation guard and Experiment
Registry script. Experience Store is an independent read-only frontend module,
loaded immediately after the navigation guard so it can attach a second view to
the existing Lab without rewriting the large generated Portfolio10K pages.
"""
from __future__ import annotations

import re
from pathlib import Path

PAGES = (
    Path("pl/inwestycje/portfel-10k.html"),
    Path("en/investing/portfolio-10k.html"),
)
TARGET = "portfolio-10k-navigation-guard.js?v=7"
PATTERN = re.compile(r"portfolio-10k-navigation-guard\.js\?v=\d+")
EXPERIENCE_SRC = "/scripts/portfolio-10k-experience-store.js?v=1"
EXPERIENCE_TAG = f'<script src="{EXPERIENCE_SRC}" defer></script>'
NAV_TAG_PATTERN = re.compile(
    r'(<script\s+src="/scripts/portfolio-10k-navigation-guard\.js\?v=\d+"\s+defer></script>)'
)


def update_page(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "portfolio-10k-navigation-guard.js" not in text:
        raise RuntimeError(f"Navigation guard script missing from {path}")
    updated, count = PATTERN.subn(TARGET, text)
    if count != 1:
        raise RuntimeError(f"Expected exactly one navigation guard cache key in {path}, found {count}")

    if EXPERIENCE_SRC not in updated:
        updated, tag_count = NAV_TAG_PATTERN.subn(rf"\1{EXPERIENCE_TAG}", updated, count=1)
        if tag_count != 1:
            raise RuntimeError(f"Could not install Experience Store frontend after navigation guard in {path}")
    elif updated.count(EXPERIENCE_SRC) != 1:
        raise RuntimeError(f"Expected exactly one Experience Store frontend tag in {path}")

    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    changed = []
    for path in PAGES:
        if update_page(path):
            changed.append(str(path))
    print("Research Lab UI assets current:", ", ".join(changed) if changed else "no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
