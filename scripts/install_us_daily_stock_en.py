#!/usr/bin/env python3
"""Idempotently install the US Daily Stock widget on the English 10K page."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "en/investing/portfolio-10k.html"
CSS = '<link rel="stylesheet" href="/assets/gpw-daily-pick.css?v=1">'
SCRIPT = '<script src="/scripts/us-daily-stock-public.js?v=2" defer></script>'
START = '<!-- us-daily-stock:start -->'
END = '<!-- us-daily-stock:end -->'

WIDGET = '''<!-- us-daily-stock:start --><article id="us-daily-stock-root" class="dash-card page-card us-daily-stock-card" aria-labelledby="us-daily-stock-title"><div class="card-head"><div><span class="research-chip">DAILY STOCK · US MARKET</span><h2 id="us-daily-stock-title">US DAILY STOCK</h2><p>One audited US equity setup for a 1–2 session paper-trade horizon. Fresh market data, evidence, risk gates and an independent AI integrity review.</p></div><span class="gpw-pick-status pending" data-us-status>CHECKING</span></div><div class="gpw-pick-meta"><span data-us-date>—</span><span data-us-generated>—</span></div><div data-us-body><div class="gpw-pick-empty"><strong>Loading US Daily Stock…</strong></div></div><div class="gpw-pick-metrics" data-us-metrics></div><details class="gpw-pick-details" data-us-details hidden><summary>Method, score and track record</summary><div data-us-details-body></div></details><p class="legal">Research paper-trading module. Not investment advice.</p></article><!-- us-daily-stock:end -->'''


def install() -> bool:
    text = PAGE.read_text(encoding="utf-8")
    original = text

    if CSS not in text:
        marker = '</head>'
        if marker not in text:
            raise SystemExit("EN portfolio page has no </head> marker")
        text = text.replace(marker, CSS + marker, 1)

    if START in text and END in text:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        text = before + WIDGET + after
    else:
        marker = '<!-- portfolio-static-snapshot:start -->'
        if marker not in text:
            raise SystemExit("EN portfolio static snapshot marker not found")
        text = text.replace(marker, WIDGET + marker, 1)

    text = re.sub(
        r'<script src="/scripts/us-daily-stock-public\.js\?v=\d+" defer></script>',
        SCRIPT,
        text,
    )
    if SCRIPT not in text:
        marker = '</body>'
        if marker not in text:
            raise SystemExit("EN portfolio page has no </body> marker")
        text = text.replace(marker, SCRIPT + marker, 1)

    if text != original:
        PAGE.write_text(text, encoding="utf-8")
        return True
    return False


if __name__ == "__main__":
    changed = install()
    print("US Daily Stock EN widget installed." if changed else "US Daily Stock EN widget already current.")
