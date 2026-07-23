#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "scripts" / "site-header.js"
PAGES = [ROOT / "pl" / "inwestycje.html", ROOT / "en" / "investing.html"]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Could not patch {label}: expected source fragment is missing")
    return text.replace(old, new, 1)


def patch_js() -> bool:
    text = JS.read_text(encoding="utf-8")
    original = text

    text = replace_once(
        text,
        "      '.br-daily-alert__meta{display:flex;align-items:center;justify-content:flex-end;gap:10px;color:#91a6ba;font-size:10px;font-weight:850;white-space:nowrap}',\n"
        "      '.br-daily-alert__freshness.is-stale{color:#ffbf3f}',",
        "      '.br-daily-alert__meta{display:flex;align-items:center;justify-content:flex-end;gap:12px;color:#91a6ba;font-size:10px;font-weight:850;white-space:nowrap}',\n"
        "      '.br-daily-alert__meta-copy{display:flex;flex-direction:column;align-items:flex-end;gap:3px}',\n"
        "      '.br-daily-alert__edition{color:#c5d4e2;font-size:9px;font-weight:900}',\n"
        "      '.br-daily-alert__action{display:flex;flex-direction:column;align-items:center;gap:3px}',\n"
        "      '.br-daily-alert__expand{color:#9ffff6;font-size:9px;font-weight:950;letter-spacing:.045em;text-transform:uppercase}',\n"
        "      '.br-daily-alert__freshness.is-stale{color:#ffbf3f}',",
        "daily alert meta styles",
    )
    text = replace_once(
        text,
        "      '.br-daily-alert__summary{max-width:960px;margin:0 0 17px;color:#c7d6e4;font-size:13px;line-height:1.65}',",
        "      '.br-daily-alert__summary{max-width:960px;margin:0 0 12px;color:#c7d6e4;font-size:13px;line-height:1.65}',\n"
        "      '.br-daily-alert__session-note{margin:0 0 17px;padding:10px 12px;border:1px solid rgba(56,214,201,.16);border-radius:12px;background:rgba(56,214,201,.055);color:#aee5df;font-size:11px;line-height:1.55}',",
        "session note styles",
    )
    text = replace_once(
        text,
        "      '@media(max-width:620px){.br-daily-alert__toggle{grid-template-columns:1fr;padding:16px}.br-daily-alert__meta{justify-content:space-between}.br-daily-alert__body{padding:15px}.br-daily-alert__title{font-size:20px}.br-daily-alert__snapshot{font-size:11px}.br-daily-alert__reason{font-size:11.5px}}',",
        "      '@media(max-width:620px){.br-daily-alert__toggle{grid-template-columns:1fr;padding:16px}.br-daily-alert__meta{justify-content:space-between}.br-daily-alert__meta-copy{align-items:flex-start}.br-daily-alert__body{padding:15px}.br-daily-alert__title{font-size:20px}.br-daily-alert__snapshot{font-size:11px}.br-daily-alert__reason{font-size:11.5px}}',",
        "mobile alert meta styles",
    )
    text = replace_once(
        text,
        "      updated: 'Aktualizacja',\n"
        "      reason: 'Co nowego i dlaczego rynek reaguje',",
        "      updated: 'Aktualizacja',\n"
        "      expand: 'Rozwiń',\n"
        "      collapse: 'Zwiń',\n"
        "      openingEdition: 'Alert po otwarciu',\n"
        "      precloseEdition: 'Aktualizacja przed zamknięciem',\n"
        "      reason: 'Co nowego i dlaczego rynek reaguje',",
        "Polish alert labels",
    )
    text = replace_once(
        text,
        "      updated: 'Updated',\n"
        "      reason: 'What is new and why the market is reacting',",
        "      updated: 'Updated',\n"
        "      expand: 'Expand',\n"
        "      collapse: 'Collapse',\n"
        "      openingEdition: 'Post-open alert',\n"
        "      precloseEdition: 'Pre-close update',\n"
        "      reason: 'What is new and why the market is reacting',",
        "English alert labels",
    )
    text = replace_once(
        text,
        "    var stale = isStale(data.updated_at);\n"
        "    var snapshot = data.instruments.map(function (instrument) {",
        "    var stale = isStale(data.updated_at);\n"
        "    var editionLabel = data.edition === 'preclose' ? labels.precloseEdition : labels.openingEdition;\n"
        "    var sessionNote = data.preclose_check && data.preclose_check.note ? localized(data.preclose_check.note, language) : '';\n"
        "    var snapshot = data.instruments.map(function (instrument) {",
        "edition and session state",
    )
    text = replace_once(
        text,
        "        '<span class=\"br-daily-alert__meta\"><span class=\"br-daily-alert__freshness' + (stale ? ' is-stale' : '') + '\">' + (stale ? labels.stale : labels.current) + '</span><span>' + labels.updated + ': ' + escapeHtml(formatUpdated(data.updated_at, language)) + '</span><span class=\"br-daily-alert__chevron\" aria-hidden=\"true\">⌄</span></span>' +",
        "        '<span class=\"br-daily-alert__meta\"><span class=\"br-daily-alert__meta-copy\"><span class=\"br-daily-alert__freshness' + (stale ? ' is-stale' : '') + '\">' + (stale ? labels.stale : labels.current) + '</span><span class=\"br-daily-alert__edition\">' + escapeHtml(editionLabel) + ' · ' + labels.updated + ': ' + escapeHtml(formatUpdated(data.updated_at, language)) + '</span></span><span class=\"br-daily-alert__action\"><span class=\"br-daily-alert__expand\">' + labels.expand + '</span><span class=\"br-daily-alert__chevron\" aria-hidden=\"true\">⌄</span></span></span>' +",
        "expand label markup",
    )
    text = replace_once(
        text,
        "        '<p class=\"br-daily-alert__summary\">' + escapeHtml(localized(data.summary, language)) + '</p>' +\n"
        "        '<div class=\"br-daily-alert__grid\">'",
        "        '<p class=\"br-daily-alert__summary\">' + escapeHtml(localized(data.summary, language)) + '</p>' +\n"
        "        (sessionNote ? '<p class=\"br-daily-alert__session-note\">' + escapeHtml(sessionNote) + '</p>' : '') +\n"
        "        '<div class=\"br-daily-alert__grid\">'",
        "session note markup",
    )
    text = replace_once(
        text,
        "    var toggle = section.querySelector('.br-daily-alert__toggle');\n"
        "    var body = section.querySelector('.br-daily-alert__body');\n"
        "    toggle.addEventListener('click', function () {\n"
        "      var open = toggle.getAttribute('aria-expanded') === 'true';\n"
        "      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');\n"
        "      body.hidden = open;\n"
        "      section.classList.toggle('is-open', !open);\n"
        "    });",
        "    var toggle = section.querySelector('.br-daily-alert__toggle');\n"
        "    var body = section.querySelector('.br-daily-alert__body');\n"
        "    var expandLabel = section.querySelector('.br-daily-alert__expand');\n"
        "    toggle.addEventListener('click', function () {\n"
        "      var open = toggle.getAttribute('aria-expanded') === 'true';\n"
        "      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');\n"
        "      body.hidden = open;\n"
        "      section.classList.toggle('is-open', !open);\n"
        "      if (expandLabel) expandLabel.textContent = open ? labels.expand : labels.collapse;\n"
        "    });",
        "expand label interaction",
    )

    if text != original:
        JS.write_text(text, encoding="utf-8")
        return True
    return False


def patch_pages() -> bool:
    changed = False
    for path in PAGES:
        text = path.read_text(encoding="utf-8")
        updated = text.replace(
            "/scripts/site-header.js?v=20260719-1",
            "/scripts/site-header.js?v=20260723-3",
        )
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed = True
    return changed


def main() -> None:
    changed = patch_js() | patch_pages()
    print("Daily alert UI patched." if changed else "Daily alert UI already up to date.")


if __name__ == "__main__":
    main()
