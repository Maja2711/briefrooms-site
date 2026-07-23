#!/usr/bin/env python3
"""Apply the BriefRooms external-media policy to all content generators.

This patch is intentionally idempotent. It fails loudly when a known generator
changes shape, rather than silently leaving legal/technical safeguards inactive.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Patch target missing: {label}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    if replacement.strip() in text:
        return text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"Patch target missing or ambiguous: {label} ({count})")
    return updated


PL_IMAGE_BLOCK = r'''def entry_image(entry, article_url: str, feed_url: str) -> str:
    """Return an RSS image only when it belongs to the publisher host family."""
    for key in ("media_thumbnail", "media_content"):
        media = entry.get(key) or []
        if isinstance(media, dict):
            media = [media]
        for item in media:
            image_url = external_image_url((item or {}).get("url"), article_url, feed_url)
            if image_url:
                return image_url
    for enclosure in entry.get("enclosures") or []:
        image_url = (enclosure or {}).get("href") or (enclosure or {}).get("url")
        content_type = ((enclosure or {}).get("type") or "").lower()
        if image_url and content_type.startswith("image/"):
            approved = external_image_url(image_url, article_url, feed_url)
            if approved:
                return approved
    return ""


def article_image(link: str) -> str:
    """Read publisher metadata without downloading or storing the photograph."""
    try:
        response = requests.get(
            link,
            headers={"User-Agent": "BriefRoomsBot/2.2 (+https://briefrooms.com)"},
            timeout=7,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return ""
        page = response.text[:350000]
    except Exception:
        return ""

    patterns = (
        r'<meta[^>]+(?:property|name)=["\\'](?:og:image|twitter:image)["\\'][^>]+content=["\\']([^"\\']+)',
        r'<meta[^>]+content=["\\']([^"\\']+)["\\'][^>]+(?:property|name)=["\\'](?:og:image|twitter:image)["\\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            image_url = external_image_url(match.group(1), link, link)
            if image_url:
                return image_url
    return ""

'''

EN_IMAGE_BLOCK = r'''def _valid_image_url(value: str, base_url: str = "", source_url: str = "") -> str:
    return external_image_url(value, source_url or base_url, base_url)


def entry_image(entry, article_url: str, feed_url: str) -> str:
    """Return the best RSS image linked to the article publisher."""
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        media_items = entry.get(key, []) or []
        if isinstance(media_items, dict):
            media_items = [media_items]
        for media in media_items:
            if not isinstance(media, dict):
                continue
            url = _valid_image_url(media.get("url") or media.get("href"), feed_url, article_url)
            if not url:
                continue
            try:
                width = int(media.get("width") or 0)
            except (TypeError, ValueError):
                width = 0
            candidates.append((width, url))
    for enclosure in (entry.get("enclosures", []) or []) + (entry.get("links", []) or []):
        if not isinstance(enclosure, dict):
            continue
        media_type = str(enclosure.get("type") or "").lower()
        rel = str(enclosure.get("rel") or "").lower()
        if media_type.startswith("image/") or rel == "enclosure":
            url = _valid_image_url(enclosure.get("href") or enclosure.get("url"), feed_url, article_url)
            if url:
                candidates.append((0, url))
    raw_html = str(entry.get("summary") or entry.get("description") or "")
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw_html, re.I)
    if match:
        url = _valid_image_url(match.group(1), feed_url, article_url)
        if url:
            candidates.append((0, url))
    return max(candidates, default=(0, ""), key=lambda item: item[0])[1]


def article_image(link: str) -> str:
    """Use publisher metadata without copying the image into BriefRooms."""
    if not str(link or "").lower().startswith(("http://", "https://")):
        return ""
    try:
        response = requests.get(
            link,
            headers={
                "User-Agent": "BriefRoomsBot/2.2 (+https://briefrooms.com)",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=6,
            allow_redirects=True,
        )
        if not response.ok:
            return ""
        page = decode_http_response(response)[:600000]
    except Exception:
        return ""
    patterns = (
        r'''<meta[^>]+(?:property|name)=["']og:image(?::secure_url)?["'][^>]+content=["']([^"']+)''',
        r'''<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']og:image(?::secure_url)?["']''',
        r'''<meta[^>]+(?:property|name)=["']twitter:image(?::src)?["'][^>]+content=["']([^"']+)''',
        r'''<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["']twitter:image(?::src)?["']''',
        r'''<link[^>]+rel=["']image_src["'][^>]+href=["']([^"']+)''',
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            image_url = _valid_image_url(match.group(1), link, link)
            if image_url:
                return image_url
    return ""


def image_is_fetchable(value: str, source_url: str = "") -> bool:
    return policy_image_is_fetchable(value, source_url)

'''


def patch_pl() -> None:
    path = "scripts/fetch_news_pl.py"
    text = read(path)
    text = replace_once(
        text,
        "from news_story_dedupe import same_story\n",
        "from news_story_dedupe import same_story\nfrom external_media_policy import external_image_url, image_is_fetchable, source_preview_label\n",
        "PL media import",
    )
    text = regex_once(
        text,
        r"def entry_image\(entry, source_url: str\) -> str:.*?(?=def fetch_section\()",
        PL_IMAGE_BLOCK,
        "PL image helpers",
    )
    text = replace_once(text, '"thumbnail_url": entry_image(e, f_url),', '"thumbnail_url": entry_image(e, link, f_url),', "PL RSS image call")
    text = regex_once(
        text,
        r'    if section_key in \("polityka", "biznes"\):\n        for item in picked:\n            if not item\.get\("thumbnail_url"\):\n                item\["thumbnail_url"\] = article_image\(item\.get\("link", ""\)\)\n',
        '''    for item in picked:
        source_url = item.get("link", "")
        image_url = external_image_url(item.get("thumbnail_url", ""), source_url)
        if image_url and not image_is_fetchable(image_url, source_url):
            image_url = ""
        if not image_url:
            image_url = article_image(source_url)
        item["thumbnail_url"] = image_url if image_url and image_is_fetchable(image_url, source_url) else ""
''',
        "PL picked-image validation",
    )
    text = replace_once(
        text,
        '    .news-thumb.has-image img{ width:100%; height:100%; display:block; object-fit:cover; }',
        '''    .news-thumb{position:relative;overflow:hidden}
    .news-thumb.has-image img{position:relative;z-index:1;width:100%;height:100%;display:block;object-fit:cover}
    .media-fallback{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:8px 10px;background:linear-gradient(135deg,#0d344a,#081827)}
    .media-source-badge{position:absolute;z-index:2;left:7px;bottom:6px;max-width:calc(100% - 14px);padding:3px 6px;border:1px solid rgba(255,255,255,.22);border-radius:7px;background:rgba(3,16,28,.76);color:#dff7ff;font-size:8px;font-weight:800;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}''',
        "PL media CSS",
    )
    pl_badge = '''    def badge(it, source: str):
        thumbnail_url = external_image_url(it.get("thumbnail_url", ""), it.get("link", ""))
        fallback = (
            '<span class="media-fallback" aria-hidden="true">'
            '<span class="dot"></span>'
            f'<span class="title">{esc(source_badge_for(source))}</span>'
            '<span class="sub">źródło</span></span>'
        )
        if thumbnail_url:
            return (
                '<span class="news-thumb has-image">' + fallback
                + f'<img src="{esc(thumbnail_url)}" alt="" loading="lazy" decoding="async" '
                + f'referrerpolicy="no-referrer" width="78" height="54" data-br-external-media="source-linked" data-br-source-url="{esc(it.get("link", ""))}" />'
                + f'<span class="media-source-badge">{esc(source_preview_label(source, "pl"))}</span></span>'
            )
        return '<span class="news-thumb">' + fallback + '</span>'

'''
    text = regex_once(text, r"    def badge\(it, source: str\):.*?(?=    def make_li\(it\):)", pl_badge, "PL badge renderer")
    text = replace_once(text, 'rel="noopener">', 'rel="noopener noreferrer external">', "PL external-link rel")
    text = replace_once(
        text,
        '  <script src="/scripts/site-header.js?v=20260719-1" defer></script>',
        '  <script src="/scripts/site-header.js?v=20260719-1" defer></script>\n  <script src="/scripts/external-media-guard.js?v=1" defer></script>',
        "PL runtime guard",
    )
    write(path, text)


def patch_en() -> None:
    path = "scripts/fetch_news_en.py"
    text = read(path)
    text = replace_once(
        text,
        "from news_story_dedupe import same_story\n",
        "from news_story_dedupe import same_story\nfrom external_media_policy import external_image_url, image_is_fetchable as policy_image_is_fetchable, source_preview_label\n",
        "EN media import",
    )
    text = regex_once(
        text,
        r"def _valid_image_url\(value: str, base_url: str = \"\"\) -> str:.*?(?=# =========================\n# POBIERANIE \+ DEDUPE)",
        EN_IMAGE_BLOCK,
        "EN image helpers",
    )
    text = replace_once(text, '"thumbnail_url": entry_image(e, feed_url),', '"thumbnail_url": entry_image(e, link, feed_url),', "EN RSS image call")
    text = text.replace('image_is_fetchable(image_url)', 'image_is_fetchable(image_url, it.get("link", ""))')
    text = replace_once(
        text,
        '    .news-thumb.has-image img{width:100%;height:100%;display:block;object-fit:cover;}',
        '''    .news-thumb{position:relative;overflow:hidden}
    .news-thumb.has-image img{position:relative;z-index:1;width:100%;height:100%;display:block;object-fit:cover}
    .media-fallback{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:8px 10px;background:linear-gradient(135deg,#0d344a,#081827)}
    .media-source-badge{position:absolute;z-index:2;left:7px;bottom:6px;max-width:calc(100% - 14px);padding:3px 6px;border:1px solid rgba(255,255,255,.22);border-radius:7px;background:rgba(3,16,28,.76);color:#dff7ff;font-size:8px;font-weight:800;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}''',
        "EN media CSS",
    )
    en_badge = '''    def badge(it, source_label: str):
        image_url = external_image_url(it.get("thumbnail_url", ""), it.get("link", ""))
        source_text = esc(source_label or "Source")
        fallback = (
            '<span class="media-fallback" aria-hidden="true">'
            '<span class="dot"></span>'
            f'<span class="title">{source_text}</span>'
            '<span class="sub">Article</span></span>'
        )
        if image_url:
            return (
                '<span class="news-thumb has-image">' + fallback
                + f'<img src="{esc(image_url)}" alt="" loading="lazy" decoding="async" '
                + f'referrerpolicy="no-referrer" width="78" height="54" data-br-external-media="source-linked" data-br-source-url="{esc(it.get("link", ""))}" />'
                + f'<span class="media-source-badge">{esc(source_preview_label(source_label, "en"))}</span></span>'
            )
        return '<span class="news-thumb">' + fallback + '</span>'

'''
    text = regex_once(text, r"    def badge\(source_label: str, image_url: str = \"\"\):.*?(?=    def make_li\(it\):)", en_badge, "EN badge renderer")
    text = replace_once(text, '{badge(raw_source, it.get("thumbnail_url", ""))}', '{badge(it, raw_source)}', "EN badge call")
    text = replace_once(text, 'rel="noopener">', 'rel="noopener noreferrer external">', "EN external-link rel")
    text = replace_once(
        text,
        '  <script src="/scripts/site-header.js?v=20260719-1" defer></script>',
        '  <script src="/scripts/site-header.js?v=20260719-1" defer></script>\n  <script src="/scripts/external-media-guard.js?v=1" defer></script>',
        "EN runtime guard",
    )
    write(path, text)


def patch_permanent_briefs() -> None:
    path = "scripts/generate_permanent_briefs.py"
    text = read(path)
    text = replace_once(
        text,
        "from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit\n",
        "from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit\n\nfrom external_media_policy import external_image_url\n",
        "permanent-brief media import",
    )
    text = replace_once(text, 'image_url = safe_external_url(item.get("image"))', 'image_url = external_image_url(item.get("image"), item.get("link"))', "homepage image policy")
    text = replace_once(text, 'image = absolute_image_url(item.get("image"))', 'image = external_image_url(item.get("image"), source_url) or DEFAULT_IMAGE', "brief image policy")
    text = replace_once(
        text,
        "    image = record[\"image\"]\n",
        '''    image = record["image"]
    external_image = external_image_url(image, record["source_url"])
    media_attrs = (
        f' data-br-external-media="source-linked" data-br-source-url="{esc(record["source_url"])}"'
        if external_image else ""
    )
    preview_badge = (
        f'<span class="media-source-badge">{esc(cfg["source"])}: {esc(record["source"])}</span>'
        if external_image else ""
    )
''',
        "brief media attributes",
    )
    text = replace_once(
        text,
        "{BRIEF_STYLE}\n  </style>",
        "{BRIEF_STYLE}\n    .image{position:relative}.media-source-badge{position:absolute;z-index:3;left:14px;bottom:13px;max-width:calc(100% - 28px);padding:5px 9px;border:1px solid rgba(255,255,255,.22);border-radius:9px;background:rgba(3,16,28,.78);color:#e6f8ff;font-size:11px;font-weight:800}\n  </style>\n  <script src=\"/scripts/external-media-guard.js?v=1\" defer></script>",
        "brief media style/runtime",
    )
    text = replace_once(
        text,
        '<div class="image"><img src="{esc(image)}" alt="" referrerpolicy="no-referrer" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\'" /><div class="fallback" style="display:none">{esc(fallback_text)}</div></div>',
        '<div class="image"><img src="{esc(image)}" alt="" referrerpolicy="no-referrer"{media_attrs} onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\'" /><div class="fallback" style="display:none">{esc(fallback_text)}</div>{preview_badge}</div>',
        "brief image markup",
    )
    text = replace_once(text, 'rel="noopener">{esc(cfg[\'open_source\'])}', 'rel="noopener noreferrer external">{esc(cfg[\'open_source\'])}', "brief source rel")
    text = replace_once(
        text,
        "image = (\n            f'<img src=\"{html.escape(image_url, quote=True)}\" alt=\"\" loading=\"lazy\" '\n            'referrerpolicy=\"no-referrer\">'\n        )",
        "image = (\n            f'<img src=\"{html.escape(image_url, quote=True)}\" alt=\"\" loading=\"lazy\" '\n            f'referrerpolicy=\"no-referrer\" data-br-external-media=\"source-linked\" data-br-source-url=\"{html.escape(str(item.get(\"link\") or \"\"), quote=True)}\">'\n            f'<span class=\"media-source-badge\">{html.escape(cfg[\"source\"])}: {html.escape(source)}</span>'\n        )",
        "static homepage image markup",
    )
    write(path, text)


def patch_home_js() -> None:
    path = "scripts/home-briefs.js"
    text = read(path)
    safe_image = '''
  function safeImageUrl(value, sourceUrl) {
    if (typeof globalThis !== 'undefined' && globalThis.BriefRoomsMediaPolicy) {
      return globalThis.BriefRoomsMediaPolicy.safeImageUrl(value, sourceUrl);
    }
    return '';
  }
'''
    text = replace_once(text, "\n  function safePermalink(value, lang) {", safe_image + "\n  function safePermalink(value, lang) {", "homepage JS image helper")
    text = replace_once(text, 'var imageUrl = safeHttpUrl(item.image);', 'var imageUrl = safeImageUrl(item.image, item.link);', "homepage JS image policy")
    text = replace_once(
        text,
        "      image.referrerPolicy = 'no-referrer';\n",
        "      image.referrerPolicy = 'no-referrer';\n      image.dataset.brExternalMedia = 'source-linked';\n      image.dataset.brSourceUrl = String(item.link || '');\n",
        "homepage JS media attributes",
    )
    text = replace_once(
        text,
        "      thumb.appendChild(image);\n",
        "      thumb.appendChild(image);\n      thumb.appendChild(element(document, 'span', 'media-source-badge', cfg.source + ': ' + (item.source || cfg.source)));\n",
        "homepage JS source badge",
    )
    text = replace_once(text, "    safeHttpUrl: safeHttpUrl,\n", "    safeHttpUrl: safeHttpUrl,\n    safeImageUrl: safeImageUrl,\n", "homepage JS export")
    write(path, text)


def main() -> None:
    patch_pl()
    patch_en()
    patch_permanent_briefs()
    patch_home_js()
    print("External-media policy applied to PL/EN generators and homepage renderer.")


if __name__ == "__main__":
    main()
