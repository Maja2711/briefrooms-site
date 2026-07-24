#!/usr/bin/env python3
"""Install the BRACE-SPX Lab tabs into the existing PL/EN S&P scenario pages.

The existing pages are intentionally patched by a tiny deterministic installer so
that their long editorial content is not rewritten by hand. Running the script
more than once is safe.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'data-brace-spx-tabs="true"'

PL_TAB = '''  <nav data-brace-spx-tabs="true" role="tablist" aria-label="Zakładki Scenariuszy S&P 500" style="display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 18px">
    <a role="tab" aria-selected="true" aria-current="page" href="/pl/inwestycje/spx-scenariusze-2026.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border-radius:999px;background:#111827;color:#fff;font-weight:800;text-decoration:none">Scenariusze S&amp;P</a>
    <a role="tab" aria-selected="false" href="/pl/inwestycje/brace-spx-lab.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border:1px solid rgba(15,23,42,.12);border-radius:999px;background:#fff;color:#1e3a8a;font-weight:800;text-decoration:none;box-shadow:0 7px 18px rgba(15,23,42,.06)">BRACE-SPX Lab</a>
  </nav>'''

EN_TAB = '''  <nav data-brace-spx-tabs="true" role="tablist" aria-label="S&P 500 Scenario tabs" style="display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 18px">
    <a role="tab" aria-selected="true" aria-current="page" href="/en/investing/spx-scenarios-2026.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border-radius:999px;background:#111827;color:#fff;font-weight:800;text-decoration:none">S&amp;P Scenarios</a>
    <a role="tab" aria-selected="false" href="/en/investing/brace-spx-lab.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border:1px solid rgba(15,23,42,.12);border-radius:999px;background:#fff;color:#1e3a8a;font-weight:800;text-decoration:none;box-shadow:0 7px 18px rgba(15,23,42,.06)">BRACE-SPX Lab</a>
  </nav>'''

TARGETS = (
    (ROOT / "pl" / "inwestycje" / "spx-scenariusze-2026.html", PL_TAB, "/pl/inwestycje/brace-spx-lab.html"),
    (ROOT / "en" / "investing" / "spx-scenarios-2026.html", EN_TAB, "/en/investing/brace-spx-lab.html"),
)


def install_page(path: Path, tab: str) -> bool:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return False
    anchor = "<main>\n"
    if anchor not in source:
        raise RuntimeError(f"Cannot find <main> insertion point in {path}")
    updated = source.replace(anchor, anchor + tab + "\n", 1)
    path.write_text(updated, encoding="utf-8")
    return True


def install_sitemap() -> bool:
    path = ROOT / "sitemap.xml"
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    urls = (
        "https://briefrooms.com/pl/inwestycje/brace-spx-lab.html",
        "https://briefrooms.com/en/investing/brace-spx-lab.html",
    )
    missing = [url for url in urls if url not in source]
    if not missing:
        return False
    closing = "</urlset>"
    if closing not in source:
        raise RuntimeError("sitemap.xml has no </urlset> closing tag")
    rows = "".join(f"  <url><loc>{url}</loc></url>\n" for url in missing)
    path.write_text(source.replace(closing, rows + closing, 1), encoding="utf-8")
    return True


def validate() -> None:
    for path, _tab, href in TARGETS:
        source = path.read_text(encoding="utf-8")
        if source.count(MARKER) != 1:
            raise RuntimeError(f"Expected exactly one BRACE-SPX tab bar in {path}")
        if href not in source:
            raise RuntimeError(f"Missing BRACE-SPX Lab link in {path}")
    for page in (
        ROOT / "pl" / "inwestycje" / "brace-spx-lab.html",
        ROOT / "en" / "investing" / "brace-spx-lab.html",
    ):
        if not page.exists() or "data-brace-lab-root" not in page.read_text(encoding="utf-8"):
            raise RuntimeError(f"Missing public laboratory page: {page}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        changed = [str(path.relative_to(ROOT)) for path, tab, _href in TARGETS if install_page(path, tab)]
        if install_sitemap():
            changed.append("sitemap.xml")
        print("BRACE-SPX public panel installed:", ", ".join(changed) if changed else "already current")
    validate()


if __name__ == "__main__":
    main()
