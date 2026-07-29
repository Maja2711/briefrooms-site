#!/usr/bin/env python3
"""Install BRACE-SPX tabs and the Generation 3 status block on PL/EN scenario pages.

The installer is deterministic and idempotent so long editorial pages do not need
to be rewritten manually.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAB_MARKER = 'data-brace-spx-tabs="true"'
GEN3_MARKER = 'data-brace-spx-generation3'
GEN3_SCRIPT = '<script src="/scripts/brace-spx-generation3-card.js?v=20260729-1" defer></script>'

PL_TAB = '''  <nav data-brace-spx-tabs="true" role="tablist" aria-label="Zakładki Scenariuszy S&P 500" style="display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 18px">
    <a role="tab" aria-selected="true" aria-current="page" href="/pl/inwestycje/spx-scenariusze-2026.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border-radius:999px;background:#111827;color:#fff;font-weight:800;text-decoration:none">Scenariusze S&amp;P</a>
    <a role="tab" aria-selected="false" href="/pl/inwestycje/brace-spx-lab.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border:1px solid rgba(15,23,42,.12);border-radius:999px;background:#fff;color:#1e3a8a;font-weight:800;text-decoration:none;box-shadow:0 7px 18px rgba(15,23,42,.06)">BRACE-SPX Lab</a>
  </nav>'''

EN_TAB = '''  <nav data-brace-spx-tabs="true" role="tablist" aria-label="S&P 500 Scenario tabs" style="display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 18px">
    <a role="tab" aria-selected="true" aria-current="page" href="/en/investing/spx-scenarios-2026.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border-radius:999px;background:#111827;color:#fff;font-weight:800;text-decoration:none">S&amp;P Scenarios</a>
    <a role="tab" aria-selected="false" href="/en/investing/brace-spx-lab.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border:1px solid rgba(15,23,42,.12);border-radius:999px;background:#fff;color:#1e3a8a;font-weight:800;text-decoration:none;box-shadow:0 7px 18px rgba(15,23,42,.06)">BRACE-SPX Lab</a>
  </nav>'''

PL_GEN3 = '''  <section class="card" data-brace-spx-generation3 aria-labelledby="brace-spx-gen3-heading" style="border:2px solid rgba(37,99,235,.22);box-shadow:0 14px 30px rgba(37,99,235,.1)">
    <div style="display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:12px">
      <div><h2 id="brace-spx-gen3-heading" style="margin-top:0">BRACE-SPX LAB — Generacja 3</h2><strong data-v3-field="generation">spx-focused-v3</strong></div>
      <span data-v3-field="status" style="display:inline-flex;padding:7px 11px;border-radius:999px;background:#eff6ff;color:#1e40af;font-weight:800">Oczekiwanie na pierwszy przebieg</span>
    </div>
    <p>Mniejsza, ukierunkowana przestrzeń <strong>48 kandydatów</strong> dla SPY. Badanie porównuje wyniki z Buy &amp; Hold i trendem 200D; holdout 48M pozostaje zamknięty.</p>
    <div style="height:10px;overflow:hidden;border-radius:999px;background:#e2e8f0" aria-label="Postęp generacji 3"><div data-v3-progress-bar role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" style="height:100%;width:0;background:#2563eb;transition:width .25s ease"></div></div>
    <div class="signal-grid" style="margin-top:12px">
      <div class="signal-card"><small>Postęp</small><strong><span data-v3-field="completed">0</span> / <span data-v3-field="total">48</span></strong><span><span data-v3-field="remaining">48</span> pozostało</span></div>
      <div class="signal-card"><small>Sygnatura</small><strong data-v3-field="signature">—</strong><span>niezmienny hash kandydatów</span></div>
      <div class="signal-card"><small>Holdout</small><strong data-v3-field="holdout">zapieczętowany</strong><span>bez podglądania wyniku końcowego</span></div>
      <div class="signal-card"><small>CAGR lidera</small><strong data-v3-field="cagr">—</strong><span>dane rozwojowe</span></div>
      <div class="signal-card"><small>Sharpe excess</small><strong data-v3-field="sharpe">—</strong><span>ponad stopę wolną od ryzyka</span></div>
      <div class="signal-card"><small>Ścisła bramka</small><strong data-v3-field="gate">w toku</strong><span>DSR, PBO, stabilność i benchmarki</span></div>
    </div>
    <p class="tagline"><a href="/pl/inwestycje/brace-spx-lab.html">Otwórz pełny panel BRACE-SPX Lab →</a></p>
  </section>'''

EN_GEN3 = '''  <section class="card" data-brace-spx-generation3 aria-labelledby="brace-spx-gen3-heading" style="border:2px solid rgba(37,99,235,.22);box-shadow:0 14px 30px rgba(37,99,235,.1)">
    <div style="display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:12px">
      <div><h2 id="brace-spx-gen3-heading" style="margin-top:0">BRACE-SPX LAB — Generation 3</h2><strong data-v3-field="generation">spx-focused-v3</strong></div>
      <span data-v3-field="status" style="display:inline-flex;padding:7px 11px;border-radius:999px;background:#eff6ff;color:#1e40af;font-weight:800">Waiting for the first run</span>
    </div>
    <p>A smaller, focused space of <strong>48 SPY candidates</strong>. Research compares results with Buy &amp; Hold and the 200D trend while the 48M holdout remains sealed.</p>
    <div style="height:10px;overflow:hidden;border-radius:999px;background:#e2e8f0" aria-label="Generation 3 progress"><div data-v3-progress-bar role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" style="height:100%;width:0;background:#2563eb;transition:width .25s ease"></div></div>
    <div class="signal-grid" style="margin-top:12px">
      <div class="signal-card"><small>Progress</small><strong><span data-v3-field="completed">0</span> / <span data-v3-field="total">48</span></strong><span><span data-v3-field="remaining">48</span> remaining</span></div>
      <div class="signal-card"><small>Signature</small><strong data-v3-field="signature">—</strong><span>immutable candidate hash</span></div>
      <div class="signal-card"><small>Holdout</small><strong data-v3-field="holdout">sealed</strong><span>no final-result tuning</span></div>
      <div class="signal-card"><small>Leader CAGR</small><strong data-v3-field="cagr">—</strong><span>development data</span></div>
      <div class="signal-card"><small>Excess Sharpe</small><strong data-v3-field="sharpe">—</strong><span>above the risk-free rate</span></div>
      <div class="signal-card"><small>Strict gate</small><strong data-v3-field="gate">pending</strong><span>DSR, PBO, stability and baselines</span></div>
    </div>
    <p class="tagline"><a href="/en/investing/brace-spx-lab.html">Open the full BRACE-SPX Lab panel →</a></p>
  </section>'''

TARGETS = (
    (ROOT / "pl" / "inwestycje" / "spx-scenariusze-2026.html", PL_TAB, PL_GEN3, "/pl/inwestycje/brace-spx-lab.html"),
    (ROOT / "en" / "investing" / "spx-scenarios-2026.html", EN_TAB, EN_GEN3, "/en/investing/brace-spx-lab.html"),
)


def install_page(path: Path, tab: str, generation_block: str) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    if TAB_MARKER not in source:
        anchor = "<main>\n"
        if anchor not in source:
            raise RuntimeError(f"Cannot find <main> insertion point in {path}")
        source = source.replace(anchor, anchor + tab + "\n", 1)
        changed = True
    if GEN3_MARKER not in source:
        marker_index = source.index(TAB_MARKER)
        nav_end = source.index("</nav>", marker_index) + len("</nav>")
        source = source[:nav_end] + "\n" + generation_block + source[nav_end:]
        changed = True
    if GEN3_SCRIPT not in source:
        if "</body>" not in source:
            raise RuntimeError(f"Cannot find </body> in {path}")
        source = source.replace("</body>", GEN3_SCRIPT + "\n</body>", 1)
        changed = True
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


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
    if "</urlset>" not in source:
        raise RuntimeError("sitemap.xml has no </urlset> closing tag")
    rows = "".join(f"  <url><loc>{url}</loc></url>\n" for url in missing)
    path.write_text(source.replace("</urlset>", rows + "</urlset>", 1), encoding="utf-8")
    return True


def validate() -> None:
    for path, _tab, _generation_block, href in TARGETS:
        source = path.read_text(encoding="utf-8")
        if source.count(TAB_MARKER) != 1:
            raise RuntimeError(f"Expected exactly one BRACE-SPX tab bar in {path}")
        if source.count(GEN3_MARKER) != 1:
            raise RuntimeError(f"Expected exactly one Generation 3 block in {path}")
        if href not in source or GEN3_SCRIPT not in source:
            raise RuntimeError(f"Incomplete BRACE-SPX integration in {path}")
    for page in (
        ROOT / "pl" / "inwestycje" / "brace-spx-lab.html",
        ROOT / "en" / "investing" / "brace-spx-lab.html",
    ):
        source = page.read_text(encoding="utf-8") if page.exists() else ""
        if "data-brace-lab-root" not in source or GEN3_MARKER not in source:
            raise RuntimeError(f"Missing Generation 3 laboratory block: {page}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        changed = [
            str(path.relative_to(ROOT))
            for path, tab, block, _href in TARGETS
            if install_page(path, tab, block)
        ]
        if install_sitemap():
            changed.append("sitemap.xml")
        print("BRACE-SPX public panel installed:", ", ".join(changed) if changed else "already current")
    validate()


if __name__ == "__main__":
    main()
