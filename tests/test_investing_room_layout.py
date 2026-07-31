from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    ROOT / "pl" / "inwestycje.html",
    ROOT / "en" / "investing.html",
)


def page_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_three_primary_investing_entries_are_equally_exposed() -> None:
    for page in PAGES:
        html = page_text(page)
        relative = page.relative_to(ROOT)
        assert '<section class="focus-grid"' in html, relative
        assert html.count('class="card weekly"') == 1, relative
        assert html.count('class="card portfolio"') == 1, relative
        assert html.count('class="card scenario"') == 1, relative
        assert "grid-template-columns:repeat(3,minmax(0,1fr))" in html, relative

        focus_index = html.index('<section class="focus-grid"')
        quotes_index = html.index('<h2 class="quotes-head">')
        assert focus_index < quotes_index, relative


def test_market_quotes_are_compact_on_desktop_and_mobile() -> None:
    for page in PAGES:
        html = page_text(page)
        relative = page.relative_to(ROOT)
        assert "padding:10px 12px" in html, relative
        assert "font-size:20px" in html, relative
        assert "grid-template-columns:repeat(2,minmax(0,1fr));gap:7px" in html, relative
        assert "font-size:16px" in html, relative
        assert "font-size:7.8px" in html, relative
        assert ".quotes,.hero,.grid{grid-template-columns:1fr}" not in html, relative


def test_obsolete_static_model_panel_is_not_rendered() -> None:
    for page in PAGES:
        html = page_text(page)
        relative = page.relative_to(ROOT)
        assert '<aside class="model">' not in html, relative
        assert '<span id="daily-market-alert-anchor" hidden></span>' in html, relative


def test_quotes_keep_automatic_refresh_and_shared_header() -> None:
    for page in PAGES:
        html = page_text(page)
        relative = page.relative_to(ROOT)
        assert '<header id="site-header"></header>' in html, relative
        assert "/assets/site-header.css?v=20260719-1" in html, relative
        assert "/scripts/site-header.js?v=20260723-4" in html, relative
        assert "setInterval(loadQuotes,15*60*1000)" in html, relative
        assert "visibilitychange" in html, relative
