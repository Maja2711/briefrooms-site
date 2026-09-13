#!/usr/bin/env python3
"""Keep Portfolio10K research-lab frontend assets installed in both languages.

The main Lab panel is injected by the shared navigation guard and Experiment
Registry script. Experience Store is an independent read-only frontend module,
loaded immediately after the navigation guard so it can attach a second view to
the existing Lab without rewriting the large generated Portfolio10K pages.
The Lab health layer is loaded after Experience Store and reports the real
loading/success/error state of the active Lab data source.
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
HEALTH_SRC = "/scripts/portfolio-10k-lab-health.js?v=1"
HEALTH_TAG = f'<script src="{HEALTH_SRC}" defer></script>'
NAV_TAG_PATTERN = re.compile(
    r'(<script\s+src="/scripts/portfolio-10k-navigation-guard\.js\?v=\d+"\s+defer></script>)'
)
EXPERIENCE_TAG_PATTERN = re.compile(
    r'(<script\s+src="/scripts/portfolio-10k-experience-store\.js\?v=\d+"\s+defer></script>)'
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

    if HEALTH_SRC not in updated:
        updated, health_count = EXPERIENCE_TAG_PATTERN.subn(rf"\1{HEALTH_TAG}", updated, count=1)
        if health_count != 1:
            raise RuntimeError(f"Could not install Lab health frontend after Experience Store in {path}")
    elif updated.count(HEALTH_SRC) != 1:
        raise RuntimeError(f"Expected exactly one Lab health frontend tag in {path}")

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
