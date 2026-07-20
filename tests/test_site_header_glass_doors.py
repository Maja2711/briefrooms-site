from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "scripts" / "site-header.js"
CSS_PATH = ROOT / "assets" / "site-header.css"
WEEKLY_RENDERER = ROOT / "scripts" / "render_weekly_public_pages.py"


def test_shared_header_contains_all_six_rooms_in_both_languages() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    expected = (
        "Aktualności", "Geopolityka", "Zdrowie", "Nauka", "Inwestycje", "O nas",
        "News", "Geopolitics", "Health", "Science", "Investing", "About",
    )
    for label in expected:
        assert f"label: '{label}'" in js
    assert js.count("section: 'about'") == 2
    assert "return 'about';" in js


def test_shared_header_uses_opening_glass_door_visuals() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")
    assert ".br-site-header__link::before" in css
    assert ".br-site-header__link::after" in css
    assert "rotateY(-52deg)" in css
    assert "rotateY(52deg)" in css
    assert ".br-site-header__link.is-active::before" in css
    assert ".br-site-header__link.is-active::after" in css
    assert '[data-section="about"]' in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css


def test_weekly_page_generator_cannot_restore_legacy_text_header() -> None:
    renderer = WEEKLY_RENDERER.read_text(encoding="utf-8")
    assert '<header id="site-header"></header>' in renderer
    assert "/assets/site-header.css" in renderer
    assert "/scripts/site-header.js" in renderer
    assert '<header class="top">' not in renderer


def test_current_weekly_pages_use_shared_header() -> None:
    pages = (
        ROOT / "pl" / "inwestycje" / "prognozy-tygodniowe.html",
        ROOT / "en" / "investing" / "weekly-forecasts.html",
    )
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert '<header id="site-header"></header>' in html
        assert "/assets/site-header.css" in html
        assert "/scripts/site-header.js" in html
        assert '<header class="top">' not in html
