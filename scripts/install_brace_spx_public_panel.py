#!/usr/bin/env python3
"""Install BRACE-SPX tabs and a collapsed public status block on scenario pages."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAB_MARKER = 'data-brace-spx-tabs="true"'
GEN3_MARKER = 'data-brace-spx-generation3'
GEN3_SCRIPT = '<script src="/scripts/brace-spx-generation3-card.js?v=20260802-2" defer></script>'
OLD_SCRIPT_PREFIX = '<script src="/scripts/brace-spx-generation3-card.js?v='
GREEN_TAB_STYLE = (
    "display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;"
    "border:1px solid #166534!important;border-radius:999px;"
    "background:#15803d!important;color:#fff!important;font-weight:800;"
    "text-decoration:none;box-shadow:0 7px 18px rgba(21,128,61,.24)!important"
)

PL_TAB = f'''  <nav data-brace-spx-tabs="true" role="tablist" aria-label="Zakładki Scenariuszy S&P 500" style="display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 18px">
    <a role="tab" aria-selected="true" aria-current="page" href="/pl/inwestycje/spx-scenariusze-2026.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border-radius:999px;background:#111827;color:#fff;font-weight:800;text-decoration:none">Scenariusze S&amp;P</a>
    <a role="tab" aria-selected="false" class="brace-spx-lab-tab" href="/pl/inwestycje/brace-spx-lab.html" style="{GREEN_TAB_STYLE}">BRACE-SPX Lab</a>
  </nav>'''

EN_TAB = f'''  <nav data-brace-spx-tabs="true" role="tablist" aria-label="S&P 500 Scenario tabs" style="display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 18px">
    <a role="tab" aria-selected="true" aria-current="page" href="/en/investing/spx-scenarios-2026.html" style="display:inline-flex;align-items:center;min-height:42px;padding:9px 15px;border-radius:999px;background:#111827;color:#fff;font-weight:800;text-decoration:none">S&amp;P Scenarios</a>
    <a role="tab" aria-selected="false" class="brace-spx-lab-tab" href="/en/investing/brace-spx-lab.html" style="{GREEN_TAB_STYLE}">BRACE-SPX Lab</a>
  </nav>'''

PL_GEN3 = '''  <details class="card brace-overview" data-brace-spx-generation3 aria-labelledby="brace-spx-gen3-heading">
    <summary class="brace-overview-summary">
      <span class="brace-overview-title"><h2 id="brace-spx-gen3-heading">BRACE-SPX LAB — Architecture 2S</h2><strong data-v3-field="generation">spx-multisignal-regime-a2s</strong></span>
      <span data-v3-field="status" class="brace-overview-status">Walidacja long/short/flat</span>
      <span class="brace-overview-toggle"><span class="when-closed">Rozwiń</span><span class="when-open">Zwiń</span><span aria-hidden="true">⌄</span></span>
    </summary>
    <div class="brace-overview-body">
      <p class="brace-note">Nowa Architecture 2S dopuszcza long, short albo flat bez dźwigni. Stare wyniki Architecture 2 long/flat pozostają nienaruszonym punktem odniesienia.</p>
      <div class="brace-progress" aria-label="Postęp badań"><div data-v3-progress-bar role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div></div>
      <div class="signal-grid">
        <div class="signal-card"><small>Postęp</small><strong><span data-v3-field="completed">0</span> / <span data-v3-field="total">10</span></strong><span><span data-v3-field="remaining">10</span> pozostało</span></div>
        <div class="signal-card"><small>Sygnatura</small><strong data-v3-field="signature">—</strong><span>nowy hash kandydatów</span></div>
        <div class="signal-card"><small>Holdout</small><strong data-v3-field="holdout">zapieczętowany</strong><span>bez podglądania wyniku końcowego</span></div>
        <div class="signal-card"><small>CAGR lidera</small><strong data-v3-field="cagr">—</strong><span>dane rozwojowe</span></div>
        <div class="signal-card"><small>Sharpe excess</small><strong data-v3-field="sharpe">—</strong><span>po wszystkich kosztach</span></div>
        <div class="signal-card"><small>Ścisła bramka</small><strong data-v3-field="gate">w toku</strong><span>DSR, PBO, stabilność i benchmarki</span></div>
      </div>
      <p class="tagline"><a href="/pl/inwestycje/brace-spx-lab.html">Otwórz pełny panel BRACE-SPX Lab →</a></p>
    </div>
  </details>'''

EN_GEN3 = '''  <details class="card brace-overview" data-brace-spx-generation3 aria-labelledby="brace-spx-gen3-heading">
    <summary class="brace-overview-summary">
      <span class="brace-overview-title"><h2 id="brace-spx-gen3-heading">BRACE-SPX LAB — Architecture 2S</h2><strong data-v3-field="generation">spx-multisignal-regime-a2s</strong></span>
      <span data-v3-field="status" class="brace-overview-status">Long/short/flat validation</span>
      <span class="brace-overview-toggle"><span class="when-closed">Expand</span><span class="when-open">Collapse</span><span aria-hidden="true">⌄</span></span>
    </summary>
    <div class="brace-overview-body">
      <p class="brace-note">The new Architecture 2S permits long, short or flat exposure without leverage. The old Architecture 2 long/flat evidence remains an untouched reference.</p>
      <div class="brace-progress" aria-label="Research progress"><div data-v3-progress-bar role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div></div>
      <div class="signal-grid">
        <div class="signal-card"><small>Progress</small><strong><span data-v3-field="completed">0</span> / <span data-v3-field="total">10</span></strong><span><span data-v3-field="remaining">10</span> remaining</span></div>
        <div class="signal-card"><small>Signature</small><strong data-v3-field="signature">—</strong><span>new candidate hash</span></div>
        <div class="signal-card"><small>Holdout</small><strong data-v3-field="holdout">sealed</strong><span>no final-result tuning</span></div>
        <div class="signal-card"><small>Leader CAGR</small><strong data-v3-field="cagr">—</strong><span>development data</span></div>
        <div class="signal-card"><small>Excess Sharpe</small><strong data-v3-field="sharpe">—</strong><span>after all costs</span></div>
        <div class="signal-card"><small>Strict gate</small><strong data-v3-field="gate">pending</strong><span>DSR, PBO, stability and baselines</span></div>
      </div>
      <p class="tagline"><a href="/en/investing/brace-spx-lab.html">Open the full BRACE-SPX Lab panel →</a></p>
    </div>
  </details>'''

TARGETS = (
    (ROOT / "pl" / "inwestycje" / "spx-scenariusze-2026.html", PL_TAB, PL_GEN3, "/pl/inwestycje/brace-spx-lab.html"),
    (ROOT / "en" / "investing" / "spx-scenarios-2026.html", EN_TAB, EN_GEN3, "/en/investing/brace-spx-lab.html"),
)


def _replace_script(source: str) -> tuple[str, bool]:
    start = source.find(OLD_SCRIPT_PREFIX)
    if start < 0:
        return source.replace("</body>", GEN3_SCRIPT + "\n</body>", 1), True
    end = source.find("</script>", start)
    if end < 0:
        raise RuntimeError("Malformed BRACE-SPX script tag")
    end += len("</script>")
    current = source[start:end]
    return (source, False) if current == GEN3_SCRIPT else (source[:start] + GEN3_SCRIPT + source[end:], True)


def _normalize_tab(source: str, tab: str) -> tuple[str, bool]:
    if TAB_MARKER not in source:
        anchor = "<main>\n"
        if anchor not in source:
            raise RuntimeError("Cannot find <main> insertion point")
        return source.replace(anchor, anchor + tab + "\n", 1), True
    marker_index = source.index(TAB_MARKER)
    nav_start = source.rfind("<nav", 0, marker_index)
    nav_end = source.index("</nav>", marker_index) + len("</nav>")
    current = source[nav_start:nav_end]
    if current == tab:
        return source, False
    return source[:nav_start] + tab + source[nav_end:], True


def install_page(path: Path, tab: str, generation_block: str) -> bool:
    source = path.read_text(encoding="utf-8")
    source, changed = _normalize_tab(source, tab)
    if GEN3_MARKER not in source:
        marker_index = source.index(TAB_MARKER)
        nav_end = source.index("</nav>", marker_index) + len("</nav>")
        source = source[:nav_end] + "\n" + generation_block + source[nav_end:]
        changed = True
    source, script_changed = _replace_script(source)
    changed = changed or script_changed
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
    rows = "".join(f"  <url><loc>{url}</loc></url>\n" for url in missing)
    path.write_text(source.replace("</urlset>", rows + "</urlset>", 1), encoding="utf-8")
    return True


def validate() -> None:
    for path, tab, _block, href in TARGETS:
        source = path.read_text(encoding="utf-8")
        if source.count(TAB_MARKER) != 1 or source.count(GEN3_MARKER) != 1:
            raise RuntimeError(f"Incomplete BRACE-SPX integration in {path}")
        if href not in source or GEN3_SCRIPT not in source or tab not in source:
            raise RuntimeError(f"Incomplete BRACE-SPX integration in {path}")
        if "background:#15803d!important" not in source or "color:#fff!important" not in source:
            raise RuntimeError(f"Green BRACE-SPX tab is not enforced in {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        changed = [str(path.relative_to(ROOT)) for path, tab, block, _href in TARGETS if install_page(path, tab, block)]
        if install_sitemap():
            changed.append("sitemap.xml")
        print("BRACE-SPX public panel installed:", ", ".join(changed) if changed else "already current")
    validate()


if __name__ == "__main__":
    main()
