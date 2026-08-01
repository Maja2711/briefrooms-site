#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "scripts" / "site-header.js"
PAGES = [ROOT / "pl/inwestycje.html", ROOT / "en/investing.html"]
VERSION = "20260801-2"

PANEL_RE = re.compile(r"\n\s*<aside class=\"model\">.*?</aside>\s*\n", re.S)
STATUS_SCRIPT_RE = re.compile(
    r"\n<script>\s*\n\(function\(\)\{\s*\n\s*const closedText=.*?</script>\s*\n",
    re.S,
)
INSTRUMENT_MARKUP_RE = re.compile(
    r"  function instrumentMarkup\(instrument, language, labels\) \{.*?\n  \}\n\n  function sourceMarkup",
    re.S,
)


def patch_js() -> bool:
    text = JS.read_text(encoding="utf-8")
    updated = text.replace(
        "var anchor = doc.querySelector('.model');",
        "var anchor = doc.querySelector('#daily-market-alert-anchor');",
    )
    if updated == text and "#daily-market-alert-anchor" not in text:
        raise RuntimeError("Daily alert mount selector was not found")

    updated = updated.replace(
        "      reason: 'Co nowego i dlaczego rynek reaguje',",
        "      whatChanged: 'Co się zmieniło',\n      whyMatters: 'Dlaczego to ma znaczenie',\n      baseCase: 'Scenariusz bazowy: 1–3 sesje',",
    ).replace(
        "      reason: 'What is new and why the market is reacting',",
        "      whatChanged: 'What changed',\n      whyMatters: 'Why it matters',\n      baseCase: 'Base case: next 1–3 sessions',",
    )

    new_markup = '''  function instrumentMarkup(instrument, language, labels) {
    var directionClass = instrument.direction === 'up' ? ' is-up' : instrument.direction === 'down' ? ' is-down' : '';
    var narrative = instrument.narrative && instrument.narrative[language] ? instrument.narrative[language] : null;
    var whatChanged = narrative && narrative.what_changed ? narrative.what_changed : localized(instrument.reason, language);
    var whyMatters = narrative && narrative.why_it_matters ? narrative.why_it_matters : '';
    var baseCase = narrative && narrative.base_case ? narrative.base_case : localized(instrument.trigger, language);
    return '<article class="br-daily-alert__card">' +
      '<div class="br-daily-alert__instrument-head">' +
        '<div class="br-daily-alert__instrument"><h3>' + escapeHtml(instrument.name) + '</h3><span class="br-daily-alert__class">' + escapeHtml(localized(instrument.asset_class, language)) + '</span></div>' +
        '<div class="br-daily-alert__market"><span class="br-daily-alert__price">' + escapeHtml(instrument.price) + '</span><span class="br-daily-alert__change' + directionClass + '">' + escapeHtml(instrument.change) + '</span></div>' +
      '</div>' +
      '<span class="br-daily-alert__label">' + labels.whatChanged + '</span>' +
      '<p class="br-daily-alert__reason">' + escapeHtml(whatChanged) + '</p>' +
      (whyMatters ? '<span class="br-daily-alert__label">' + labels.whyMatters + '</span><p class="br-daily-alert__reason">' + escapeHtml(whyMatters) + '</p>' : '') +
      '<div class="br-daily-alert__levels">' +
        '<div class="br-daily-alert__level"><small>' + labels.support + '</small><b>' + escapeHtml(instrument.support) + '</b></div>' +
        '<div class="br-daily-alert__level"><small>' + labels.resistance + '</small><b>' + escapeHtml(instrument.resistance) + '</b></div>' +
      '</div>' +
      '<span class="br-daily-alert__label">' + labels.baseCase + '</span>' +
      '<p class="br-daily-alert__trigger">' + escapeHtml(baseCase) + '</p>' +
      '<span class="br-daily-alert__label">' + labels.horizon + '</span>' +
      '<div class="br-daily-alert__scenarios">' + scenarioMarkup(instrument.scenarios, language) + '</div>' +
    '</article>';
  }

  function sourceMarkup'''
    updated, count = INSTRUMENT_MARKUP_RE.subn(new_markup, updated, count=1)
    if count != 1 and "var narrative = instrument.narrative" not in updated:
        raise RuntimeError("Daily alert instrument renderer was not found")

    if updated != text:
        JS.write_text(updated, encoding="utf-8")
        return True
    return False


def patch_page(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = text

    if 'id="daily-market-alert-anchor"' not in updated:
        updated, count = PANEL_RE.subn(
            '\n  <span id="daily-market-alert-anchor" hidden></span>\n',
            updated,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"Model direction panel not found in {path}")
    else:
        updated = PANEL_RE.sub("\n", updated, count=1)

    updated = STATUS_SCRIPT_RE.sub("\n", updated, count=1)
    updated = re.sub(
        r"/scripts/site-header\.js\?v=\d{8}-\d+",
        f"/scripts/site-header.js?v={VERSION}",
        updated,
    )

    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = patch_js()
    for page in PAGES:
        changed = patch_page(page) or changed
    print("Investing alert UI upgraded." if changed else "Investing alert UI already upgraded.")


if __name__ == "__main__":
    main()
