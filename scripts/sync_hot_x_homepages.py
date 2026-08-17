#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from hot_x_items import is_direct_post, is_editorial_search, item_url

ROOT = Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "data" / "hot_tweets.json"
HOME_PATHS = {
    "pl": ROOT / "pl" / "index.html",
    "en": ROOT / "en" / "index.html",
}
INITIAL_VISIBLE = 4
MARKER_BLOCK = re.compile(
    r'<div\s+class=["\']source-feed["\'][^>]*>\s*'
    r'<!-- HOT_X_STATIC_START -->[\s\S]*?<!-- HOT_X_STATIC_END -->\s*</div>',
    re.I,
)
EMPTY_BLOCK = re.compile(r'<div\s+class=["\']source-feed["\']\s*>\s*</div>', re.I)
MARKETING_BLOCK = re.compile(
    r'\s*<!-- BR_MARKETING_START -->[\s\S]*?<!-- BR_MARKETING_END -->',
    re.I,
)
SHARE_BLOCK = re.compile(
    r'\s*<!-- BR_SHARE_START -->[\s\S]*?<!-- BR_SHARE_END -->',
    re.I,
)
MAIN_HEAD = re.compile(
    r'(<section\s+class=["\']main-head["\'][^>]*>)([\s\S]*?)(</section>)',
    re.I,
)
TITLE_TAG = re.compile(r'<title>[\s\S]*?</title>', re.I)
DESCRIPTION_TAG = re.compile(r'<meta\s+name=["\']description["\'][^>]*>', re.I)

PAGE_META = {
    "pl": {
        "title": "BriefRooms — najważniejsze informacje bez szumu",
        "description": (
            "BriefRooms: krótkie, aktualne briefy o AI, inwestycjach, geopolityce, nauce i zdrowiu — "
            "zawsze z linkami do źródeł. Sprawdź szybko, co naprawdę warto przeczytać."
        ),
        "canonical": "https://briefrooms.com/pl/",
        "locale": "pl_PL",
        "locale_alt": "en_US",
        "share_title": "Podaj dalej BriefRooms",
        "share_text": "Krótkie briefy, konkretne źródła i mniej informacyjnego szumu.",
        "share_aria": "Udostępnij BriefRooms",
        "x_text": "BriefRooms — krótkie briefy z linkami do źródeł. Sprawdź, co dziś warto przeczytać.",
    },
    "en": {
        "title": "BriefRooms — essential news without the noise",
        "description": (
            "BriefRooms delivers concise, current briefs on AI, investing, geopolitics, science and health — "
            "always linked to original sources. See what is worth reading now."
        ),
        "canonical": "https://briefrooms.com/en/",
        "locale": "en_US",
        "locale_alt": "pl_PL",
        "share_title": "Share BriefRooms",
        "share_text": "Concise briefs, primary sources and less information noise.",
        "share_aria": "Share BriefRooms",
        "x_text": "BriefRooms — concise briefs with links to original sources. See what is worth reading today.",
    },
}


def load_feed() -> dict:
    data = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list) or len(items) < INITIAL_VISIBLE:
        raise RuntimeError("Hot X feed must contain at least four items")
    return data


def text(item: dict, field: str, lang: str) -> str:
    return str(item.get(f"{field}_{lang}") or "").strip()


def link_meta(item: dict, lang: str) -> tuple[str, str, str, str]:
    url = item_url(item)
    if not url:
        return "", "", "", ""
    if is_direct_post(item.get("tweet_url")):
        return (
            url,
            "Konkretny post" if lang == "pl" else "Specific post",
            "Otwórz post na X →" if lang == "pl" else "Open post on X →",
            "",
        )
    if is_editorial_search(item):
        return (
            url,
            "Temat na X" if lang == "pl" else "X topic search",
            "Zobacz dyskusję na X →" if lang == "pl" else "View the discussion on X →",
            " hot-x-editorial-topic",
        )
    return "", "", "", ""


def card_html(item: dict, lang: str, index: int) -> str:
    url, post_label, cta, editorial_class = link_meta(item, lang)
    title = text(item, "title", lang)
    label = text(item, "label", lang) or "X"
    comment = text(item, "comment", lang) or text(item, "summary", lang)
    image = str(item.get("image") or "").strip()
    if not url or not title or len(comment) < 40:
        raise RuntimeError(f"Invalid Hot X item at position {index + 1}")
    extra = " hot-x-extra" if index >= INITIAL_VISIBLE else ""
    comment_label = "Komentarz" if lang == "pl" else "Comment"
    image_html = ""
    if image:
        image_html = (
            '<div class="tweet-img">'
            f'<img src="{html.escape(image, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            "</div>"
        )
    return (
        f'<article class="source-card hot-tweet hot-x-card{extra}{editorial_class}">'
        f'<a class="hot-x-card-link" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer external">'
        f'{image_html}<div class="hot-x-badges">'
        f'<span class="tweet-kicker">{html.escape(label)}</span>'
        f'<span class="hot-x-link-type">{post_label}</span>'
        f'</div><h3>{html.escape(title)}</h3></a>'
        f'<p class="hot-x-mode">{comment_label}</p>'
        f'<p class="hot-x-text">{html.escape(comment)}</p>'
        f'<a class="hot-x-source" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer external">{cta}</a>'
        "</article>"
    )


def static_block(data: dict, lang: str) -> str:
    updated_at = str(data.get("updated_at") or "").strip()
    items = data["items"][:10]
    cards = "\n".join(card_html(item, lang, index) for index, item in enumerate(items))
    more = "Więcej z X" if lang == "pl" else "More from X"
    button = ""
    if len(items) > INITIAL_VISIBLE:
        button = (
            '\n<div class="hot-x-more-wrap">'
            f'<button type="button" class="hot-x-more" aria-expanded="false">{more}</button>'
            "</div>"
        )
    return (
        f'<div class="source-feed" data-hot-x-static-updated-at="{html.escape(updated_at, quote=True)}" '
        f'data-hot-x-count="{len(items)}">\n'
        '<!-- HOT_X_STATIC_START -->\n'
        f'{cards}{button}\n'
        '<!-- HOT_X_STATIC_END -->\n'
        '</div>'
    )


def tracked_home_url(lang: str, source: str) -> str:
    base = PAGE_META[lang]["canonical"]
    return base + "?" + urllib.parse.urlencode(
        {
            "utm_source": source,
            "utm_medium": "social",
            "utm_campaign": "homepage_share",
        }
    )


def share_url(lang: str, network: str) -> str:
    if network == "x":
        return "https://twitter.com/intent/tweet?" + urllib.parse.urlencode(
            {"text": PAGE_META[lang]["x_text"], "url": tracked_home_url(lang, "x")}
        )
    if network == "linkedin":
        return "https://www.linkedin.com/sharing/share-offsite/?" + urllib.parse.urlencode(
            {"url": tracked_home_url(lang, "linkedin")}
        )
    if network == "facebook":
        return "https://www.facebook.com/sharer/sharer.php?" + urllib.parse.urlencode(
            {"u": tracked_home_url(lang, "facebook")}
        )
    raise ValueError(f"Unsupported network: {network}")


def marketing_head_block(lang: str) -> str:
    meta = PAGE_META[lang]
    canonical = meta["canonical"]
    schema = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "BriefRooms",
            "url": "https://briefrooms.com/",
            "inLanguage": ["pl", "en"],
            "description": meta["description"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join(
        [
            "<!-- BR_MARKETING_START -->",
            f'<link rel="canonical" href="{html.escape(canonical, quote=True)}">',
            '<link rel="alternate" hreflang="pl" href="https://briefrooms.com/pl/">',
            '<link rel="alternate" hreflang="en" href="https://briefrooms.com/en/">',
            '<link rel="alternate" hreflang="x-default" href="https://briefrooms.com/">',
            '<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">',
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="BriefRooms">',
            f'<meta property="og:title" content="{html.escape(meta["title"], quote=True)}">',
            f'<meta property="og:description" content="{html.escape(meta["description"], quote=True)}">',
            f'<meta property="og:url" content="{html.escape(canonical, quote=True)}">',
            f'<meta property="og:locale" content="{meta["locale"]}">',
            f'<meta property="og:locale:alternate" content="{meta["locale_alt"]}">',
            '<meta name="twitter:card" content="summary">',
            f'<meta name="twitter:title" content="{html.escape(meta["title"], quote=True)}">',
            f'<meta name="twitter:description" content="{html.escape(meta["description"], quote=True)}">',
            f'<script type="application/ld+json">{schema}</script>',
            "<style>",
            ".br-share-strip{margin-top:18px;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 15px;border:1px solid rgba(56,214,201,.22);border-radius:16px;background:linear-gradient(135deg,rgba(56,214,201,.09),rgba(127,200,255,.05));box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}",
            ".br-share-copy{display:flex;flex-direction:column;gap:3px;min-width:0}.br-share-copy strong{font-size:13px;color:#eaf7ff}.br-share-copy span{font-size:11px;line-height:1.35;color:#9fb2c8}",
            ".br-share-actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.br-share-btn{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:0 11px;border:1px solid rgba(255,255,255,.14);border-radius:999px;background:rgba(255,255,255,.055);color:#dffcff;font-size:11px;font-weight:900;transition:transform .18s ease,border-color .18s ease,background .18s ease}.br-share-btn:hover{transform:translateY(-1px);border-color:rgba(56,214,201,.42);background:rgba(56,214,201,.12);color:#fff}",
            "@media(max-width:680px){.br-share-strip{align-items:flex-start;flex-direction:column}.br-share-actions{width:100%}.br-share-btn{flex:1}}",
            "</style>",
            "<!-- BR_MARKETING_END -->",
        ]
    )


def share_block(lang: str) -> str:
    meta = PAGE_META[lang]
    buttons = []
    for network, label in (("x", "X"), ("linkedin", "LinkedIn"), ("facebook", "Facebook")):
        buttons.append(
            f'<a class="br-share-btn" data-share-network="{network}" '
            f'href="{html.escape(share_url(lang, network), quote=True)}" '
            'target="_blank" rel="noopener noreferrer external">'
            f'{label}</a>'
        )
    return (
        "<!-- BR_SHARE_START -->\n"
        f'<div class="br-share-strip" aria-label="{html.escape(meta["share_aria"], quote=True)}">'
        '<div class="br-share-copy">'
        f'<strong>{html.escape(meta["share_title"])}</strong>'
        f'<span>{html.escape(meta["share_text"])}</span>'
        '</div><div class="br-share-actions">'
        + "".join(buttons)
        + "</div></div>\n<!-- BR_SHARE_END -->"
    )


def patch_growth(source: str, lang: str) -> str:
    meta = PAGE_META[lang]
    updated = TITLE_TAG.sub(f'<title>{html.escape(meta["title"])}</title>', source, count=1)
    description = f'<meta name="description" content="{html.escape(meta["description"], quote=True)}">'
    if DESCRIPTION_TAG.search(updated):
        updated = DESCRIPTION_TAG.sub(description, updated, count=1)
    else:
        viewport = re.search(r'<meta\s+name=["\']viewport["\'][^>]*>', updated, re.I)
        if not viewport:
            raise RuntimeError("Could not locate viewport meta tag")
        updated = updated[: viewport.end()] + "\n" + description + updated[viewport.end() :]

    marketing = marketing_head_block(lang)
    if MARKETING_BLOCK.search(updated):
        updated = MARKETING_BLOCK.sub(lambda _: "\n" + marketing, updated, count=1)
    else:
        desc_match = DESCRIPTION_TAG.search(updated)
        if not desc_match:
            raise RuntimeError("Could not locate description meta tag")
        updated = updated[: desc_match.end()] + "\n" + marketing + updated[desc_match.end() :]

    sharing = share_block(lang)
    if SHARE_BLOCK.search(updated):
        updated = SHARE_BLOCK.sub(lambda _: "\n" + sharing, updated, count=1)
    else:
        main_head = MAIN_HEAD.search(updated)
        if not main_head:
            raise RuntimeError("Could not locate homepage main-head section")
        replacement = main_head.group(1) + main_head.group(2) + "\n" + sharing + main_head.group(3)
        updated = updated[: main_head.start()] + replacement + updated[main_head.end() :]
    return updated


def patch_homepage(path: Path, lang: str, data: dict) -> bool:
    source = path.read_text(encoding="utf-8")
    replacement = static_block(data, lang)
    if MARKER_BLOCK.search(source):
        updated, count = MARKER_BLOCK.subn(replacement, source, count=1)
    else:
        updated, count = EMPTY_BLOCK.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"Could not locate one source-feed container in {path}")
    updated = patch_growth(updated, lang)
    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def check_files(data: dict) -> None:
    updated_at = str(data.get("updated_at") or "")
    expected_urls = [item_url(item) for item in data["items"][:10]]
    for lang, path in HOME_PATHS.items():
        source = path.read_text(encoding="utf-8")
        if f'data-hot-x-static-updated-at="{updated_at}"' not in source:
            raise RuntimeError(f"Missing current Hot X marker in {path}")
        if "<!-- HOT_X_STATIC_START -->" not in source or "<!-- HOT_X_STATIC_END -->" not in source:
            raise RuntimeError(f"Missing Hot X static markers in {path}")
        for url in expected_urls:
            escaped_url = html.escape(url or "", quote=True)
            if not url or escaped_url not in source:
                raise RuntimeError(f"Missing Hot X URL {url!r} in {path}")
        expected_title = text(data["items"][0], "title", lang)
        if expected_title not in source:
            raise RuntimeError(f"Missing first {lang} Hot X title in {path}")
        meta = PAGE_META[lang]
        required_growth_markers = (
            "<!-- BR_MARKETING_START -->",
            "<!-- BR_SHARE_START -->",
            f'<link rel="canonical" href="{meta["canonical"]}">',
            'utm_campaign%3Dhomepage_share',
            'data-share-network="x"',
            'data-share-network="linkedin"',
            'data-share-network="facebook"',
        )
        for marker in required_growth_markers:
            if marker not in source:
                raise RuntimeError(f"Missing homepage growth marker {marker!r} in {path}")


def verify_production(data: dict, base_url: str, status_path: Path) -> None:
    updated_at = str(data.get("updated_at") or "")
    first_url = item_url(data["items"][0])
    escaped_first_url = html.escape(first_url, quote=True)
    status = {
        "expected_updated_at": updated_at,
        "expected_first_url": first_url,
        "expected_marketing_layer": True,
        "status": "failed",
        "attempts": 0,
        "pages": {},
    }
    last_error = ""
    for attempt in range(1, 41):
        status["attempts"] = attempt
        pages = {}
        try:
            for lang, page in (("pl", "pl/"), ("en", "en/")):
                url = f"{base_url.rstrip('/')}/{page}?hotx={int(time.time())}-{attempt}"
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "BriefRooms-Hot-X-Homepage-Verifier/1.0",
                        "Cache-Control": "no-cache",
                    },
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = response.read().decode("utf-8", errors="replace")
                ok = (
                    f'data-hot-x-static-updated-at="{updated_at}"' in body
                    and escaped_first_url in body
                    and "<!-- HOT_X_STATIC_START -->" in body
                    and "<!-- BR_MARKETING_START -->" in body
                    and "<!-- BR_SHARE_START -->" in body
                    and 'utm_campaign%3Dhomepage_share' in body
                    and f'<link rel="canonical" href="{PAGE_META[lang]["canonical"]}">' in body
                )
                pages[lang] = {"url": url, "passed": ok, "marketing_layer": ok}
                if not ok:
                    raise RuntimeError(f"production page {lang} does not contain current Hot X and growth layer")
            status.update({"status": "passed", "pages": pages})
            last_error = ""
            break
        except Exception as exc:
            status["pages"] = pages
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(15)
    status["last_error"] = last_error
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if status["status"] != "passed":
        raise RuntimeError(last_error or "Hot X homepage production verification failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-production", action="store_true")
    parser.add_argument("--base-url", default="https://briefrooms.com")
    parser.add_argument(
        "--status-path",
        type=Path,
        default=ROOT / "data" / "hot_x_homepage_production_status.json",
    )
    args = parser.parse_args()
    data = load_feed()
    if args.verify_production:
        verify_production(data, args.base_url, args.status_path)
        return
    if args.check:
        check_files(data)
        return
    changed = []
    for lang, path in HOME_PATHS.items():
        if patch_homepage(path, lang, data):
            changed.append(lang)
    check_files(data)
    print("Hot X + homepage growth layer synchronized: " + (", ".join(changed) if changed else "already current"))


if __name__ == "__main__":
    main()
