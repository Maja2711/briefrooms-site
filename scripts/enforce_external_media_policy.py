#!/usr/bin/env python3
"""Enforce source-linked external images across generated BriefRooms pages.

The guard never downloads or stores publisher photographs. It validates URL
relationships, adds attribution/fallback markup and removes images whose host
cannot be linked to the source article or a configured publisher CDN.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from external_media_policy import external_image_url

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = '<script src="/scripts/external-media-guard.js?v=1" defer></script>'
STYLE = """<style id="br-external-media-policy-style">
.news-thumb,.thumb,.image{position:relative;overflow:hidden}
.media-fallback{position:absolute;inset:0;z-index:0;display:flex;flex-direction:column;justify-content:center;align-items:center;padding:10px;background:radial-gradient(circle at 25% 15%,rgba(56,214,201,.28),transparent 42%),linear-gradient(135deg,#0d344a,#081827);color:#dff7ff;text-align:center}
.media-fallback strong{font-size:18px;line-height:1}.media-fallback small{margin-top:5px;color:#8fa8bc;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.06em}
.news-thumb.has-image img,.thumb.has-image img,.image img[data-br-external-media]{position:relative;z-index:1;width:100%;height:100%;display:block;object-fit:cover}
.media-source-badge{position:absolute;z-index:3;left:8px;bottom:7px;max-width:calc(100% - 16px);padding:4px 7px;border:1px solid rgba(255,255,255,.22);border-radius:8px;background:rgba(3,16,28,.78);color:#e6f8ff;font-size:9px;font-weight:850;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.image .media-source-badge{left:14px;bottom:13px;padding:5px 9px;font-size:11px}.media-fallback-active img{display:none!important}
</style>"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_if_changed(path: Path, value: str) -> bool:
    old = read(path)
    if old == value:
        return False
    path.write_text(value, encoding="utf-8", newline="\n")
    print(f"updated {path.relative_to(ROOT)}")
    return True


def ensure_assets(value: str) -> str:
    if 'id="br-external-media-policy-style"' not in value and "</head>" in value:
        value = value.replace("</head>", STYLE + "\n</head>", 1)
    if "/scripts/external-media-guard.js" not in value and "</body>" in value:
        value = value.replace("</body>", RUNTIME + "\n</body>", 1)
    return value


def clean_rel(tag: str) -> str:
    if not re.search(r'\btarget=["\']_blank["\']', tag, re.I):
        return tag
    required = "noopener noreferrer external"
    if re.search(r'\brel=["\'][^"\']*["\']', tag, re.I):
        return re.sub(r'\brel=["\'][^"\']*["\']', f'rel="{required}"', tag, count=1, flags=re.I)
    return tag[:-1] + f' rel="{required}">' if tag.endswith(">") else tag


def media_img(src: str, source_url: str, extra: str = "") -> str:
    return (
        f'<img src="{html.escape(src, quote=True)}" alt="" loading="lazy" decoding="async" '
        f'referrerpolicy="no-referrer" data-br-external-media="source-linked" '
        f'data-br-source-url="{html.escape(source_url, quote=True)}"{extra}>'
    )


def fallback_markup(lang: str, label: str = "BRs") -> str:
    sub = "źródło" if lang == "pl" else "source"
    return (
        '<span class="media-fallback" aria-hidden="true">'
        f'<strong>{html.escape(label)}</strong><small>{sub}</small></span>'
    )


def preview_label(lang: str) -> str:
    return "Podgląd źródła" if lang == "pl" else "Source preview"


def sanitize_home_json(path: Path) -> dict[str, dict]:
    payload = json.loads(read(path))
    mapping: dict[str, dict] = {}
    changed = False
    for section in ("latest", "radar"):
        items = payload.get(section) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("link") or "")
            approved = external_image_url(item.get("image"), source)
            current = str(item.get("image") or "")
            if approved != current:
                item["image"] = approved
                changed = True
            status = "source-linked-external" if approved else "fallback"
            if item.get("image_policy") != status:
                item["image_policy"] = status
                changed = True
            permalink = str(item.get("permalink") or "")
            if permalink:
                mapping[permalink] = item
    if changed:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"updated {path.relative_to(ROOT)}")
    return mapping


def sanitize_home_page(path: Path, mapping: dict[str, dict], lang: str) -> None:
    value = read(path)

    def card(match: re.Match[str]) -> str:
        opening, href, body, closing = match.group(1), match.group(2), match.group(3), match.group(4)
        item = mapping.get(html.unescape(href))
        if not item:
            return match.group(0)
        source_url = str(item.get("link") or "")
        approved = external_image_url(item.get("image"), source_url)
        source = str(item.get("source") or preview_label(lang))
        pattern = re.compile(
            r'<div class="thumb(?: has-image| media-fallback-active)?">'
            r'(?P<fallback><div class="fallback-art".*?</div>)'
            r'(?:<img[^>]*>)?(?:<span class="media-source-badge">.*?</span>)?</div>',
            re.S,
        )
        inner = pattern.search(body)
        if not inner:
            return match.group(0)
        if approved:
            replacement = (
                '<div class="thumb has-image">' + inner.group("fallback")
                + media_img(approved, source_url)
                + f'<span class="media-source-badge">{html.escape(preview_label(lang))}: {html.escape(source)}</span></div>'
            )
        else:
            replacement = '<div class="thumb media-fallback-active">' + inner.group("fallback") + "</div>"
        body = body[: inner.start()] + replacement + body[inner.end() :]
        return opening + body + closing

    card_re = re.compile(
        r'(<a class="brief-card" href="([^"]+)">)(.*?)(</a>)',
        re.S,
    )
    value = card_re.sub(card, value)
    value = ensure_assets(value)
    value = re.sub(r'<a\b[^>]*target=["\']_blank["\'][^>]*>', lambda m: clean_rel(m.group(0)), value, flags=re.I)
    write_if_changed(path, value)


def sanitize_news_page(path: Path, lang: str) -> None:
    value = read(path)

    def anchor(match: re.Match[str]) -> str:
        tag, source_url, body = match.group(1), html.unescape(match.group(2)), match.group(3)
        image_match = re.search(r'<span class="news-thumb has-image">.*?<img[^>]+src="([^"]+)"[^>]*>.*?</span>', body, re.S)
        if not image_match:
            return clean_rel(tag) + body + "</a>"
        approved = external_image_url(html.unescape(image_match.group(1)), source_url)
        if approved:
            wrapper = (
                '<span class="news-thumb has-image">' + fallback_markup(lang)
                + media_img(approved, source_url, ' width="78" height="54"')
                + f'<span class="media-source-badge">{preview_label(lang)}</span></span>'
            )
        else:
            wrapper = '<span class="news-thumb media-fallback-active">' + fallback_markup(lang) + "</span>"
        body = body[: image_match.start()] + wrapper + body[image_match.end() :]
        return clean_rel(tag) + body + "</a>"

    pattern = re.compile(
        r'(<a\s+class="news-main-link"\s+href="([^"]+)"[^>]*>)(.*?)</a>',
        re.S | re.I,
    )
    value = pattern.sub(anchor, value)
    value = ensure_assets(value)
    write_if_changed(path, value)


def sanitize_brief_page(path: Path, lang: str) -> None:
    value = read(path)
    source_match = re.search(r'<a class="btn primary" href="([^"]+)"[^>]*>', value, re.I)
    image_match = re.search(r'<div class="image"><img src="([^"]+)"[^>]*>', value, re.I)
    if not source_match or not image_match:
        return
    source_url = html.unescape(source_match.group(1))
    old_image = html.unescape(image_match.group(1))
    approved = external_image_url(old_image, source_url)
    if approved:
        replacement = media_img(approved, source_url)
        badge = f'<span class="media-source-badge">{preview_label(lang)}</span>'
    else:
        approved = "https://briefrooms.com/assets/og-cover.jpg"
        replacement = '<img src="/assets/og-cover.jpg" alt="" loading="lazy" decoding="async">'
        badge = ""
    value = value.replace(old_image, approved)
    value = re.sub(r'<img src="[^"]+"[^>]*>', replacement, value, count=1, flags=re.I)
    value = re.sub(r'(<div class="image">.*?)(?:<span class="media-source-badge">.*?</span>)?(</div>)', rf'\1{badge}\2', value, count=1, flags=re.S)
    value = re.sub(r'<a\b[^>]*target=["\']_blank["\'][^>]*>', lambda m: clean_rel(m.group(0)), value, flags=re.I)
    value = ensure_assets(value)
    write_if_changed(path, value)


def main() -> None:
    pl_map = sanitize_home_json(ROOT / "pl" / "home_brief.json")
    en_map = sanitize_home_json(ROOT / "en" / "home_brief.json")
    sanitize_home_page(ROOT / "pl" / "index.html", pl_map, "pl")
    sanitize_home_page(ROOT / "en" / "index.html", en_map, "en")
    sanitize_news_page(ROOT / "pl" / "aktualnosci.html", "pl")
    sanitize_news_page(ROOT / "en" / "news.html", "en")
    for path in (ROOT / "pl" / "briefy").glob("*.html"):
        sanitize_brief_page(path, "pl")
    for path in (ROOT / "en" / "briefs").glob("*.html"):
        sanitize_brief_page(path, "en")


if __name__ == "__main__":
    main()
