#!/usr/bin/env python3
"""Canonical finalizer for Portfolio 10K public HTML assets.

Every automation that rewrites PL/EN Portfolio 10K HTML must finish with this
module.  It normalizes cache keys and removes duplicate protected scripts so
independent generators cannot roll each other back.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    Path("pl/inwestycje/portfel-10k.html"),
    Path("en/investing/portfolio-10k.html"),
)

CANONICAL_VERSIONS = {
    "portfolio-10k-dashboard.js": "10",
    "portfolio-10k-dashboard-en.js": "10",
    "portfolio-10k-experience-store.js": "1",
    "portfolio-10k-lab-health.js": "3",
    "ai-tournament-public.js": "6",
    "ai-tournament-readiness.js": "6",
    "ai-tournament-company-profiles.js": "2",
    "ai-tournament-summary.js": "2",
    "portfolio-10k-navigation-guard.js": "8",
}
REQUIRED_SHARED = (
    "portfolio-10k-experience-store.js",
    "portfolio-10k-lab-health.js",
    "ai-tournament-public.js",
    "ai-tournament-readiness.js",
    "ai-tournament-company-profiles.js",
    "ai-tournament-summary.js",
    "portfolio-10k-navigation-guard.js",
)


def script_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf'<script\s+src=["\']/scripts/{re.escape(name)}(?:\?[^"\']*)?["\']\s+defer></script>',
        re.I,
    )


def canonical_tag(name: str) -> str:
    version = CANONICAL_VERSIONS[name]
    return f'<script src="/scripts/{name}?v={version}" defer></script>'


def _normalize_one(source: str, name: str, *, required: bool) -> str:
    pattern = script_pattern(name)
    matches = list(pattern.finditer(source))
    if not matches:
        if not required:
            return source
        if "</body>" not in source:
            raise RuntimeError("portfolio page has no closing body tag")
        return source.replace("</body>", canonical_tag(name) + "</body>", 1)

    first = matches[0]
    chunks = []
    cursor = 0
    for index, match in enumerate(matches):
        chunks.append(source[cursor:match.start()])
        if index == 0:
            chunks.append(canonical_tag(name))
        cursor = match.end()
    chunks.append(source[cursor:])
    return "".join(chunks)


def finalize_text(source: str) -> str:
    # Normalize whichever language-specific dashboard controller is present.
    for controller in ("portfolio-10k-dashboard.js", "portfolio-10k-dashboard-en.js"):
        source = _normalize_one(source, controller, required=False)

    for name in REQUIRED_SHARED:
        source = _normalize_one(source, name, required=True)

    # Navigation guard must be the final protected script so it sees the fully
    # installed page. Move only this tag, not the rest of the script order.
    nav = canonical_tag("portfolio-10k-navigation-guard.js")
    source = script_pattern("portfolio-10k-navigation-guard.js").sub("", source)
    if "</body>" not in source:
        raise RuntimeError("portfolio page has no closing body tag")
    source = source.replace("</body>", nav + "</body>", 1)
    return source


def finalize_pages(root: Path = ROOT, check: bool = False) -> list[str]:
    changed: list[str] = []
    for rel in PAGES:
        path = root / rel
        before = path.read_text(encoding="utf-8")
        after = finalize_text(before)
        if before != after:
            changed.append(str(rel))
            if not check:
                path.write_text(after, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = finalize_pages(args.root.resolve(), check=args.check)
    if args.check and changed:
        print("Portfolio 10K HTML integrity drift: " + ", ".join(changed))
        return 1
    print("Portfolio 10K HTML integrity: " + (", ".join(changed) if changed else "current"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
