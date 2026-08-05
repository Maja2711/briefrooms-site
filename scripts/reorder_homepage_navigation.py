#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ORDERS = {
    ROOT / "pl" / "index.html": [
        "/pl/aktualnosci.html",
        "/pl/inwestycje.html",
        "/pl/zdrowie.html",
        "/pl/nauka.html",
        "/pl/geopolityka.html",
        "/pl/o-projekcie.html",
    ],
    ROOT / "en" / "index.html": [
        "/en/news.html",
        "/en/investing.html",
        "/en/health.html",
        "/en/science.html",
        "/en/geopolitics.html",
        "/en/about.html",
    ],
}

NAV_PATTERN = re.compile(
    r'(<nav\s+class="nav"\b[^>]*>)([\s\S]*?)(</nav>)',
    re.IGNORECASE,
)
ANCHOR_PATTERN = re.compile(r'<a\b[\s\S]*?</a>', re.IGNORECASE)
HREF_PATTERN = re.compile(r'\bhref="([^"]+)"', re.IGNORECASE)


def nav_order(source: str) -> list[str]:
    match = NAV_PATTERN.search(source)
    if not match:
        raise RuntimeError('homepage navigation block not found')
    hrefs: list[str] = []
    for anchor in ANCHOR_PATTERN.findall(match.group(2)):
        href_match = HREF_PATTERN.search(anchor)
        if href_match:
            hrefs.append(href_match.group(1))
    return hrefs


def reorder(source: str, expected: list[str]) -> str:
    match = NAV_PATTERN.search(source)
    if not match:
        raise RuntimeError('homepage navigation block not found')

    anchors = ANCHOR_PATTERN.findall(match.group(2))
    by_href: dict[str, str] = {}
    for anchor in anchors:
        href_match = HREF_PATTERN.search(anchor)
        if not href_match:
            continue
        href = href_match.group(1)
        if href in by_href:
            raise RuntimeError(f'duplicate homepage navigation href: {href}')
        by_href[href] = anchor

    if set(by_href) != set(expected):
        missing = sorted(set(expected) - set(by_href))
        unexpected = sorted(set(by_href) - set(expected))
        raise RuntimeError(
            f'homepage navigation mismatch; missing={missing}, unexpected={unexpected}'
        )

    ordered_body = ''.join(by_href[href] for href in expected)
    return source[:match.start()] + match.group(1) + ordered_body + match.group(3) + source[match.end():]


def apply(check_only: bool) -> None:
    changed: list[str] = []
    for path, expected in EXPECTED_ORDERS.items():
        source = path.read_text(encoding='utf-8')
        updated = reorder(source, expected)
        actual = nav_order(updated)
        if actual != expected:
            raise RuntimeError(f'{path}: invalid order after rewrite: {actual}')
        if source != updated:
            changed.append(str(path.relative_to(ROOT)))
            if not check_only:
                path.write_text(updated, encoding='utf-8', newline='\n')
        elif check_only and nav_order(source) != expected:
            raise RuntimeError(f'{path}: homepage navigation order is stale')

    if check_only and changed:
        raise RuntimeError('homepage navigation requires update: ' + ', '.join(changed))
    print('Homepage navigation order is correct for PL and EN.')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.check)


if __name__ == '__main__':
    main()
