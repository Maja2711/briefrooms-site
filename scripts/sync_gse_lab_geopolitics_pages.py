#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "pl": ROOT / "pl" / "geopolityka.html",
    "en": ROOT / "en" / "geopolitics.html",
}
BLOCK = re.compile(r"\s*<!-- BR_GSE_LAB_ENTRY_START -->[\s\S]*?<!-- BR_GSE_LAB_ENTRY_END -->\s*", re.I)
STYLE = '<link rel="stylesheet" href="/assets/gse-lab-entry.css?v=1" />'
SCRIPT = '<script src="/scripts/gse-lab-entry.js?v=1" defer></script>'


def block(lang: str) -> str:
    if lang == "pl":
        return '''<!-- BR_GSE_LAB_ENTRY_START -->
<section class="gse-lab-entry" data-gse-lab-entry aria-labelledby="gse-lab-entry-title">
  <div class="gse-lab-entry__top">
    <div>
      <span class="gse-lab-entry__eyebrow">BriefRooms Research</span>
      <h2 id="gse-lab-entry-title">GSE Lab</h2>
      <p class="gse-lab-entry__full">Geopolitical Scenario Engine</p>
      <p class="gse-lab-entry__desc">Laboratorium silnika scenariuszy geopolitycznych: historia uczenia, zweryfikowane epizody, walk-forward, kalibracja live i porównanie horyzontów 24h / 7d / 30d.</p>
    </div>
    <span class="gse-lab-entry__status" data-gse-status>SHADOW LEARNING</span>
  </div>
  <div class="gse-lab-entry__metrics">
    <div class="gse-lab-entry__metric"><strong data-gse-clusters>18 / 100+</strong><span>klastry</span></div>
    <div class="gse-lab-entry__metric"><strong data-gse-walk>282</strong><span>walk-forward</span></div>
    <div class="gse-lab-entry__metric"><strong data-gse-live>32</strong><span>pary live</span></div>
    <div class="gse-lab-entry__metric"><strong data-gse-best>30d</strong><span>najlepszy horyzont</span></div>
  </div>
  <div class="gse-lab-entry__bottom">
    <p class="gse-lab-entry__finding" data-gse-finding>Najlepszy historyczny horyzont: 30d. Szczegóły i pełne wyniki są w laboratorium.</p>
    <a class="gse-lab-entry__link" href="/pl/geo/gse-lab.html">Otwórz GSE Lab →</a>
  </div>
</section>
<!-- BR_GSE_LAB_ENTRY_END -->'''
    return '''<!-- BR_GSE_LAB_ENTRY_START -->
<section class="gse-lab-entry" data-gse-lab-entry aria-labelledby="gse-lab-entry-title">
  <div class="gse-lab-entry__top">
    <div>
      <span class="gse-lab-entry__eyebrow">BriefRooms Research</span>
      <h2 id="gse-lab-entry-title">GSE Lab</h2>
      <p class="gse-lab-entry__full">Geopolitical Scenario Engine</p>
      <p class="gse-lab-entry__desc">Geopolitical scenario-engine research: learning history, verified episodes, walk-forward validation, prospective calibration and 24h / 7d / 30d horizon comparison.</p>
    </div>
    <span class="gse-lab-entry__status" data-gse-status>SHADOW LEARNING</span>
  </div>
  <div class="gse-lab-entry__metrics">
    <div class="gse-lab-entry__metric"><strong data-gse-clusters>18 / 100+</strong><span>clusters</span></div>
    <div class="gse-lab-entry__metric"><strong data-gse-walk>282</strong><span>walk-forward</span></div>
    <div class="gse-lab-entry__metric"><strong data-gse-live>32</strong><span>live pairs</span></div>
    <div class="gse-lab-entry__metric"><strong data-gse-best>30d</strong><span>best horizon</span></div>
  </div>
  <div class="gse-lab-entry__bottom">
    <p class="gse-lab-entry__finding" data-gse-finding>Best historical horizon: 30d. Full results are available in the lab.</p>
    <a class="gse-lab-entry__link" href="/en/geo/gse-lab.html">Open GSE Lab →</a>
  </div>
</section>
<!-- BR_GSE_LAB_ENTRY_END -->'''


def patch(path: Path, lang: str) -> bool:
    source = path.read_text(encoding="utf-8")
    cleaned = BLOCK.sub("\n", source)
    if STYLE not in cleaned:
        cleaned = cleaned.replace("</head>", STYLE + "\n</head>", 1)
    if SCRIPT not in cleaned:
        cleaned = cleaned.replace("</body>", SCRIPT + "\n</body>", 1)
    marker = re.search(r"<main(?:\s[^>]*)?>", cleaned, re.I)
    if not marker:
        raise RuntimeError(f"Missing <main> in {path}")
    updated = cleaned[:marker.end()] + "\n" + block(lang) + "\n" + cleaned[marker.end():]
    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def check() -> None:
    for lang, path in PAGES.items():
        source = path.read_text(encoding="utf-8")
        expected = "/pl/geo/gse-lab.html" if lang == "pl" else "/en/geo/gse-lab.html"
        for marker in ("<!-- BR_GSE_LAB_ENTRY_START -->", "Geopolitical Scenario Engine", expected, STYLE, SCRIPT):
            if marker not in source:
                raise RuntimeError(f"Missing {marker!r} in {path}")
        if source.count("<!-- BR_GSE_LAB_ENTRY_START -->") != 1:
            raise RuntimeError(f"Duplicate GSE Lab entry in {path}")


def main() -> None:
    changed=[]
    for lang,path in PAGES.items():
        if patch(path,lang): changed.append(lang)
    check()
    print("GSE Lab room entry synchronized: " + (", ".join(changed) if changed else "already current"))


if __name__ == "__main__":
    main()
