from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from scripts import sync_site_header


ROOT = Path(__file__).resolve().parents[1]
VERSION = sync_site_header.VERSION
CSS_REFERENCE = f'/assets/site-header.css?v={VERSION}'
JS_REFERENCE = f'/scripts/site-header.js?v={VERSION}'


class AlternateLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {name.lower(): value or "" for name, value in attrs}
        if "alternate" in values.get("rel", "").lower().split() and values.get("hreflang"):
            self.links[values["hreflang"].lower()] = values.get("href", "")


def test_every_public_page_has_one_shared_header() -> None:
    pages = sync_site_header.public_pages()
    assert len(pages) == 45

    for page in pages:
        text = page.read_text(encoding="utf-8")
        relative = page.relative_to(ROOT)
        assert text.count(CSS_REFERENCE) == 1, relative
        assert text.count(JS_REFERENCE) == 1, relative
        assert len(re.findall(r'id=["\']site-header["\']', text, re.IGNORECASE)) == 1, relative
        assert re.search(
            r'<body\b[^>]*>\s*<header id=["\']site-header["\']></header>',
            text,
            re.IGNORECASE,
        ), relative
        head = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.IGNORECASE | re.DOTALL)
        assert head, relative
        assert CSS_REFERENCE in head.group(1), relative
        assert JS_REFERENCE in head.group(1), relative
        assert re.search(
            rf'<script[^>]+src=["\']{re.escape(JS_REFERENCE)}["\'][^>]*\bdefer\b',
            head.group(1),
            re.IGNORECASE,
        ), relative


def test_homepages_keep_the_original_door_navigation() -> None:
    for page in (ROOT / "pl" / "index.html", ROOT / "en" / "index.html"):
        text = page.read_text(encoding="utf-8")
        assert '<header class="top">' in text, page.relative_to(ROOT)
        assert text.count('class="nav-link ') == 6, page.relative_to(ROOT)
        assert '<span class="brand-mark">BRs</span>' in text, page.relative_to(ROOT)
        assert "/assets/site-header.css" not in text, page.relative_to(ROOT)
        assert "/scripts/site-header.js" not in text, page.relative_to(ROOT)
        assert 'id="site-header"' not in text, page.relative_to(ROOT)


def test_legacy_full_navigation_is_not_duplicated() -> None:
    for page in sync_site_header.public_pages():
        text = page.read_text(encoding="utf-8")
        for match in sync_site_header.HEADER_RE.finditer(text):
            block = match.group(0)
            opening = block[: block.find(">") + 1]
            class_match = re.search(r'class=["\']([^"\']*)["\']', opening, re.IGNORECASE)
            classes = set(class_match.group(1).split()) if class_match else set()
            is_legacy_nav = (
                "top" in classes
                and re.search(r"<nav\b", block, re.IGNORECASE)
                and re.search(r'href=["\']/(?:pl|en)/["\']', block, re.IGNORECASE)
            )
            assert not is_legacy_nav, page.relative_to(ROOT)


def test_room_hubs_use_header_navigation_without_obsolete_back_links() -> None:
    hubs = (
        ROOT / "pl" / "geopolityka.html",
        ROOT / "pl" / "zdrowie.html",
        ROOT / "pl" / "nauka.html",
        ROOT / "en" / "geopolitics.html",
        ROOT / "en" / "health.html",
        ROOT / "en" / "science.html",
    )
    obsolete_labels = (
        "Wróć na stronę wyboru pokoju",
        "Wróć na stronę wyboru pokoi",
        "Back to room selection",
    )
    for page in hubs:
        text = page.read_text(encoding="utf-8")
        assert '<header id="site-header"></header>' in text, page.relative_to(ROOT)
        assert not any(label in text for label in obsolete_labels), page.relative_to(ROOT)


def test_generated_briefs_and_redirect_templates_are_excluded() -> None:
    excluded = [
        ROOT / "pl" / "brief.html",
        ROOT / "en" / "brief.html",
        ROOT / "en" / "geo" / "topic.html",
        *(ROOT / "pl" / "briefy").glob("*.html"),
        *(ROOT / "en" / "briefs").glob("*.html"),
    ]
    assert excluded
    for page in excluded:
        text = page.read_text(encoding="utf-8")
        assert "/assets/site-header.css" not in text, page.relative_to(ROOT)
        assert "/scripts/site-header.js" not in text, page.relative_to(ROOT)
        assert not re.search(r'id=["\']site-header["\']', text, re.IGNORECASE), page.relative_to(ROOT)


def test_news_generators_preserve_the_shared_header() -> None:
    for generator in (ROOT / "scripts" / "fetch_news_pl.py", ROOT / "scripts" / "fetch_news_en.py"):
        text = generator.read_text(encoding="utf-8")
        assert CSS_REFERENCE in text, generator.name
        assert JS_REFERENCE in text, generator.name
        assert '<header id="site-header"></header>' in text, generator.name


def test_declared_language_counterparts_exist() -> None:
    for page in sync_site_header.public_pages():
        relative = page.relative_to(ROOT)
        language = relative.parts[0]
        target_language = "en" if language == "pl" else "pl"
        parser = AlternateLinkParser()
        parser.feed(page.read_text(encoding="utf-8"))
        href = parser.links.get(target_language)
        if not href:
            continue
        route = urlparse(href).path.lstrip("/")
        target = ROOT / route
        if route.endswith("/"):
            target /= "index.html"
        assert target.is_file(), f"{relative} points to missing counterpart {href}"


def test_shared_header_styles_cover_layout_and_accessibility() -> None:
    css = (ROOT / "assets" / "site-header.css").read_text(encoding="utf-8")
    assert "position: sticky" in css
    assert "min-height: 44px" in css
    assert "@media (max-width: 819px)" in css
    assert "#site-header.is-open .br-site-header__nav" in css
    assert "focus-visible" in css
    assert "scroll-padding-top" in css
    assert "padding: 0 !important" in css
    assert "background: rgba(5, 17, 29, 0.96) !important" in css
    assert "overflow-x: auto" not in css
    assert "#087f9a 0%, #23d5cc 38%, #78e7f7 70%, #d6fbff 100%" in css
    assert re.search(r"#site-header\s*\{[^}]*min-height:\s*var\(--br-site-header-height\)", css, re.DOTALL)


def test_sync_script_is_idempotent() -> None:
    for page in sync_site_header.public_pages():
        raw = page.read_bytes()
        newline = "\r\n" if b"\r\n" in raw else "\n"
        text = raw.decode("utf-8")
        assert sync_site_header.transform(text, newline) == text, page.relative_to(ROOT)
